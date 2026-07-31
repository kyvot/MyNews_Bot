import httpx2
import asyncio
import stamina
import feedparser
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from core.config import stg, logger
from fake_useragent import UserAgent
from fake_useragent import FakeUserAgent
from typing import Any


ua: FakeUserAgent = UserAgent(
    fallback="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
)

semaphore = asyncio.Semaphore(stg.LIMIT_REQ)
request_lock = asyncio.Lock()


@stamina.retry(on=httpx2.HTTPError, attempts=stg.ATTEMPTS_FOR_FETCH)
async def fetch_single_url(feed_url: str, client: httpx2.AsyncClient) -> str:
    async with semaphore:
        # Keep a minimum interval between request start times.
        async with request_lock:
            await asyncio.sleep(stg.SLEEP_REQ)

        resp = await client.get(
  
            url=feed_url,
            headers={"User-Agent": ua.random},
            timeout=stg.TIMEOUT_FETCH,
        )
        resp.raise_for_status()
        return resp.text or ""


async def parse_rss(
    feed_url: str,
    source: str = "Habr",
    limit: int = stg.LIMIT_RSS_NEWS,
    client: httpx2.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """
    Parse one RSS feed and extract news items.

    Args:
        feed_url: RSS feed URL.
        source: Source name added to every result.
        limit: Maximum number of news items to extract.

    Returns:
        A list of normalized news dictionaries.
    """
    if client is None:
        async with httpx2.AsyncClient(follow_redirects=True) as own_client:
            return await parse_rss(
                feed_url=feed_url,
                source=source,
                limit=limit,
                client=own_client,
            )

    news_list: list[dict[str, Any]] = []

    try:
        raw_html = await fetch_single_url(feed_url, client)

        if not raw_html:
            logger.warning(f"Empty response from {feed_url}")
            return news_list

        feed = feedparser.parse(raw_html)

        if feed.bozo and not feed.entries:
            logger.warning(
                f"Feed parser failed for {feed_url}: {feed.bozo_exception}"
            )
            return news_list

        for entry in feed.entries[:limit]:
            try:
                title = entry.get("title", "Untitled").strip()
                url = entry.get("link", "")

                raw_desc = entry.get("description") or entry.get("summary") or ""
                if entry.get("content"):
                    raw_desc = entry.content[0].get("value", raw_desc)

                soup = BeautifulSoup(raw_desc, "html.parser")
                desc = (
                    soup.get_text(separator=" ", strip=True)
                    .removesuffix("Читать далее")
                    .strip()
                )

                img_links = [
                    img["src"] for img in soup.find_all("img") if img.get("src")
                ]

                publish_date = None
                parsed_time = entry.get("published_parsed") or entry.get(
                    "updated_parsed"
                )
                if parsed_time:
                    publish_date = datetime(
                        *parsed_time[:6],
                        tzinfo=timezone.utc,
                    )

                news_list.append(
                    {
                        "title": title,
                        "url": url,
                        "source": source,
                        "img_links": img_links,
                        "desc": desc,
                        "published_at": publish_date,
                    }
                )

            except Exception as e:
                logger.exception(f"Failed to parse entry from {feed_url}: {e}")
                continue

    except Exception as e:
        logger.exception(f"Failed to parse RSS feed {feed_url}: {e}")

    return news_list


async def parse_multiple_feeds(feed_urls: list[str], source: str = "Habr"):
    async with httpx2.AsyncClient(follow_redirects=True) as client:
        tasks = [
            parse_rss(url, source, client=client)
            for url in feed_urls
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_news = []
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Failed to parse feed: {result}")
        else:
            all_news.extend(result)

    return all_news
