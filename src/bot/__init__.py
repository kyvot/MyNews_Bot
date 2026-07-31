from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode

from bot.routers import main_router
from core.config import stg
from core.db.db import async_session


session = AiohttpSession(proxy=stg.PROXY_URL)

bot = Bot(
    token=stg.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    session=session,
)
dp = Dispatcher()
dp.include_router(main_router)


class DBSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with async_session() as session:
            data["ses"] = session
            return await handler(event, data)


db_middleware = DBSessionMiddleware()
dp.update.outer_middleware(db_middleware)


async def setup_webhook():
    await bot.set_webhook(
        url=stg.BASE_URL,
        secret_token=stg.XTGTOK,
        drop_pending_updates=True,
    )
