import httpx2

from ai.article import close_extraction_pool, extract_article_text
from ai.base import Summarizer, SummaryContext
from ai.errors import SummaryConfigurationError, SummaryError
from ai.providers import (
    ExtractiveSummarizer,
    OpenRouterClient,
    OpenRouterSummarizer,
)
from core.config import logger, stg
from utils.deepl import DeepLClient


class SummaryService:
    def __init__(self) -> None:
        self._article_client = httpx2.AsyncClient(
            follow_redirects=False,
            trust_env=False,
        )
        self._ai_client = httpx2.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            proxy=stg.OPENROUTER_PROXY_URL,
        )
        self._translation_client = httpx2.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            proxy=stg.DEEPL_PROXY_URL,
        )
        self.openrouter_client = OpenRouterClient(self._ai_client)
        self.deepl_client = DeepLClient(self._translation_client)

    async def summarize_url(
        self,
        url: str,
        *,
        title: str | None = None,
        source: str | None = None,
    ) -> str:
        article = await extract_article_text(url, self._article_client)
        context = SummaryContext(url=url, title=title, source=source)
        return await self.summarize_text(article, context=context)

    async def summarize_text(
        self,
        text: str,
        *,
        context: SummaryContext | None = None,
    ) -> str:
        context = context or SummaryContext(url="")
        provider = self._provider()

        try:
            summary = await self._summarize_long_text(
                provider,
                text,
                context=context,
            )
        except SummaryError:
            if (
                isinstance(provider, ExtractiveSummarizer)
                or not stg.AI_EXTRACTIVE_FALLBACK_ENABLED
            ):
                raise
            logger.exception(
                "AI summary failed; using the extractive fallback"
            )
            summary = await ExtractiveSummarizer().summarize(
                text,
                context=context,
            )
        return await self._ensure_content_language(summary)

    async def close(self) -> None:
        await self._article_client.aclose()
        await self._ai_client.aclose()
        await self._translation_client.aclose()
        close_extraction_pool(terminate=True)

    def _provider(self) -> Summarizer:
        match stg.AI_PROVIDER.casefold():
            case "openrouter":
                return OpenRouterSummarizer(self.openrouter_client)
            case "extractive" | "none":
                return ExtractiveSummarizer()
            case provider:
                raise SummaryConfigurationError(
                    f"Unknown AI provider: {provider}"
                )

    async def _ensure_content_language(self, summary: str) -> str:
        # Delayed import keeps the translation utility reusable without an
        # ai.service import cycle.
        from utils.translate import translate_text

        translated = await translate_text(
            summary,
            target_language=stg.AI_LANGUAGE,
            client=self.deepl_client,
        )
        return translated["translated_text"]

    async def _summarize_long_text(
        self,
        provider: Summarizer,
        text: str,
        *,
        context: SummaryContext,
    ) -> str:
        chunks = _split_chunks(text, stg.AI_CHUNK_CHARS)
        if len(chunks) == 1:
            return await provider.summarize(chunks[0], context=context)

        summaries = [
            await provider.summarize(chunk, context=context)
            for chunk in chunks
        ]
        return await provider.summarize(
            "\n\n".join(summaries),
            context=context,
        )


def _split_chunks(text: str, max_chars: int) -> list[str]:
    paragraphs = [
        paragraph.strip()
        for paragraph in text.splitlines()
        if paragraph.strip()
    ]
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0

    for paragraph in paragraphs:
        if current and current_size + len(paragraph) + 2 > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_size = 0

        if len(paragraph) > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_size = 0
            chunks.extend(
                paragraph[index:index + max_chars]
                for index in range(0, len(paragraph), max_chars)
            )
            continue

        current.append(paragraph)
        current_size += len(paragraph) + 2

    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text[:max_chars]]


summary_service = SummaryService()
