import uvicorn
from fastapi import FastAPI
from api import main_router
from bot import bot
from core.config import stg, logger
from core.scheduler import start_scheduler, stop_scheduler
from bot.services import close_services


async def lifespan(app: FastAPI):
    try:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            logger.exception("Failed to delete the previous webhook")
        await bot.set_webhook(
            url=stg.BASE_URL + stg.WEBHOOK_PATH,
            secret_token=stg.XTGTOK,
            drop_pending_updates=True
        )
        await start_scheduler()
        yield
    finally:
        await stop_scheduler()
        await close_services()
        logger.info("Clearing temporary data!")
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            logger.exception("Failed to delete webhook during shutdown")
        await bot.session.close()


async def run_webhook():
    app = FastAPI(
        docs_url="/docs",
        redoc_url=None,
        version="0.1.0",
        lifespan=lifespan
    )
    app.include_router(main_router)

    server = uvicorn.Server(
        uvicorn.Config(
            app=app,
            host=stg.WEBHOST,
            port=stg.WEBPORT,
        )
    )
    await server.serve()
