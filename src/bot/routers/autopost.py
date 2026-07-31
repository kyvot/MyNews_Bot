import asyncio
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.scheduled_posts import create_scheduled_post
from bot.states import AutopostST
from core.config import logger, stg
from core.db.db import async_session
from utils.calendar import get_calendar
from utils.inline import delete_inline


autrtr = Router(name="autrtr")
_ALBUM_COLLECTION_DELAY = 0.75
_pending_albums: dict[tuple[int, str], list[Message]] = {}
_album_tasks: dict[tuple[int, str], asyncio.Task[None]] = {}


@autrtr.callback_query(F.data == "autopost")
async def autopost(clck: CallbackQuery, state: FSMContext):
    await delete_inline(state=state, obj=clck)
    await state.clear()
    await clck.message.edit_text(
        text="<b>Выберите дату публикации:</b>",
        reply_markup=get_calendar(base_clck=autrtr.name),
    )
    await clck.answer()


@autrtr.callback_query(F.data.startswith(f"{autrtr.name}_cldt:"))
async def calendar_date(callback: CallbackQuery, state: FSMContext):
    _, year, month, day = callback.data.split(":")
    selected_date = date(
        year=int(year),
        month=int(month),
        day=int(day),
    )
    local_today = datetime.now(ZoneInfo(stg.SCHEDULER_TIMEZONE)).date()
    if selected_date < local_today:
        await callback.answer(
            "Дата публикации не может быть в прошлом",
            show_alert=True,
        )
        return

    kb = InlineKeyboardBuilder()
    kb.button(
        text="Указать время",
        icon_custom_emoji_id="6048701411888206453",
        callback_data=f"{autrtr.name}_stime",
    )
    kb.button(
        text="Назад",
        icon_custom_emoji_id="5256247952564825322",
        callback_data="autopost",
    )
    kb.adjust(1)

    await callback.message.edit_text(
        f"<tg-emoji emoji-id='5958356435514431314'>👌</tg-emoji> "
        f"<b>Дата публикации:</b> <i>{selected_date:%d.%m.%Y}</i>",
        reply_markup=kb.as_markup(),
    )
    # RedisStorage uses JSON, so FSM values must stay JSON serializable.
    await state.update_data(autopost_date=selected_date.isoformat())
    await callback.answer()


@autrtr.callback_query(F.data.startswith(f"{autrtr.name}_tmm:"))
async def set_minutes(clck: CallbackQuery, state: FSMContext):
    _, minutes_value = clck.data.split(":")
    minutes = int(minutes_value)
    await state.update_data(**{f"{autrtr.name}_sm": minutes})

    hours = await state.get_value(f"{autrtr.name}_sh")
    date_value = await state.get_value("autopost_date")
    if hours is None or date_value is None:
        await clck.answer(
            "Выберите дату и время заново",
            show_alert=True,
        )
        return

    selected_date = date.fromisoformat(date_value)
    local_run_at = datetime(
        selected_date.year,
        selected_date.month,
        selected_date.day,
        hours,
        minutes,
        tzinfo=ZoneInfo(stg.SCHEDULER_TIMEZONE),
    )
    run_at = local_run_at.astimezone(timezone.utc)
    if run_at <= datetime.now(timezone.utc):
        await clck.answer(
            "Это время уже прошло. Выберите будущее время.",
            show_alert=True,
        )
        return

    kb = InlineKeyboardBuilder()
    kb.button(
        text="Готово",
        icon_custom_emoji_id="5382026293166489702",
        callback_data="setup_post",
    )
    kb.button(
        text="Назад",
        icon_custom_emoji_id="5256247952564825322",
        callback_data="autopost",
    )
    kb.adjust(1)

    await clck.message.edit_text(
        text=(
            f"<tg-emoji emoji-id='5413879192267805083'>⭐️</tg-emoji> "
            f"<b>Публикация запланирована на "
            f"{selected_date:%d.%m.%Y} в {hours:02d}:{minutes:02d}</b>"
        ),
        reply_markup=kb.as_markup(),
    )
    await clck.answer()


@autrtr.callback_query(F.data == "setup_post")
async def setup_post(clck: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    date_value = data.get("autopost_date")
    hour = data.get(f"{autrtr.name}_sh")
    minute = data.get(f"{autrtr.name}_sm")
    if date_value is None or hour is None or minute is None:
        await clck.answer(
            "Выберите дату и время заново",
            show_alert=True,
        )
        return

    selected_date = date.fromisoformat(date_value)
    local_run_at = datetime(
        selected_date.year,
        selected_date.month,
        selected_date.day,
        hour,
        minute,
        tzinfo=ZoneInfo(stg.SCHEDULER_TIMEZONE),
    )
    run_at = local_run_at.astimezone(timezone.utc)
    if run_at <= datetime.now(timezone.utc):
        await clck.answer(
            "Время публикации должно быть в будущем",
            show_alert=True,
        )
        return

    await state.update_data(autopost_run_at=run_at.isoformat())
    await state.set_state(AutopostST.waiting_post)
    await clck.message.edit_text(
        "<b>Отправьте сообщение, которое нужно опубликовать.</b>\n\n"
        "В указанное время бот перешлёт его в канал. "
        "Не удаляйте исходное сообщение до публикации."
    )
    await clck.answer()


@autrtr.message(AutopostST.waiting_post)
async def receive_scheduled_post(
    msg: Message,
    state: FSMContext,
    ses: AsyncSession,
):
    run_at_value = await state.get_value("autopost_run_at")
    if run_at_value is None:
        await state.clear()
        await msg.answer(
            "Данные планирования устарели. Начните заново."
        )
        return

    if msg.media_group_id is not None:
        _collect_album_message(
            msg=msg,
            state=state,
            run_at_value=run_at_value,
        )
        return

    await _save_scheduled_messages(
        messages=[msg],
        state=state,
        session=ses,
        run_at_value=run_at_value,
    )


def _collect_album_message(
    *,
    msg: Message,
    state: FSMContext,
    run_at_value: str,
) -> None:
    key = (msg.chat.id, msg.media_group_id)
    _pending_albums.setdefault(key, []).append(msg)
    if key not in _album_tasks:
        _album_tasks[key] = asyncio.create_task(
            _finalize_album(
                key=key,
                state=state,
                run_at_value=run_at_value,
            )
        )


async def _finalize_album(
    *,
    key: tuple[int, str],
    state: FSMContext,
    run_at_value: str,
) -> None:
    try:
        await asyncio.sleep(_ALBUM_COLLECTION_DELAY)
        messages = sorted(
            _pending_albums.pop(key, []),
            key=lambda message: message.message_id,
        )
        if not messages:
            return
        async with async_session() as session:
            await _save_scheduled_messages(
                messages=messages,
                state=state,
                session=session,
                run_at_value=run_at_value,
            )
    finally:
        _album_tasks.pop(key, None)
        _pending_albums.pop(key, None)


async def _save_scheduled_messages(
    *,
    messages: list[Message],
    state: FSMContext,
    session: AsyncSession,
    run_at_value: str,
) -> None:
    msg = messages[0]
    try:
        post = await create_scheduled_post(
            session=session,
            source_chat_id=msg.chat.id,
            source_message_id=msg.message_id,
            source_message_ids=[
                message.message_id for message in messages
            ],
            target_chat_id=stg.CHANNEL_ID,
            created_by_id=(
                msg.from_user.id if msg.from_user is not None else msg.chat.id
            ),
            run_at=datetime.fromisoformat(run_at_value),
        )
    except ValueError:
        await state.clear()
        await msg.answer(
            "Время публикации указано неверно или уже наступило. "
            "Начните заново."
        )
        return
    except Exception:
        logger.exception("Failed to persist a scheduled post")
        await session.rollback()
        await msg.answer(
            "Не удалось запланировать публикацию. Повторите попытку."
        )
        return

    await state.clear()
    local_run_at = post.run_at.astimezone(
        ZoneInfo(stg.SCHEDULER_TIMEZONE)
    )
    await msg.answer(
        f"<tg-emoji emoji-id='5206607081334906820'>✔️</tg-emoji> <b>Публикация №{post.id} запланирована</b>\n"
        f"Время публикации: "
        f"<code>{local_run_at:%d.%m.%Y %H:%M}</code> "
        f"({stg.SCHEDULER_TIMEZONE})"
    )
