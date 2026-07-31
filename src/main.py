import asyncio
from core.config import stg, logger
from core.longpolling import run_polling
from core.webhook import run_webhook



async def main():
    if stg.RUN_MODE == "web":
        await run_webhook()
    elif stg.RUN_MODE == "long":
        await run_polling()
    else:
        logger.info("Please, set the run mode in environment variables.") 


if __name__ == "__main__":
    asyncio.run(main())
