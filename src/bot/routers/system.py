from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram import Router, F
from aiogram.types import CallbackQuery
from core.config import stg
from utils.calendar import get_calendar, get_hour_keyb, get_minutes_keyb


sysrtr = Router()


@sysrtr.callback_query(F.data == "clgn")
async def calendar_ignore_btns(clck: CallbackQuery):
    await clck.answer(
        text="Это информационная кнопка календаря",
        show_alert=False,
    )
    return


@sysrtr.callback_query(F.data.contains("_clpr:"))
async def calendar_previous(clck: CallbackQuery):
    base_data, year, month = clck.data.split(":")
    base_clck = base_data.removesuffix("_clpr")

    await clck.message.edit_reply_markup(
        reply_markup=get_calendar(
            year=int(year),
            month=int(month),
            base_clck=base_clck
        )
    )

    await clck.answer()


@sysrtr.callback_query(F.data.contains("_clnt:"))
async def calendar_next(clck: CallbackQuery):
    base_data, year, month = clck.data.split(":")
    base_clck = base_data.removesuffix("_clnt")

    await clck.message.edit_reply_markup(
        reply_markup=get_calendar(
            year=int(year),
            month=int(month),
            base_clck=base_clck
        )
    )

    await clck.answer()


@sysrtr.callback_query(F.data.contains("_cltd:"))
async def calendar_today(clck: CallbackQuery):
    base_data, year, month = clck.data.split(":")
    base_clck = base_data.removesuffix("_cltd")

    await clck.message.edit_reply_markup(
        reply_markup=get_calendar(
            year=int(year),
            month=int(month),
            base_clck=base_clck
        )
    )

    await clck.answer()


@sysrtr.callback_query(F.data.contains("_stime"))
async def set_time(clck: CallbackQuery):
    base_clck, _ = clck.data.split("_")
    await clck.message.edit_text(
        text=(
            "<tg-emoji emoji-id='6048701411888206453'>🕐</tg-emoji> "
            "<b>Выберите час:</b>"
        ),
        reply_markup=get_hour_keyb(base_clck=base_clck)
    )


@sysrtr.callback_query(F.data.contains("_tmh"))
async def set_h(clck: CallbackQuery, state: FSMContext):
    base_data, hour = clck.data.split(":")
    base_clck = base_data.removesuffix("_tmh")

    if base_clck == "autrtr":
        date_value = await state.get_value("autopost_date")
        if date_value is not None:
            selected_date = date.fromisoformat(date_value)
            local_now = datetime.now(ZoneInfo(stg.SCHEDULER_TIMEZONE))
            if (
                selected_date == local_now.date()
                and int(hour) < local_now.hour
            ):
                await clck.answer(
                    "Этот час уже прошёл. Выберите будущее время.",
                    show_alert=True,
                )
                return

    await state.update_data(
        data={
            f"{base_clck}_sh": int(hour)
        }
    )

    await clck.message.edit_text(
        text=(
            "<tg-emoji emoji-id='6048701411888206453'>🕐</tg-emoji> "
            "<b>Выберите минуты:</b>"
        ),
        reply_markup=get_minutes_keyb(base_clck)
    )
