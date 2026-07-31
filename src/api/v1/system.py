import orjson
from fastapi import APIRouter, Request, Header, HTTPException, status
from core.config import stg
from aiogram.types.update import Update
from bot import bot, dp


sysrtr = APIRouter(
    tags=["System routers"]
)


@sysrtr.post(
    stg.WEBHOOK_PATH,
    responses={
        200: {
            "description": "Telegram webhook response",
            "content": {
                "application/json": {
                    "example": {
                        "ok": True
                    }
                }
            }
        }
    }
)
async def tg_webhook(
        req: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None)
) -> dict:
    if x_telegram_bot_api_secret_token != stg.XTGTOK:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid secret token"
        )

    raw: bytes = await req.body()
    data = orjson.loads(raw)
    
    update: Update = Update.model_validate(
        data,
        context={"bot": bot}
    )
    await dp.feed_update(bot=bot, update=update)
    return {"ok": True}

