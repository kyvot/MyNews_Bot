from aiogram.filters import callback_data
import calendar
from datetime import date
from utils.inline import column_order
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


MONTHS = [
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]


def get_calendar(
    base_clck: str,
    year: int | None = None,
    month: int | None = None,
) -> InlineKeyboardMarkup:
    today = date.today()

    year = year or today.year
    month = month or today.month

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=f"{MONTHS[month]} {year}",
            callback_data="clgn",
            icon_custom_emoji_id="5413879192267805083"
        )
    )

    builder.row(
        *[
            InlineKeyboardButton(
                text=day,
                callback_data="clgn",
            )
            for day in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        ]
    )

    month_calendar = calendar.monthcalendar(year, month)

    for week in month_calendar:
        buttons = []

        for day in week:
            if day == 0:
                buttons.append(
                    InlineKeyboardButton(
                        text=" ",
                        callback_data="clgn",
                        icon_custom_emoji_id="5294481802973432756"
                    )
                )
                continue

            if (
                year == today.year
                and month == today.month
                and day == today.day
            ):
                text = f"• {day} •"
            else:
                text = str(day)
            buttons.append(
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"{base_clck}_cldt:{year}:{month}:{day}",
                )
            )

        builder.row(*buttons)

    if month == 1:
        previous_year = year - 1
        previous_month = 12
    else:
        previous_year = year
        previous_month = month - 1

    if month == 12:
        next_year = year + 1
        next_month = 1
    else:
        next_year = year
        next_month = month + 1

    builder.row(
        InlineKeyboardButton(
            text=" ",
            icon_custom_emoji_id="5325598171018051937",
            callback_data=f"{base_clck}_clpr:{previous_year}:{previous_month}",
        ),
        InlineKeyboardButton(
            text="Сегодня",
            callback_data=f"{base_clck}_cltd:{today.year}:{today.month}",
        ),
        InlineKeyboardButton(
            text=" ",
            icon_custom_emoji_id="5327939533784760171",
            callback_data=f"{base_clck}_clnt:{next_year}:{next_month}",
        ),
    )

    return builder.as_markup()



def get_hour_keyb(base_clck: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    
    for i in column_order(start=0, stop=24, step=1, columns=4):
    
        if len(str(i)) == 1:
            txt = f"0{i}"
        else:
            txt = str(i)
        kb.button(
            text=txt,
            callback_data=f"{base_clck}_tmh:{i}"
        )
    kb.adjust(4)
    return kb.as_markup()


def get_minutes_keyb(base_clck: str) -> InlineKeyboardMarkup:

    kb = InlineKeyboardBuilder()
    for i in column_order(start=0, stop=60, step=5, columns=4):
        if len(str(i)) == 1:
            txt = f"0{i}"
        else:
            txt = str(i)
        kb.button(
            text=txt,
            callback_data=f"{base_clck}_tmm:{i}"
        )
    kb.adjust(4)
    return kb.as_markup()
