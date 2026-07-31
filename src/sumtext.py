"""Small CLI wrapper around the summarization service used by the bot."""

import asyncio
import re
import sys

from ai.base import SummaryContext
from ai.service import summary_service


async def summarize_input(value: str) -> str:
    try:
        if re.match(r"https?://", value):
            return await summary_service.summarize_url(value)
        return await summary_service.summarize_text(
            value,
            context=SummaryContext(url=""),
        )
    finally:
        await summary_service.close()


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python sumtext.py <text_or_url>")
    print(asyncio.run(summarize_input(sys.argv[1])))


if __name__ == "__main__":
    main()
