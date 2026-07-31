"""Application services used by bot handlers and background jobs."""


async def close_services() -> None:
    from ai.service import summary_service
    from bot.services.news_feed import news_feed_publisher

    await summary_service.close()
    await news_feed_publisher.close()
