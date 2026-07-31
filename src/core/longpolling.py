from bot.routers import main_router
from bot import bot, dp
from core.config import logger
from core.scheduler import start_scheduler, stop_scheduler
from bot.services import close_services


async def run_polling():
    try:
        try:
            await bot.delete_webhook(
                drop_pending_updates=True
            )
        except Exception:
            logger.exception("Failed to delete webhook before polling")

        await start_scheduler()
        await dp.start_polling(bot)
    finally:
        await stop_scheduler()
        await close_services()
        await bot.session.close()
