from html import escape

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.db.models import NewsRaw


SEPARATOR = "===================="
_SOURCE_LABELS = {
    "Habr": "Хабр",
    "Habr / Artificial Intelligence": "Хабр / Искусственный интеллект",
    "Habr / Open Source": "Хабр / Открытый исходный код",
    "Habr / Python": "Хабр / Python",
    "Habr / Algorithms": "Хабр / Алгоритмы",
    "Habr / Web Development": "Хабр / Веб-разработка",
    "Habr / PostgreSQL": "Хабр / PostgreSQL",
    "Habr / Information Security": "Хабр / Информационная безопасность",
    "Habr / API Design": "Хабр / Проектирование API",
    "Habr / Data Storage": "Хабр / Хранение данных",
    "Habr / Code Quality": "Хабр / Качество кода",
    "Habr / Hackathons": "Хабр / Хакатоны",
    "Habr / Linux": "Хабр / Linux",
    "Habr / Image Processing": "Хабр / Обработка изображений",
    "Habr / IT Law": "Хабр / Законодательство в ИТ",
    "Habr / IT Legislation": "Хабр / Законодательство в ИТ",
    "Habr / Cryptography": "Хабр / Криптография",
    "Habr / All Articles": "Хабр / Все публикации",
    "Habr / All Streams": "Хабр / Все публикации",
    "Habr / Best": "Хабр / Лучшее",
    "Xakep": "Хакер",
    "Хабр / Open Source": "Хабр / Открытый исходный код",
    "Хабр / Алгоримты": "Хабр / Алгоритмы",
    "Хабр / Веб разработка": "Хабр / Веб-разработка",
    "Хабр / Законодательство в IT": "Хабр / Законодательство в ИТ",
    "Хабр / Best": "Хабр / Лучшее",
    "Хакер РУ": "Хакер",
}


class NewsCallback(CallbackData, prefix="nw"):
    action: str
    news_id: int


def news_keyboard(
    news_id: int,
    likes: int,
    dislikes: int,
    *,
    show_summary: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if show_summary:
        builder.button(
            text="Показать оригинал",
            callback_data=NewsCallback(action="original", news_id=news_id),
            icon_custom_emoji_id='5373318693650458620'
        )
    else:
        builder.button(
            text="Подробнее",
            callback_data=NewsCallback(action="more", news_id=news_id),
            icon_custom_emoji_id='5373318693650458620'
        )
    builder.button(
        text=f"{likes}",
        callback_data=NewsCallback(action="like", news_id=news_id),
        icon_custom_emoji_id='5429353942255410691'
    )
    builder.button(
        text=f"{dislikes}",
        callback_data=NewsCallback(action="dislike", news_id=news_id),
        icon_custom_emoji_id='5411242430240414694'
    )
    builder.adjust(1, 2)
    return builder.as_markup()


def format_news_card(
    news: NewsRaw,
    *,
    show_summary: bool = False,
) -> str:
    body = news.summary if show_summary and news.summary else news.content
    label = (
        "Краткое содержание"
        if show_summary and news.summary
        else "Описание"
    )
    body = _limit_text(body, 2_500)
    title = _limit_text(news.title, 500)
    source = _limit_text(normalize_source_label(news.source), 200)
    body_display = body if show_summary and news.summary else escape(body)

    return (
        f"<b>{escape(title)}</b>\n\n"
        f"{SEPARATOR}\n\n"
        f"<b>{label}:</b>\n{body_display}\n\n"
        f"{SEPARATOR}\n\n"
        f"<b>Источник:</b> {escape(source)}\n"
        f'<tg-emoji emoji-id="5271604874419647061">🔗</tg-emoji> <a href="{escape(news.url, quote=True)}">Читать оригинал</a>'
    )


def normalize_source_label(source: str) -> str:
    clean = source.strip()
    return _SOURCE_LABELS.get(clean, clean)


def _limit_text(text: str, limit: int) -> str:
    clean = "\n".join(
        " ".join(line.split())
        for line in text.splitlines()
        if line.strip()
    )
    if len(clean) <= limit:
        return clean
    shortened = clean[: limit - 1].rsplit(" ", 1)[0].rstrip()
    return f"{shortened}…"
