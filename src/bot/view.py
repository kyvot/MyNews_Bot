from utils.inline import save_inline
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder


async def start_cmd_priv(msg: Message | CallbackQuery, state: FSMContext):
    if msg is CallbackQuery:
        msg = msg.message 

    kb = InlineKeyboardBuilder()
    kb.button(
        text="Запустить парсер",
        callback_data="start_parse",
        icon_custom_emoji_id="6147551548789492357"
    )
    kb.button(
        text="Остановить парсер",
        callback_data="stop_parse",
        icon_custom_emoji_id="5318860058820359775"
    )
    kb.button(
        text="Запланировать публикацию",
        callback_data="autopost",
        icon_custom_emoji_id="5413879192267805083"
    )
    kb.button(
        text="Очистить метаданные",
        callback_data="anonymize",
        icon_custom_emoji_id="5445267414562389170"
    )
    kb.adjust(1)
    txt = "<b>Выберите нужное действие:</b>"
    msg_chat = await msg.answer(
        txt,
        reply_markup=kb.as_markup()
    )
    msg_id = msg_chat.message_id
    await save_inline(msg_id=msg_id, state=state)
