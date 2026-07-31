from aiogram import Router
from aiogram.types import Message
from bot.view import start_cmd_priv
from bot.db_methods import get_admins
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.fsm.context import FSMContext
from aiogram.filters.command import CommandStart


start_rtr = Router()


@start_rtr.message(CommandStart())
async def send_welcome(msg: Message, ses: AsyncSession, state: FSMContext):
    try:
        await msg.delete()
    except:
        pass
    admin_ids = await get_admins(ses=ses)
    match msg.chat.type:
        case "private":
            admin_obj = next((dm for dm in admin_ids if dm.user_id == msg.chat.id), None)
            if admin_obj is None:
                return
            await start_cmd_priv(msg, state)
        case _:
            return
