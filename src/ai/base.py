from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SummaryContext:
    url: str
    title: str | None = None
    source: str | None = None


class Summarizer(Protocol):
    async def summarize(
        self,
        text: str,
        *,
        context: SummaryContext,
    ) -> str: ...
