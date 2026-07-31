import asyncio
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup
import httpx2
import orjson


BASE_URL = "https://hacker-news.firebaseio.com/v0"


async def fetch_top_news_with_content(
    limit: int = 10,
    *,
    client: httpx2.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Load HN metadata without prefetching arbitrary article websites.

    The original article is downloaded only after a user presses
    ``Read more``. That keeps parser cycles light and ensures the guarded
    HTTPX2 + Trafilatura pipeline is used for external pages.
    """
    if client is None:
        async with httpx2.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=10.0,
        ) as own_client:
            return await fetch_top_news_with_content(
                limit,
                client=own_client,
            )

    top_response = await client.get(f"{BASE_URL}/topstories.json")
    top_response.raise_for_status()
    story_ids = orjson.loads(top_response.content)[:limit]

    results = await asyncio.gather(
        *(_fetch_story(client, story_id) for story_id in story_ids),
        return_exceptions=True,
    )

    news: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, Exception) or result is None:
            continue
        news.append(result)
    return news


async def _fetch_story(
    client: httpx2.AsyncClient,
    story_id: int,
) -> dict[str, Any] | None:
    response = await client.get(f"{BASE_URL}/item/{story_id}.json")
    response.raise_for_status()
    item = response.json()
    if (
        not item
        or item.get("type") != "story"
        or not item.get("url")
    ):
        return None

    description = BeautifulSoup(
        str(item.get("text") or ""),
        "html.parser",
    ).get_text(separator=" ", strip=True)
    if not description:
        description = "Popular Hacker News community post."

    return {
        "id": item["id"],
        "title": item.get("title") or "Untitled",
        "url": item["url"],
        "content": description,
        "score": item.get("score", 0),
        "author": item.get("by", "unknown"),
        "published_at": datetime.fromtimestamp(
            item["time"],
            tz=timezone.utc,
        ),
        "comments_count": item.get("descendants", 0),
        "source": "Hacker News",
    }
