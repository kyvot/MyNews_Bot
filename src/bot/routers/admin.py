from aiogram.types import Message, CallbackQuery
from aiogram.filters.command import Command
from sqlalchemy.ext.asyncio import AsyncSession
from bot.db_methods import get_admins
from core.config import stg
from aiogram import Router


adminrtr = Router()


@adminrtr.message(Command("admin"))
async def send_admin_panel(msg: Message, ses: AsyncSession):
    admins = await get_admins(ses=ses)
    print(admins)


    ...
