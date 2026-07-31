import asyncio
from weakref import WeakValueDictionary

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai.errors import SummaryError
from ai.service import summary_service
from bot.news_ui import (
    NewsCallback,
    _limit_text,
    format_news_card,
    news_keyboard,
    normalize_source_label,
)
from bot.services.news_feedback import record_vote
from bot.services.parser_runner import (
    ParserTargetError,
    start_parser,
    stop_parser,
    validate_parser_target,
)
from core.config import logger, stg
from core.db.db import async_session
from core.db.models import Admins, NewsRaw
from utils.translate import translate_news, translate_text


newsrtr = Router(name="news")
_summary_locks: WeakValueDictionary[int, asyncio.Lock] = (
    WeakValueDictionary()
)
_ui_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


@newsrtr.callback_query(F.data == "start_parse")
async def start_parse_callback(
    callback: CallbackQuery,
    ses: AsyncSession,
):
    if not await _is_admin(ses, callback.from_user.id):
        await callback.answer(
            "Недостаточно прав",
            show_alert=True,
        )
        return
    try:
        await validate_parser_target()
    except ParserTargetError as error:
        await callback.answer(str(error), show_alert=True)
        return
    except (TelegramAPIError, TimeoutError):
        logger.exception("Could not validate the parser group")
        await callback.answer(
            "Не удалось проверить группу парсера. Проверьте Telegram и "
            "SOCKS5-прокси, затем повторите попытку.",
            show_alert=True,
        )
        return
    try:
        started = start_parser()
    except Exception:
        logger.exception("Could not start the news parser")
        await callback.answer(
            "Не удалось запустить парсер. Проверьте журнал приложения.",
            show_alert=True,
        )
        return
    await callback.answer(
        "Парсер запущен" if started else "Парсер уже работает",
        show_alert=True,
    )


@newsrtr.callback_query(F.data == "stop_parse")
async def stop_parse_callback(
    callback: CallbackQuery,
    ses: AsyncSession,
):
    if not await _is_admin(ses, callback.from_user.id):
        await callback.answer(
            "Недостаточно прав",
            show_alert=True,
        )
        return
    stopped = stop_parser()
    await callback.answer(
        "Парсер остановлен" if stopped else "Парсер уже остановлен",
        show_alert=True,
    )


@newsrtr.callback_query(NewsCallback.filter(F.action == "like"))
@newsrtr.callback_query(NewsCallback.filter(F.action == "dislike"))
async def vote_callback(
    callback: CallbackQuery,
    callback_data: NewsCallback,
    ses: AsyncSession,
):
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    if callback.message.chat.id != stg.GROUP_ID:
        await callback.answer(
            "Эта кнопка больше не активна",
            show_alert=True,
        )
        return

    ui_lock = _ui_locks.setdefault(
        callback_data.news_id,
        asyncio.Lock(),
    )
    async with ui_lock:
        result = await record_vote(
            ses,
            news_id=callback_data.news_id,
            user_id=callback.from_user.id,
            value=1 if callback_data.action == "like" else -1,
        )
        if result is None:
            await callback.answer("Новость не найдена", show_alert=True)
            return
        if result.status == "closed":
            await callback.answer("Голосование завершено", show_alert=True)
            return

        messages = {
            "recorded": "Голос учтён",
            "switched": "Голос изменён",
            "unchanged": "Вы уже так проголосовали",
        }
        await callback.answer(messages[result.status])
        try:
            is_showing_summary = (
                "Краткое содержание" in (callback.message.text or "")
            )
            await callback.message.edit_reply_markup(
                reply_markup=news_keyboard(
                    callback_data.news_id,
                    result.likes,
                    result.dislikes,
                    show_summary=is_showing_summary,
                )
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).casefold():
                logger.exception(
                    "Failed to refresh buttons for news %s",
                    callback_data.news_id,
                )
        except Exception:
            # The vote is already committed; a temporary Telegram edit
            # failure must not roll it back.
            logger.exception(
                "Failed to refresh buttons for news %s",
                callback_data.news_id,
            )


@newsrtr.callback_query(NewsCallback.filter(F.action == "more"))
async def more_callback(
    callback: CallbackQuery,
    callback_data: NewsCallback,
):
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    if callback.message.chat.id != stg.GROUP_ID:
        await callback.answer(
            "Эта кнопка больше не активна",
            show_alert=True,
        )
        return

    news = await _load_news_for_details(
        callback_data.news_id,
        callback.message.message_id,
    )
    if news is None:
        await callback.answer("Новость не найдена", show_alert=True)
        return

    await callback.answer()
    lock = _summary_locks.setdefault(news.id, asyncio.Lock())

    try:
        try:
            await callback.message.edit_text(
                text="<tg-emoji emoji-id='5927026418616636353'>🧠</tg-emoji> <b>Генерация краткого содержания…</b>",
                reply_markup=None,
                link_preview_options=LinkPreviewOptions(
                    is_disabled=True,
                ),
            )
        except TelegramBadRequest:
            pass

        async with lock:
            await _normalize_news_for_display(news)
            summary = await _get_cached_summary(news.id)
            cached_summary = summary is not None
            if summary is None:
                summary = await summary_service.summarize_url(
                    news.url,
                    title=news.title,
                    source=news.source,
                )
            normalized = await translate_text(
                summary,
                target_language=stg.AI_LANGUAGE,
                client=summary_service.deepl_client,
            )
            content_summary = normalized["translated_text"]
            summary_changed = content_summary != summary
            summary = content_summary
            if not cached_summary:
                await _save_summary(news.id, summary)
            elif summary_changed:
                await _save_summary(
                    news.id,
                    summary,
                    replace_existing=True,
                )

        ui_lock = _ui_locks.setdefault(news.id, asyncio.Lock())
        async with ui_lock:
            refreshed = await _get_news(news.id)
            if refreshed is not None:
                news = refreshed
            news.summary = summary

            await _safe_edit_or_send(
                callback,
                format_news_card(news, show_summary=True),
                news_keyboard(
                    news.id,
                    news.likes,
                    news.dislikes,
                    show_summary=True,
                ),
            )
    except SummaryError:
        logger.exception("Failed to summarize news %s", news.id)
        await _fallback_and_show(callback, news)
    except Exception:
        logger.exception("Unexpected details error for news %s", news.id)
        await _fallback_and_show(callback, news)


@newsrtr.callback_query(NewsCallback.filter(F.action == "original"))
async def original_callback(
    callback: CallbackQuery,
    callback_data: NewsCallback,
):
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    if callback.message.chat.id != stg.GROUP_ID:
        await callback.answer(
            "Эта кнопка больше не активна",
            show_alert=True,
        )
        return

    news = await _load_news_for_details(
        callback_data.news_id,
        callback.message.message_id,
    )
    if news is None:
        await callback.answer("Новость не найдена", show_alert=True)
        return

    await callback.answer()
    await _safe_edit_or_send(
        callback,
        format_news_card(news, show_summary=False),
        news_keyboard(
            news.id,
            news.likes,
            news.dislikes,
            show_summary=False,
        ),
    )


async def _safe_edit_or_send(
    callback: CallbackQuery,
    text: str,
    reply_markup,
) -> None:
    try:
        await callback.message.edit_text(
            text=text,
            reply_markup=reply_markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramBadRequest:
        await callback.message.reply(
            text=text,
            reply_markup=reply_markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )


async def _fallback_and_show(
    callback: CallbackQuery,
    news: NewsRaw,
) -> None:
    try:
        from ai.article import extract_article_text
        from ai.service import summary_service

        article_text = await extract_article_text(
            news.url, summary_service._article_client,
        )
        fallback = _limit_text(article_text, 2_500)
    except Exception:
        logger.debug("Trafilatura fallback failed for news %s", news.id)
        fallback = _limit_text(news.content, 2_500)

    news.summary = fallback
    await _safe_edit_or_send(
        callback,
        format_news_card(news, show_summary=True),
        news_keyboard(
            news.id,
            news.likes,
            news.dislikes,
            show_summary=True,
        ),
    )


async def _is_admin(session: AsyncSession, user_id: int) -> bool:
    return await session.scalar(
        select(Admins.user_id).where(Admins.user_id == user_id)
    ) is not None


async def _load_news_for_details(
    news_id: int,
    message_id: int,
) -> NewsRaw | None:
    async with async_session.begin() as session:
        news = await session.scalar(
            select(NewsRaw).where(
                NewsRaw.id == news_id,
                NewsRaw.telegram_msg_id == message_id,
            )
        )
        if news is not None:
            news.views += 1
        return news


async def _get_cached_summary(news_id: int) -> str | None:
    async with async_session() as session:
        return await session.scalar(
            select(NewsRaw.summary).where(NewsRaw.id == news_id)
        )


async def _normalize_news_for_display(news: NewsRaw) -> None:
    translation = await translate_news(
        news.title,
        news.content,
        client=summary_service.deepl_client,
        target_language=stg.AI_LANGUAGE,
        context_url=news.url,
    )
    normalized_source = normalize_source_label(news.source)
    if (
        not translation.changed
        and normalized_source == news.source
    ):
        return

    async with async_session.begin() as session:
        await session.execute(
            update(NewsRaw)
            .where(NewsRaw.id == news.id)
            .values(
                title=translation.title,
                content=translation.content,
                source=normalized_source,
            )
        )
    news.title = translation.title
    news.content = translation.content
    news.source = normalized_source


async def _get_news(news_id: int) -> NewsRaw | None:
    async with async_session() as session:
        return await session.get(NewsRaw, news_id)


async def _save_summary(
    news_id: int,
    summary: str,
    *,
    replace_existing: bool = False,
) -> None:
    async with async_session.begin() as session:
        statement = update(NewsRaw).where(NewsRaw.id == news_id)
        if not replace_existing:
            statement = statement.where(NewsRaw.summary.is_(None))
        await session.execute(statement.values(summary=summary))
