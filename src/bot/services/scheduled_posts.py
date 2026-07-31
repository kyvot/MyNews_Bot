from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import logger, stg
from core.db.db import async_session
from core.db.models import ScheduledPost


PENDING = "pending"
PROCESSING = "processing"
PUBLISHED = "published"
FAILED = "failed"
JOB_ID_PREFIX = "scheduled-post:"
RECONCILE_JOB_ID = "scheduled-posts:reconcile"


def job_id(post_id: int) -> str:
    return f"{JOB_ID_PREFIX}{post_id}"


def add_post_job(post_id: int, run_at: datetime) -> None:
    """Put a persisted post into APScheduler's in-memory timer queue."""
    from core.scheduler import schedule_once

    effective_run_at = max(
        _as_utc(run_at),
        datetime.now(timezone.utc),
    )
    schedule_once(
        job_id=job_id(post_id),
        run_at=effective_run_at,
        func=publish_scheduled_post,
        kwargs={"post_id": post_id},
    )


async def create_scheduled_post(
    *,
    session: AsyncSession,
    source_chat_id: int,
    source_message_id: int,
    source_message_ids: list[int] | None = None,
    target_chat_id: int,
    created_by_id: int,
    run_at: datetime,
) -> ScheduledPost:
    run_at = _as_utc(run_at)
    if run_at <= datetime.now(timezone.utc):
        raise ValueError("Время публикации должно быть в будущем")

    post = ScheduledPost(
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        source_message_ids=source_message_ids or [source_message_id],
        target_chat_id=target_chat_id,
        created_by_id=created_by_id,
        run_at=run_at,
        status=PENDING,
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)

    try:
        add_post_job(post.id, post.run_at)
    except Exception:
        # The database row is already durable. Reconciliation will add the
        # missing timer without asking the user to submit the post again.
        logger.exception("Failed to add timer for scheduled post %s", post.id)

    return post


async def restore_scheduled_posts() -> int:
    """Restore all durable pending posts after an application restart."""
    await _recover_stale_posts()

    async with async_session() as session:
        posts = (
            await session.scalars(
                select(ScheduledPost)
                .where(ScheduledPost.status == PENDING)
                .order_by(ScheduledPost.run_at)
            )
        ).all()

    for post in posts:
        add_post_job(post.id, post.run_at)

    logger.info("Restored %s scheduled post(s)", len(posts))
    return len(posts)


async def reconcile_scheduled_posts() -> None:
    """Restore timers missed between a database commit and add_job()."""
    from core.scheduler import scheduler

    await _recover_stale_posts()
    async with async_session() as session:
        posts = (
            await session.execute(
                select(ScheduledPost.id, ScheduledPost.run_at).where(
                    ScheduledPost.status == PENDING
                )
            )
        ).all()

    for post_id, run_at in posts:
        if scheduler.get_job(job_id(post_id)) is None:
            add_post_job(post_id, run_at)


async def publish_scheduled_post(post_id: int) -> None:
    """Forward a source message to the target channel."""
    claimed = await _claim_post(post_id)
    if claimed is None:
        return

    source_chat_id, source_message_ids, target_chat_id = claimed

    try:
        # Imported here to avoid a cycle while the bot routers are initialized.
        from bot import bot

        forwarded = await bot.forward_messages(
            chat_id=target_chat_id,
            from_chat_id=source_chat_id,
            message_ids=source_message_ids,
        )
        if not forwarded:
            raise RuntimeError("Telegram forwarded no source messages")
    except Exception as error:
        await _handle_publish_error(post_id, error)
        return

    now = datetime.now(timezone.utc)
    async with async_session.begin() as session:
        await session.execute(
            update(ScheduledPost)
            .where(
                ScheduledPost.id == post_id,
                ScheduledPost.status == PROCESSING,
            )
            .values(
                status=PUBLISHED,
                published_message_id=forwarded[0].message_id,
                published_at=now,
                locked_at=None,
                last_error=None,
                updated_at=now,
            )
        )

    logger.info(
        "Scheduled post %s published as Telegram message %s",
        post_id,
        forwarded[0].message_id,
    )


async def _claim_post(post_id: int) -> tuple[int, list[int], int] | None:
    """Atomically allow only one worker to publish a pending post."""
    now = datetime.now(timezone.utc)
    statement = (
        update(ScheduledPost)
        .where(
            ScheduledPost.id == post_id,
            ScheduledPost.status == PENDING,
        )
        .values(
            status=PROCESSING,
            attempts=ScheduledPost.attempts + 1,
            locked_at=now,
            updated_at=now,
        )
        .returning(
            ScheduledPost.source_chat_id,
            ScheduledPost.source_message_ids,
            ScheduledPost.target_chat_id,
        )
    )

    async with async_session.begin() as session:
        row = (await session.execute(statement)).one_or_none()

    if row is None:
        return None
    return tuple(row)


async def _handle_publish_error(post_id: int, error: Exception) -> None:
    now = datetime.now(timezone.utc)
    error_text = f"{type(error).__name__}: {error}"[:4000]
    retry_at: datetime | None = None

    async with async_session.begin() as session:
        post = await session.scalar(
            select(ScheduledPost)
            .where(ScheduledPost.id == post_id)
            .with_for_update()
        )
        if post is None or post.status != PROCESSING:
            return

        post.last_error = error_text
        post.locked_at = None
        post.updated_at = now
        if post.attempts < stg.SCHEDULED_POST_MAX_ATTEMPTS:
            retry_at = now + timedelta(
                seconds=stg.SCHEDULED_POST_RETRY_DELAY
            )
            post.status = PENDING
            post.run_at = retry_at
        else:
            post.status = FAILED

    if retry_at is not None:
        add_post_job(post_id, retry_at)
        logger.warning(
            "Scheduled post %s failed; retrying at %s: %s",
            post_id,
            retry_at.isoformat(),
            error_text,
        )
    else:
        logger.error(
            "Scheduled post %s failed permanently: %s",
            post_id,
            error_text,
        )


async def _recover_stale_posts() -> int:
    now = datetime.now(timezone.utc)
    lease_expired_at = now - timedelta(
        seconds=stg.SCHEDULED_POST_LEASE_TIMEOUT
    )
    async with async_session.begin() as session:
        result = await session.execute(
            update(ScheduledPost)
            .where(
                ScheduledPost.status == PROCESSING,
                ScheduledPost.locked_at < lease_expired_at,
            )
            .values(
                status=PENDING,
                locked_at=None,
                run_at=now,
                last_error="Recovered after an expired processing lease",
                updated_at=now,
            )
        )

    recovered = result.rowcount or 0
    if recovered:
        logger.warning(
            "Recovered %s scheduled post(s) with expired leases",
            recovered,
        )
    return recovered


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("run_at must be timezone-aware")
    return value.astimezone(timezone.utc)
