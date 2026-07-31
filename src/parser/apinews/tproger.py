import httpx2
import asyncio
import stamina
import feedparser
from datetime import datetime
from bs4 import BeautifulSoup
from core.config import stg, logger
from utils.rss import fetch_single_url, parse_rss


async def main():
    res = await parse_rss(feed_url="https://dtf.ru/rss", source="tproger")
    print(res)


if __name__ == "__main__":
    asyncio.run(main())
