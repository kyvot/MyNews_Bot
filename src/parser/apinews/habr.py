import asyncio
from utils.rss import parse_rss


async def main():
    HABR_FEEDS = {
        "Habr / Artificial Intelligence": "https://habr.com/ru/rss/hubs/artificial_intelligence/articles/?fl=ru",
        "Habr / Open Source": "https://habr.com/ru/rss/hubs/open_source/articles/?fl=ru",
        "Habr / Python": "https://habr.com/ru/rss/hubs/python/articles/?fl=ru",
        "Habr / Algorithms": "https://habr.com/ru/rss/hubs/algorithms/articles/?fl=ru",
        "Habr / Web Development": "https://habr.com/ru/rss/hubs/webdev/articles/?fl=ru",
        "Habr / PostgreSQL": "https://habr.com/ru/rss/hubs/postgresql/articles/?fl=ru",
        "Habr / Information Security": "https://habr.com/ru/rss/hubs/infosecurity/articles/?fl=ru",
        "Habr / API Design": "https://habr.com/ru/rss/hubs/api/articles/?fl=ru",
        "Habr / Data Storage": "https://habr.com/ru/rss/hubs/dwh/articles/?fl=ru",
        "Habr / Code Quality": "https://habr.com/ru/rss/hubs/complete_code/articles/?fl=ru",
        "Habr / Hackathons": "https://habr.com/ru/rss/hubs/hackathons/articles/?fl=ru",
        "Habr / Linux": "https://habr.com/ru/rss/hubs/linux_dev/articles/?fl=ru",
        "Habr / Image Processing": "https://habr.com/ru/rss/hubs/image_processing/articles/?fl=ru",
        "Habr / IT Legislation": "https://habr.com/ru/rss/hubs/business-laws/articles/?fl=ru",
        "Habr / Cryptography": "https://habr.com/ru/rss/hubs/crypto/articles/?fl=ru",
        "Habr / All Streams": "https://habr.com/ru/rss/articles/?fl=ru",
        "Habr / Best": "https://habr.com/ru/rss/articles/top/",
        "Dev.to": "https://dev.to/feed",
    }
    with open("dd.txt", "w") as file:
        for key, value in HABR_FEEDS.items():
            news_list = await parse_rss(feed_url=value, source=key)
            for n in news_list:
                for i in n.items():
                    file.write(f"{i[0]} :  {i[1]}\n")
                file.write("\n" + "=" * 40 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
