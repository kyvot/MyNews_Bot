import asyncio
from typing import Any

import httpx2

from core.config import logger, stg
from parser.apinews.hackernews import fetch_top_news_with_content
from parser.feeds import RSS_FEEDS
from utils.rss import parse_rss


async def collect_news() -> list[dict[str, Any]]:
    """Run all configured parsers and normalize their output keys."""
    async with httpx2.AsyncClient(
        follow_redirects=True,
        trust_env=False,
        timeout=stg.TIMEOUT_FETCH,
    ) as client:
        rss_tasks = [
            parse_rss(
                feed_url=url,
                source=source,
                client=client,
            )
            for source, url in RSS_FEEDS.items()
        ]
        rss_results, hacker_news_result = await asyncio.gather(
            asyncio.gather(*rss_tasks, return_exceptions=True),
            fetch_top_news_with_content(
                limit=stg.LIMIT_RSS_NEWS,
                client=client,
            ),
            return_exceptions=True,
        )

    news: list[dict[str, Any]] = []
    for result in rss_results:
        if isinstance(result, Exception):
            logger.error("RSS parser failed: %s", result)
        else:
            news.extend(result)

    if isinstance(hacker_news_result, Exception):
        logger.error("Hacker News parser failed: %s", hacker_news_result)
    else:
        news.extend(
            {
                "title": item["title"],
                "url": item["url"],
                "source": item["source"],
                "img_links": [],
                "desc": item["content"],
                "published_at": item["published_at"],
            }
            for item in hacker_news_result
        )

    # Preserve parser order while removing duplicates from overlapping feeds.
    unique: dict[str, dict[str, Any]] = {}
    for item in news:
        url = str(item.get("url") or "").strip()
        if url and url not in unique:
            unique[url] = item
    return list(unique.values())
