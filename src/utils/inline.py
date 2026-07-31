from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext


async def delete_inline(state: FSMContext, obj: Message | CallbackQuery):
    data = await state.get_data()
    prev_id = data.get("previn")
    
    chat_id = obj.chat.id if isinstance(obj, Message) else obj.message.chat.id
    if prev_id:
        try:
            await obj.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=prev_id
            )
        except: pass


async def save_inline(msg_id: int, state: FSMContext):
    await state.update_data(
        previn=msg_id
    )


def column_order(
    start: int,
    stop: int,
    step: int = 1,
    columns: int = 3,
) -> tuple[int, ...]:
    values = list(range(start, stop, step))
    rows = (len(values) + columns - 1) // columns

    return tuple(
        values[row + column * rows]
        for row in range(rows)
        for column in range(columns)
        if row + column * rows < len(values)
    )
