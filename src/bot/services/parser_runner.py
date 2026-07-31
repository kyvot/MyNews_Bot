import asyncio
from datetime import datetime, timezone

from aiogram.enums import ChatType
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramUnauthorizedError,
)
from apscheduler.triggers.interval import IntervalTrigger

from bot.services.news_feed import ParsedNews, news_feed_publisher
from core.config import logger, stg
from core.scheduler import scheduler
from parser.service import collect_news


PARSER_JOB_ID = "news-parser:periodic"
_parser_enabled = False
_parser_generation = 0
_collection_tasks: dict[int, asyncio.Task] = {}


class ParserTargetError(RuntimeError):
    """The configured parser destination is unavailable or has a wrong type."""


async def validate_parser_target() -> bool:
    """Inspect GROUP_ID without treating getChat as send authority.

    ``getChat`` is only a metadata preflight. A ``chat not found`` response
    must not block a manually requested parser run; the actual publication
    call is the authoritative access check and already stops the cycle after
    its first permanent target error.
    """
    # Delayed import avoids the bot -> routers -> parser_runner import cycle.
    from bot import bot

    try:
        chat = await bot.get_chat(
            stg.GROUP_ID,
            request_timeout=stg.PARSER_TARGET_VALIDATION_TIMEOUT,
        )
    except TelegramBadRequest as error:
        logger.warning(
            "Telegram could not inspect the parser group during preflight "
            "(%s); starting anyway and deferring validation to publication",
            error,
        )
        return False
    except TelegramForbiddenError as error:
        raise ParserTargetError(
            "Telegram запретил доступ к группе парсера. Проверьте, что бот "
            "добавлен в группу и имеет необходимые права."
        ) from error
    except TelegramUnauthorizedError as error:
        raise ParserTargetError(
            "Telegram отклонил BOT_TOKEN. Проверьте токен бота."
        ) from error

    if chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        wrong_type = {
            ChatType.PRIVATE: "личный чат",
            ChatType.CHANNEL: "канал",
        }.get(chat.type, str(chat.type))
        raise ParserTargetError(
            "GROUP_ID должен указывать на группу или супергруппу Telegram, "
            f"а не на {wrong_type}."
        )
    return True


def start_parser() -> bool:
    global _parser_enabled, _parser_generation

    if scheduler.get_job(PARSER_JOB_ID) is not None:
        return False

    _parser_generation += 1
    generation = _parser_generation
    _parser_enabled = True
    try:
        scheduler.add_job(
            run_parser_cycle,
            trigger=IntervalTrigger(
                seconds=stg.PARSER_INTERVAL_SECONDS,
                timezone=stg.SCHEDULER_TIMEZONE,
            ),
            id=PARSER_JOB_ID,
            kwargs={"generation": generation},
            next_run_time=datetime.now(timezone.utc),
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
    except Exception:
        _parser_enabled = False
        raise
    logger.info("News parser started")
    return True


def stop_parser() -> bool:
    global _parser_enabled, _parser_generation

    job = scheduler.get_job(PARSER_JOB_ID)
    was_running = _parser_enabled or job is not None
    _parser_enabled = False
    _parser_generation += 1
    if job is not None:
        scheduler.remove_job(PARSER_JOB_ID)
    for task in tuple(_collection_tasks.values()):
        task.cancel()
    logger.info("News parser stopped")
    return was_running


async def run_parser_cycle(*, generation: int | None = None) -> None:
    generation = _parser_generation if generation is None else generation
    if not _cycle_is_active(generation):
        return

    collection_task = asyncio.create_task(
        collect_news(),
        name=f"news-collection:{generation}",
    )
    _collection_tasks[generation] = collection_task
    try:
        parsed = await collection_task
    except asyncio.CancelledError:
        if not _cycle_is_active(generation):
            logger.info("Parser collection cancelled")
            return
        raise
    finally:
        if _collection_tasks.get(generation) is collection_task:
            _collection_tasks.pop(generation, None)

    if not _cycle_is_active(generation):
        logger.info("Parser cycle stopped before publication")
        return

    parsed.sort(key=_published_timestamp, reverse=True)
    publication_limit = max(0, stg.PARSER_MAX_POSTS_PER_CYCLE)
    processed = 0
    published = 0

    for raw_item in parsed:
        if published >= publication_limit:
            break
        if not _cycle_is_active(generation):
            logger.info(
                "Parser cycle stopped after publishing %s item(s)",
                published,
            )
            return
        processed += 1
        try:
            item = ParsedNews.from_mapping(raw_item)
            was_published = await news_feed_publisher.publish(item)
            published += was_published
            if (
                was_published
                and _cycle_is_active(generation)
                and stg.PARSER_PUBLISH_DELAY_SECONDS > 0
            ):
                await asyncio.sleep(
                    stg.PARSER_PUBLISH_DELAY_SECONDS
                    * (1 + len(item.image_urls))
                )
        except (
            TelegramBadRequest,
            TelegramForbiddenError,
            TelegramUnauthorizedError,
        ) as error:
            if _is_parser_target_access_error(error):
                logger.error(
                    "News parser stopped because Telegram cannot access the "
                    "configured parser group. Verify GROUP_ID, bot "
                    "membership, and send-message permissions."
                )
                stop_parser()
                return
            logger.exception(
                "Failed to process parsed news item %r",
                raw_item.get("url"),
            )
        except Exception:
            logger.exception(
                "Failed to process parsed news item %r",
                raw_item.get("url"),
            )

    logger.info(
        "Parser cycle completed: parsed=%s, candidates=%s, published=%s",
        len(parsed),
        processed,
        published,
    )


def _cycle_is_active(generation: int) -> bool:
    return _parser_enabled and generation == _parser_generation


def _is_parser_target_access_error(error: Exception) -> bool:
    if isinstance(
        error,
        (TelegramForbiddenError, TelegramUnauthorizedError),
    ):
        return True
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "chat not found",
            "bot was kicked",
            "bot is not a member",
            "not enough rights to send",
            "have no rights to send",
        )
    )


def _published_timestamp(item: dict) -> float:
    published_at = item.get("published_at")
    if not isinstance(published_at, datetime):
        return 0.0
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return published_at.timestamp()
