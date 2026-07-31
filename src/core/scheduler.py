import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any

from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.config import logger, stg


scheduler = AsyncIOScheduler(
    timezone=stg.SCHEDULER_TIMEZONE,
    job_defaults={
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": stg.SCHEDULER_MISFIRE_GRACE_TIME,
    },
)


async def start_scheduler() -> None:
    """Start APScheduler and rebuild timers from PostgreSQL."""
    if not scheduler.running:
        scheduler.start(paused=True)
        try:
            from bot.services.scheduled_posts import (
                RECONCILE_JOB_ID,
                reconcile_scheduled_posts,
                restore_scheduled_posts,
            )

            await restore_scheduled_posts()
            scheduler.add_job(
                func=reconcile_scheduled_posts,
                trigger=IntervalTrigger(
                    seconds=stg.SCHEDULED_POST_RECONCILE_INTERVAL,
                    timezone=stg.SCHEDULER_TIMEZONE,
                ),
                id=RECONCILE_JOB_ID,
                replace_existing=True,
            )
        except Exception:
            scheduler.shutdown(wait=False)
            await asyncio.sleep(0)
            logger.exception("Failed to initialize the scheduler")
            raise

        scheduler.resume()
        if stg.PARSER_AUTOSTART:
            from aiogram.exceptions import TelegramAPIError

            from bot.services.parser_runner import (
                ParserTargetError,
                start_parser,
                validate_parser_target,
            )

            should_start_parser = True
            try:
                await validate_parser_target()
            except ParserTargetError as error:
                should_start_parser = False
                logger.error(
                    "Parser autostart skipped: %s",
                    error,
                )
            except (TelegramAPIError, TimeoutError) as error:
                logger.warning(
                    "Parser target preflight failed temporarily (%s); "
                    "autostart will continue and retry on scheduled cycles",
                    error,
                )
            except Exception:
                should_start_parser = False
                logger.exception(
                    "Parser autostart skipped because target validation "
                    "failed"
                )
            if should_start_parser:
                try:
                    start_parser()
                except Exception:
                    logger.exception("Parser autostart failed")
        logger.info(
            "Scheduler started (timezone=%s)",
            stg.SCHEDULER_TIMEZONE,
        )


async def stop_scheduler() -> None:
    """Pause new work, let active jobs finish, then close the scheduler."""
    if not scheduler.running:
        return

    scheduler.pause()
    from bot.services.parser_runner import stop_parser

    stop_parser()
    pending = _active_executor_futures()
    if pending:
        _, unfinished = await asyncio.wait(
            pending,
            timeout=stg.SCHEDULER_SHUTDOWN_GRACE_SECONDS,
        )
    else:
        unfinished = set()

    if unfinished:
        logger.warning(
            "Cancelling %s scheduler job(s) after shutdown grace period",
            len(unfinished),
        )

    scheduler.shutdown(wait=False)
    # AsyncIOScheduler schedules the actual shutdown onto its event loop.
    await asyncio.sleep(0)

    if unfinished:
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *unfinished,
                    return_exceptions=True,
                ),
                timeout=stg.SCHEDULER_SHUTDOWN_CANCEL_SECONDS,
            )
        except TimeoutError:
            logger.error(
                "Some scheduler jobs did not finish cancellation cleanup"
            )
    logger.info("Scheduler stopped")


def _active_executor_futures() -> set[asyncio.Future]:
    futures: set[asyncio.Future] = set()
    for executor in scheduler._executors.values():
        futures.update(
            future
            for future in getattr(executor, "_pending_futures", ())
            if not future.done()
        )
    return futures


def schedule_once(
    *,
    job_id: str,
    run_at: datetime,
    func: Callable[..., Any],
    kwargs: dict[str, Any] | None = None,
) -> Job:
    """Add or replace a one-off async-compatible job."""
    return scheduler.add_job(
        func=func,
        trigger=DateTrigger(
            run_date=run_at,
            timezone=stg.SCHEDULER_TIMEZONE,
        ),
        id=job_id,
        kwargs=kwargs or {},
        replace_existing=True,
    )
