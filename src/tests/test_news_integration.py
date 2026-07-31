import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import orjson
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import GetChat, SendMessage
from pydantic import SecretStr


os.environ.setdefault("XTGTOK", "test-secret")
os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_FOR_UNIT_TESTS")
os.environ.setdefault("DEFAULT_ADMIN_ID", "1")
os.environ.setdefault("GROUP_ID", "-1001")
os.environ.setdefault("CHANNEL_ID", "-1002")
os.environ.setdefault("BASE_URL", "https://example.test")
os.environ.setdefault("PROXY_URL", "socks5://127.0.0.1:2080")

from ai.article import extract_article_text
from ai.base import SummaryContext
from ai.errors import AIRequestError, TranslationError
from ai.providers import (
    ExtractiveSummarizer,
    OpenRouterClient,
    OpenRouterSummarizer,
)
from ai.service import summary_service
from bot.news_ui import (
    NewsCallback,
    SEPARATOR,
    format_news_card,
    news_keyboard,
)
from bot.services.news_feed import (
    ParsedNews,
    PublicationClaim,
    PublicationClaimLostError,
    news_feed_publisher,
)
from bot.services import parser_runner
from core.config import (
    Config,
    _normalize_telegram_destination_id,
    _validate_proxy_url,
    stg,
)
from core.db.models import NewsRaw
from parser.apinews.hackernews import fetch_top_news_with_content
from utils.deepl import DeepLClient
from utils.language import (
    resolve_deepl_target_language,
    resolve_language,
)
from utils.translate import NewsTranslation, translate_news, translate_text


class NewsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def test_content_language_accepts_names_codes_and_locales(self):
        cases = {
            "English": ("en", "English"),
            "EN": ("en", "English"),
            "en-US": ("en", "English"),
            "Russian": ("ru", "Russian"),
            "rus": ("ru", "Russian"),
            "ru_RU": ("ru", "Russian"),
        }

        for value, expected in cases.items():
            with self.subTest(value=value):
                resolved = resolve_language(value)
                self.assertEqual(
                    (resolved.code, resolved.name),
                    expected,
                )

    def test_telegram_destination_id_accepts_bot_api_and_compact_forms(self):
        self.assertEqual(
            _normalize_telegram_destination_id(-1005518576356),
            -1005518576356,
        )
        self.assertEqual(
            _normalize_telegram_destination_id(5518576356),
            -1005518576356,
        )
        self.assertEqual(
            _normalize_telegram_destination_id("1005518576356"),
            -1005518576356,
        )

    def test_telegram_proxy_accepts_only_socks5(self):
        self.assertEqual(
            _validate_proxy_url(
                "socks5://user:pass@127.0.0.1:2080",
                allowed_schemes={"socks5"},
                setting_name="PROXY_URL",
            ),
            "socks5://user:pass@127.0.0.1:2080",
        )
        with self.assertRaises(ValueError):
            _validate_proxy_url(
                "http://127.0.0.1:2080",
                allowed_schemes={"socks5"},
                setting_name="PROXY_URL",
            )
        for invalid_url in (
            "socks5://127.0.0.1:0",
            "socks5://127.0.0.1:2080/path",
            "socks5://127.0.0.1:2080?option=value",
        ):
            with self.subTest(invalid_url=invalid_url):
                with self.assertRaises(ValueError):
                    _validate_proxy_url(
                        invalid_url,
                        allowed_schemes={"socks5"},
                        setting_name="PROXY_URL",
                    )

    def test_callback_data_is_short_and_contains_only_database_id(self):
        packed = NewsCallback(action="more", news_id=123).pack()

        self.assertEqual(packed, "nw:more:123")
        self.assertLessEqual(len(packed.encode()), 64)

    def test_card_escapes_parser_data_and_contains_original_link(self):
        news = NewsRaw(
            id=1,
            title="<script>Title</script>",
            source="Source & Co",
            url="https://example.com/a?x=1&y=2",
            content="First <b>block</b>",
            views=0,
            likes=0,
            dislikes=0,
            is_hit=False,
            voting_closed_at=datetime.now(timezone.utc)
            + timedelta(hours=1),
        )

        card = format_news_card(news)

        self.assertIn("&lt;script&gt;Title&lt;/script&gt;", card)
        self.assertIn("First &lt;b&gt;block&lt;/b&gt;", card)
        self.assertIn(SEPARATOR, card)
        self.assertIn("https://example.com/a?x=1&amp;y=2", card)
        self.assertIn("<b>Описание:</b>", card)
        self.assertIn("<b>Источник:</b>", card)
        self.assertIn(">Читать оригинал</a>", card)

        keyboard = news_keyboard(news.id, news.likes, news.dislikes)
        self.assertEqual(keyboard.inline_keyboard[0][0].text, "📖 Подробнее")

    def test_card_normalizes_legacy_source_label(self):
        news = NewsRaw(
            id=2,
            title="English title",
            source="Хабр / Алгоритмы",
            url="https://example.com/legacy",
            content="English description",
            views=0,
            likes=0,
            dislikes=0,
            is_hit=False,
            voting_closed_at=datetime.now(timezone.utc),
        )

        card = format_news_card(news)

        self.assertIn("<b>Источник:</b> Хабр / Алгоритмы", card)
        self.assertNotIn("Habr", card)

    def test_parser_normalizes_relative_and_duplicate_image_urls(self):
        item = ParsedNews.from_mapping(
            {
                "title": "Title",
                "url": "https://example.com/articles/1",
                "source": "Example",
                "desc": "Description",
                "img_links": ["/image.jpg", "/image.jpg"],
            }
        )

        self.assertEqual(
            item.image_urls,
            ("https://example.com/image.jpg",),
        )

    async def test_trafilatura_uses_html_downloaded_by_httpx2(self):
        html = """
        <html><body><article>
          <h1>Test article</h1>
          <p>This is a sufficiently long first paragraph about a real topic,
          with enough words for article extraction.</p>
          <p>This second paragraph contains additional useful details and
          makes the document suitable for Trafilatura.</p>
        </article></body></html>
        """

        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=html,
            )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler)
        ) as client:
            with patch(
                "ai.article.validate_public_url",
                AsyncMock(),
            ):
                text = await extract_article_text(
                    "https://example.com/article",
                    client,
                )

        self.assertIn("first paragraph", text)
        self.assertIn("second paragraph", text)

    async def test_extracting_fallback_limits_summary_size(self):
        summarizer = ExtractiveSummarizer()
        text = "word " * 1_000

        summary = await summarizer.summarize(
            text,
            context=SummaryContext(url="https://example.com"),
        )

        from core.config import stg

        self.assertLessEqual(len(summary), stg.AI_SUMMARY_MAX_CHARS)

    async def test_disabled_extractive_fallback_propagates_ai_error(self):
        class FailingSummarizer:
            async def summarize(self, text, *, context):
                raise AIRequestError("offline")

        with (
            patch.object(
                summary_service,
                "_provider",
                return_value=FailingSummarizer(),
            ),
            patch.object(stg, "AI_EXTRACTIVE_FALLBACK_ENABLED", False),
        ):
            with self.assertRaises(AIRequestError):
                await summary_service.summarize_text(
                    "Article text",
                    context=SummaryContext(
                        url="https://example.com/article",
                    ),
                )

    async def test_summary_service_enforces_english_output(self):
        class ForeignSummarizer:
            async def summarize(self, text, *, context):
                return "Краткое содержание статьи."

        translated = {
            "original": "Краткое содержание статьи.",
            "detected_language": "ru",
            "translated_text": "A concise summary of the article.",
            "target_language": "en",
        }
        with (
            patch.object(stg, "AI_LANGUAGE", "English"),
            patch.object(
                summary_service,
                "_provider",
                return_value=ForeignSummarizer(),
            ),
            patch(
                "utils.translate.translate_text",
                AsyncMock(return_value=translated),
            ) as translate,
        ):
            summary = await summary_service.summarize_text(
                "Article text",
                context=SummaryContext(
                    url="https://example.com/article",
                ),
            )

        self.assertEqual(summary, "A concise summary of the article.")
        translate.assert_awaited_once()
        self.assertEqual(
            translate.await_args.kwargs["target_language"],
            "English",
        )
        self.assertIs(
            translate.await_args.kwargs["client"],
            summary_service.deepl_client,
        )

    async def test_summary_service_enforces_russian_output(self):
        class ForeignSummarizer:
            async def summarize(self, text, *, context):
                return "A concise summary of the article."

        translated = {
            "original": "A concise summary of the article.",
            "detected_language": "en",
            "translated_text": "Краткое содержание статьи.",
            "target_language": "ru",
        }
        with (
            patch.object(stg, "AI_LANGUAGE", "ru-RU"),
            patch.object(
                summary_service,
                "_provider",
                return_value=ForeignSummarizer(),
            ),
            patch(
                "utils.translate.translate_text",
                AsyncMock(return_value=translated),
            ) as translate,
        ):
            summary = await summary_service.summarize_text(
                "Article text",
                context=SummaryContext(
                    url="https://example.com/article",
                ),
            )

        self.assertEqual(summary, "Краткое содержание статьи.")
        self.assertEqual(
            translate.await_args.kwargs["target_language"],
            "ru-RU",
        )

    async def test_openrouter_provider_uses_httpx2_contract(self):
        def handler(request: httpx2.Request) -> httpx2.Response:
            self.assertEqual(
                request.headers["authorization"],
                "Bearer test-key",
            )
            self.assertEqual(
                request.headers["x-openrouter-title"],
                "Test News",
            )
            payload = orjson.loads(request.content)
            self.assertEqual(payload["model"], "test/model")
            self.assertEqual(payload["max_tokens"], 350)
            self.assertNotIn("max_completion_tokens", payload)
            self.assertFalse(payload["stream"])
            self.assertIn("in English", payload["messages"][0]["content"])
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "Concise summary"}}
                    ]
                },
            )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler)
        ) as client:
            provider = OpenRouterSummarizer(
                OpenRouterClient(client)
            )

            with (
                patch.object(
                    stg,
                    "OPENROUTER_API_KEY",
                    SecretStr("test-key"),
                ),
                patch.object(
                    stg,
                    "OPENROUTER_CHAT_COMPLETIONS_URL",
                    "https://api.example.test/v1/chat/completions",
                ),
                patch.object(stg, "OPENROUTER_MODEL", "test/model"),
                patch.object(stg, "AI_LANGUAGE", "English"),
                patch.object(
                    stg,
                    "OPENROUTER_APP_TITLE",
                    "Test News",
                ),
            ):
                summary = await provider.summarize(
                    "Article text",
                    context=SummaryContext(
                        url="https://example.com/article",
                        title="Title",
                        source="Example",
                    ),
                )

        self.assertEqual(summary, "Concise summary")

    async def test_openrouter_summary_prompt_accepts_russian_code(self):
        client = AsyncMock()
        client.complete.return_value = "Краткое содержание"
        provider = OpenRouterSummarizer(client)

        with patch.object(stg, "AI_LANGUAGE", "ru"):
            summary = await provider.summarize(
                "Article text",
                context=SummaryContext(
                    url="https://example.com/article",
                ),
            )

        self.assertEqual(summary, "Краткое содержание")
        messages = client.complete.await_args.args[0]
        self.assertIn("in Russian (ru)", messages[0]["content"])

    async def test_openrouter_provider_accepts_content_chunks(self):
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {"type": "text", "text": "First part."},
                                    {"type": "text", "text": "Second part."},
                                ]
                            }
                        }
                    ]
                },
            )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler)
        ) as client:
            provider = OpenRouterSummarizer(
                OpenRouterClient(client)
            )

            with patch.object(
                stg,
                "OPENROUTER_API_KEY",
                SecretStr("test-key"),
            ):
                summary = await provider.summarize(
                    "Article text",
                    context=SummaryContext(url="https://example.com"),
                )

        self.assertEqual(summary, "First part.\nSecond part.")

    async def test_openrouter_retries_200_provider_error(self):
        responses = iter(
            [
                httpx2.Response(
                    200,
                    json={
                        "error": {
                            "code": 429,
                            "message": "Rate limit exceeded",
                            "metadata": {
                                "error_type": "rate_limit_exceeded"
                            },
                        }
                    },
                ),
                httpx2.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"content": "Done"}}
                        ]
                    },
                ),
            ]
        )

        def handler(request: httpx2.Request) -> httpx2.Response:
            return next(responses)

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler)
        ) as client:
            openrouter = OpenRouterClient(client)
            with (
                patch.object(
                    stg,
                    "OPENROUTER_API_KEY",
                    SecretStr("test-key"),
                ),
                patch.object(stg, "AI_MAX_RETRIES", 1),
                patch(
                    "ai.providers.asyncio.sleep",
                    AsyncMock(),
                ) as sleep,
            ):
                result = await openrouter.complete(
                    [{"role": "user", "content": "test"}],
                    max_completion_tokens=10,
                    temperature=0,
                )

        self.assertEqual(result, "Done")
        sleep.assert_awaited_once()

    async def test_openrouter_honors_retry_after(self):
        responses = iter(
            [
                httpx2.Response(
                    429,
                    headers={"retry-after": "2"},
                    json={
                        "error": {
                            "code": 429,
                            "message": "Rate limited",
                        }
                    },
                ),
                httpx2.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"content": "ok"}}
                        ]
                    },
                ),
            ]
        )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: next(responses)
            )
        ) as client:
            openrouter = OpenRouterClient(client)
            with (
                patch.object(
                    stg,
                    "OPENROUTER_API_KEY",
                    SecretStr("test-key"),
                ),
                patch.object(stg, "AI_MAX_RETRIES", 1),
                patch(
                    "ai.providers.asyncio.sleep",
                    AsyncMock(),
                ) as sleep,
            ):
                result = await openrouter.complete(
                    [{"role": "user", "content": "test"}],
                    max_completion_tokens=10,
                    temperature=0,
                )

        self.assertEqual(result, "ok")
        sleep.assert_awaited_once_with(2.0)

    async def test_deepl_uses_one_json_batch_for_news_fields(self):
        requests: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return httpx2.Response(
                200,
                json={
                    "translations": [
                        {
                            "detected_source_language": "RU",
                            "text": "An important new library release",
                        },
                        {
                            "detected_source_language": "RU",
                            "text": (
                                "Developers fixed bugs and improved "
                                "performance."
                            ),
                        },
                    ]
                },
            )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler)
        ) as http_client:
            client = DeepLClient(http_client)
            with (
                patch.object(
                    stg,
                    "DEEPL_API_KEY",
                    SecretStr("test-deepl-key"),
                ),
                patch.object(
                    stg,
                    "DEEPL_TRANSLATE_URL",
                    "https://api-free.deepl.test/v2/translate",
                ),
                patch.object(stg, "PARSER_TRANSLATION_ENABLED", True),
                patch.object(stg, "PARSER_TRANSLATION_REQUIRED", True),
                patch.object(stg, "AI_LANGUAGE", "English"),
            ):
                result = await translate_news(
                    "Вышла важная новая версия библиотеки",
                    "Разработчики исправили ошибки и ускорили загрузку.",
                    client=client,
                )

        self.assertEqual(
            result.translated_fields,
            frozenset({"title", "content"}),
        )
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(
            str(request.url),
            "https://api-free.deepl.test/v2/translate",
        )
        self.assertEqual(
            request.headers["authorization"],
            "DeepL-Auth-Key test-deepl-key",
        )
        payload = orjson.loads(request.content)
        self.assertEqual(
            payload["text"],
            [
                "Вышла важная новая версия библиотеки",
                "Разработчики исправили ошибки и ускорили загрузку.",
            ],
        )
        self.assertEqual(payload["target_lang"], "EN-US")
        self.assertTrue(payload["preserve_formatting"])
        self.assertNotIn("test-deepl-key", request.url.query.decode())
        self.assertNotIn("auth_key", payload)

    async def test_deepl_auto_selects_free_and_pro_endpoints(self):
        free = Config(
            DEEPL_API_KEY=SecretStr("test-key:fx"),
            DEEPL_TRANSLATE_URL=None,
        )
        pro = Config(
            DEEPL_API_KEY=SecretStr("test-key"),
            DEEPL_TRANSLATE_URL=None,
        )

        self.assertEqual(
            free.DEEPL_TRANSLATE_URL,
            "https://api-free.deepl.com/v2/translate",
        )
        self.assertEqual(
            pro.DEEPL_TRANSLATE_URL,
            "https://api.deepl.com/v2/translate",
        )

    def test_deepl_target_language_variants_are_preserved(self):
        cases = {
            "English": "EN-US",
            "en-GB": "EN-GB",
            "Portuguese": "PT-PT",
            "pt-BR": "PT-BR",
            "Chinese": "ZH-HANS",
            "zh-Hant": "ZH-HANT",
            "es-419": "ES-419",
            "Russian": "RU",
            "Catalan": "CA",
        }

        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(
                    resolve_deepl_target_language(value),
                    expected,
                )

    async def test_ai_language_locale_reaches_deepl_unchanged(self):
        client = AsyncMock()
        client.translate.return_value = [
            "A concise summary of the article."
        ]

        with patch.object(stg, "AI_LANGUAGE", "en-GB"):
            result = await translate_text(
                "Краткое содержание статьи.",
                client=client,
            )

        self.assertEqual(
            result["translated_text"],
            "A concise summary of the article.",
        )
        self.assertEqual(result["target_language"], "en")
        client.translate.assert_awaited_once_with(
            ["Краткое содержание статьи."],
            target_language="EN-GB",
        )

    async def test_deepl_honors_retry_after(self):
        responses = iter(
            [
                httpx2.Response(429, headers={"retry-after": "2"}),
                httpx2.Response(
                    200,
                    json={
                        "translations": [
                            {
                                "detected_source_language": "RU",
                                "text": "Translated text",
                            }
                        ]
                    },
                ),
            ]
        )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: next(responses)
            )
        ) as http_client:
            client = DeepLClient(http_client)
            with (
                patch.object(
                    stg,
                    "DEEPL_API_KEY",
                    SecretStr("test-key"),
                ),
                patch.object(stg, "DEEPL_MAX_RETRIES", 1),
                patch(
                    "utils.deepl.asyncio.sleep",
                    AsyncMock(),
                ) as sleep,
            ):
                result = await client.translate(
                    ["Исходный текст"],
                    target_language="en",
                )

        self.assertEqual(result, ["Translated text"])
        sleep.assert_awaited_once_with(2.0)

    async def test_deepl_requires_api_key_before_request(self):
        requests: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return httpx2.Response(200, json={"translations": []})

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler)
        ) as http_client:
            client = DeepLClient(http_client)
            with patch.object(stg, "DEEPL_API_KEY", None):
                with self.assertRaisesRegex(
                    TranslationError,
                    "DEEPL_API_KEY is not configured",
                ):
                    await client.translate(
                        ["Исходный текст"],
                        target_language="EN-US",
                    )

        self.assertEqual(requests, [])

    async def test_required_translation_raises_on_deepl_failure(self):
        client = AsyncMock()
        client.translate.side_effect = TranslationError("offline")
        original = (
            "Это достаточно длинное русское предложение для определения "
            "языка."
        )

        with (
            patch.object(stg, "PARSER_TRANSLATION_REQUIRED", True),
            patch.object(stg, "AI_LANGUAGE", "English"),
        ):
            with self.assertRaises(TranslationError):
                await translate_text(original, client=client)

        client.translate.assert_awaited_once()

    async def test_optional_translation_keeps_original_on_deepl_failure(
        self,
    ):
        client = AsyncMock()
        client.translate.side_effect = TranslationError("offline")
        original = (
            "Это достаточно длинное русское предложение для определения "
            "языка."
        )

        with (
            patch.object(stg, "PARSER_TRANSLATION_REQUIRED", False),
            patch.object(stg, "AI_LANGUAGE", "English"),
        ):
            result = await translate_text(original, client=client)

        self.assertEqual(result["translated_text"], original)
        self.assertEqual(result["target_language"], "en")
        client.translate.assert_awaited_once()

    async def test_target_language_news_does_not_call_deepl(self):
        client = AsyncMock()

        with (
            patch.object(stg, "PARSER_TRANSLATION_ENABLED", True),
            patch.object(stg, "AI_LANGUAGE", "English"),
        ):
            result = await translate_news(
                "A new library version has been released",
                "Developers fixed bugs and improved loading performance.",
                client=client,
            )

        self.assertFalse(result.changed)
        client.translate.assert_not_awaited()

    async def test_technical_product_names_do_not_call_deepl(self):
        client = AsyncMock()

        with patch.object(stg, "AI_LANGUAGE", "English"):
            for value in ("GitHub Copilot", "Docker"):
                with self.subTest(value=value):
                    result = await translate_text(value, client=client)
                    self.assertEqual(result["translated_text"], value)

        client.translate.assert_not_awaited()

    async def test_context_preserves_ambiguous_product_title(self):
        client = AsyncMock()

        with patch.object(stg, "AI_LANGUAGE", "English"):
            result = await translate_news(
                "nginx",
                (
                    "This article explains how nginx improves the "
                    "developer workflow and application setup."
                ),
                client=client,
            )

        self.assertEqual(result.title, "nginx")
        self.assertFalse(result.changed)
        client.translate.assert_not_awaited()

    async def test_deepl_translates_only_foreign_title(self):
        client = AsyncMock()
        client.translate.return_value = [
            "Docker and Kubernetes migration guide"
        ]

        with (
            patch.object(stg, "PARSER_TRANSLATION_ENABLED", True),
            patch.object(stg, "PARSER_TRANSLATION_REQUIRED", True),
            patch.object(stg, "AI_LANGUAGE", "English"),
        ):
            result = await translate_news(
                "Guide de migration pour Docker et Kubernetes",
                (
                    "A popular post from the Hacker News community with "
                    "additional discussion and comments."
                ),
                client=client,
            )

        self.assertEqual(
            result.title,
            "Docker and Kubernetes migration guide",
        )
        self.assertEqual(result.translated_fields, frozenset({"title"}))
        client.translate.assert_awaited_once_with(
            ["Guide de migration pour Docker et Kubernetes"],
            target_language="EN-US",
        )

    async def test_russian_keeps_brand_title_and_translates_content(self):
        client = AsyncMock()
        client.translate.return_value = [
            "Разработчики исправили ошибки и улучшили производительность."
        ]

        with (
            patch.object(stg, "PARSER_TRANSLATION_ENABLED", True),
            patch.object(stg, "PARSER_TRANSLATION_REQUIRED", True),
            patch.object(stg, "AI_LANGUAGE", "Russian"),
        ):
            result = await translate_news(
                "Docker",
                "Developers fixed bugs and improved performance.",
                client=client,
            )

        self.assertEqual(result.title, "Docker")
        self.assertEqual(
            result.content,
            "Разработчики исправили ошибки и улучшили производительность.",
        )
        self.assertEqual(
            result.translated_fields,
            frozenset({"content"}),
        )
        client.translate.assert_awaited_once_with(
            ["Developers fixed bugs and improved performance."],
            target_language="RU",
        )

    async def test_deepl_translation_rejects_wrong_output_language(self):
        client = AsyncMock()
        client.translate.return_value = [
            "Mostly English, но часть осталась."
        ]

        with (
            patch.object(stg, "PARSER_TRANSLATION_REQUIRED", True),
            patch.object(stg, "AI_LANGUAGE", "English"),
        ):
            with self.assertRaises(TranslationError):
                await translate_text(
                    "Исходный русский текст для перевода.",
                    client=client,
                )

        client.translate.assert_awaited_once()

    async def test_short_foreign_latin_input_is_translated_by_deepl(self):
        translations = {
            "Hola mundo": "Hello world",
            "Guten Morgen": "Good morning",
            "Bonjour monde": "Hello world",
            "Ciao mondo": "Hello world",
        }
        with patch.object(stg, "AI_LANGUAGE", "English"):
            for foreign, english in translations.items():
                with self.subTest(foreign=foreign):
                    client = AsyncMock()
                    client.translate.return_value = [english]

                    result = await translate_text(foreign, client=client)

                    self.assertEqual(result["translated_text"], english)
                    client.translate.assert_awaited_once()

    async def test_translation_default_uses_ai_language(self):
        client = AsyncMock()
        client.translate.return_value = ["Краткое содержание статьи."]

        with patch.object(stg, "AI_LANGUAGE", "Russian"):
            result = await translate_text(
                "A concise summary of the article.",
                client=client,
            )

        self.assertEqual(
            result["translated_text"],
            "Краткое содержание статьи.",
        )
        self.assertEqual(result["target_language"], "ru")
        client.translate.assert_awaited_once_with(
            ["A concise summary of the article."],
            target_language="RU",
        )

    async def test_duplicate_news_does_not_call_translator(self):
        with (
            patch.object(
                news_feed_publisher,
                "_claim",
                AsyncMock(return_value=None),
            ),
            patch(
                "bot.services.news_feed.translate_news",
                AsyncMock(),
            ) as translate,
        ):
            published = await news_feed_publisher.publish(
                ParsedNews(
                    title="Title",
                    url="https://example.com/duplicate",
                    source="Example",
                    content="Content",
                    image_urls=(),
                )
            )

        self.assertFalse(published)
        translate.assert_not_awaited()

    async def test_claim_owner_persists_translation_before_send(self):
        news = NewsRaw(
            id=7,
            title="Русский заголовок",
            source="Хабр / Python",
            url="https://example.com/translated",
            content="Русское описание",
            publication_token="lease-token",
            views=0,
            likes=0,
            dislikes=0,
            is_hit=False,
            voting_closed_at=datetime.now(timezone.utc),
        )
        translated = NewsTranslation(
            title="English title",
            content="English description",
            title_language="ru",
            content_language="ru",
            translated_fields=frozenset({"title", "content"}),
        )
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = news.id
        session = AsyncMock()
        session.execute.return_value = execute_result

        class BeginContext:
            async def __aenter__(self):
                return session

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        session_factory = MagicMock()
        session_factory.begin.return_value = BeginContext()

        with (
            patch.object(stg, "AI_LANGUAGE", "English"),
            patch(
                "bot.services.news_feed.translate_news",
                AsyncMock(return_value=translated),
            ) as translate,
            patch(
                "bot.services.news_feed.async_session",
                session_factory,
            ),
        ):
            await news_feed_publisher._translate_claimed_news(
                news,
                "lease-token",
            )

        self.assertEqual(news.title, "English title")
        self.assertEqual(news.content, "English description")
        self.assertEqual(news.source, "Хабр / Python")
        session.execute.assert_awaited_once()
        self.assertEqual(
            translate.await_args.kwargs["target_language"],
            "English",
        )
        self.assertIs(
            translate.await_args.kwargs["client"],
            summary_service.deepl_client,
        )

    async def test_publish_refreshes_claim_before_marking_published(self):
        from bot import bot

        news = NewsRaw(
            id=8,
            title="Title",
            source="Example",
            url="https://example.com/lease",
            content="Description",
            publication_token="lease-token",
            views=0,
            likes=0,
            dislikes=0,
            is_hit=False,
            voting_closed_at=datetime.now(timezone.utc),
        )
        order: list[str] = []

        async def refresh_claim(news_id: int, token: str) -> None:
            order.append("refresh")

        async def mark_published(
            news_id: int,
            token: str,
            message_id: int,
        ) -> None:
            order.append("mark")

        message = MagicMock(message_id=901)
        with (
            patch.object(
                news_feed_publisher,
                "_claim",
                AsyncMock(
                    return_value=PublicationClaim(
                        news=news,
                        token="lease-token",
                    )
                ),
            ),
            patch.object(
                news_feed_publisher,
                "_translate_claimed_news",
                AsyncMock(),
            ),
            patch.object(
                news_feed_publisher,
                "_download_images",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                news_feed_publisher,
                "_refresh_publication_claim",
                refresh_claim,
            ),
            patch.object(
                news_feed_publisher,
                "_mark_published",
                mark_published,
            ),
            patch.object(
                bot,
                "send_message",
                AsyncMock(return_value=message),
            ),
        ):
            published = await news_feed_publisher.publish(
                ParsedNews(
                    title=news.title,
                    url=news.url,
                    source=news.source,
                    content=news.content,
                    image_urls=(),
                )
            )

        self.assertTrue(published)
        self.assertEqual(order, ["refresh", "mark"])

    async def test_lost_heartbeat_cancels_publication_before_send(self):
        news = NewsRaw(
            id=9,
            title="Title",
            source="Example",
            url="https://example.com/lost-lease",
            content="Description",
            publication_token="lease-token",
            views=0,
            likes=0,
            dislikes=0,
            is_hit=False,
            voting_closed_at=datetime.now(timezone.utc),
        )

        async def slow_translation(*args, **kwargs) -> None:
            await asyncio.sleep(5)

        with (
            patch.object(
                news_feed_publisher,
                "_claim",
                AsyncMock(
                    return_value=PublicationClaim(
                        news=news,
                        token="lease-token",
                    )
                ),
            ),
            patch.object(
                news_feed_publisher,
                "_translate_claimed_news",
                slow_translation,
            ),
            patch.object(
                news_feed_publisher,
                "_refresh_publication_claim",
                AsyncMock(
                    side_effect=PublicationClaimLostError("lost")
                ),
            ),
            patch.object(
                news_feed_publisher,
                "_release_claim",
                AsyncMock(),
            ),
            patch.object(
                stg,
                "NEWS_PUBLICATION_LEASE_SECONDS",
                0.3,
            ),
        ):
            with self.assertRaises(PublicationClaimLostError):
                await news_feed_publisher.publish(
                    ParsedNews(
                        title=news.title,
                        url=news.url,
                        source=news.source,
                        content=news.content,
                        image_urls=(),
                    )
                )

    async def test_hacker_news_parser_does_not_prefetch_article_url(self):
        requested_urls: list[str] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            requested_urls.append(str(request.url))
            if request.url.path.endswith("/topstories.json"):
                return httpx2.Response(200, json=[42])
            if request.url.path.endswith("/item/42.json"):
                return httpx2.Response(
                    200,
                    json={
                        "id": 42,
                        "type": "story",
                        "title": "Story",
                        "url": "https://article.example/private",
                        "time": 1_700_000_000,
                    },
                )
            raise AssertionError(f"Unexpected request: {request.url}")

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler)
        ) as client:
            result = await fetch_top_news_with_content(
                limit=1,
                client=client,
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(len(requested_urls), 2)
        self.assertNotIn("article.example", " ".join(requested_urls))

    async def test_stopped_parser_cycle_does_not_publish(self):
        async def collect_and_stop():
            parser_runner._parser_enabled = False
            return [
                {
                    "title": "Title",
                    "url": "https://example.com/1",
                    "source": "Example",
                    "desc": "Description",
                }
            ]

        with (
            patch.object(parser_runner, "_parser_enabled", True),
            patch.object(parser_runner, "_parser_generation", 7),
            patch.object(
                parser_runner,
                "collect_news",
                collect_and_stop,
            ),
            patch.object(
                parser_runner.news_feed_publisher,
                "publish",
                AsyncMock(),
            ) as publish,
        ):
            await parser_runner.run_parser_cycle(generation=7)

        publish.assert_not_awaited()

    async def test_parser_target_validation_accepts_supergroup(self):
        from bot import bot

        with patch.object(
            bot,
            "get_chat",
            AsyncMock(return_value=MagicMock(type=ChatType.SUPERGROUP)),
        ) as get_chat:
            confirmed = await parser_runner.validate_parser_target()

        self.assertTrue(confirmed)
        get_chat.assert_awaited_once_with(
            stg.GROUP_ID,
            request_timeout=stg.PARSER_TARGET_VALIDATION_TIMEOUT,
        )

    async def test_start_parser_callback_uses_configured_group_from_private_chat(
        self,
    ):
        from bot.routers import news as news_router

        callback = MagicMock()
        callback.from_user.id = 42
        callback.message.chat.id = 42
        callback.answer = AsyncMock()
        session = MagicMock()
        session.scalar = AsyncMock(return_value=42)

        with (
            patch.object(
                news_router,
                "validate_parser_target",
                AsyncMock(return_value=False),
            ) as validate,
            patch.object(
                news_router,
                "start_parser",
                return_value=True,
            ) as start,
        ):
            await news_router.start_parse_callback(callback, session)

        validate.assert_awaited_once_with()
        start.assert_called_once_with()
        callback.answer.assert_awaited_once_with(
            "Парсер запущен",
            show_alert=True,
        )

    async def test_parser_target_chat_not_found_is_advisory(self):
        from bot import bot

        telegram_error = TelegramBadRequest(
            method=GetChat(chat_id=stg.GROUP_ID),
            message="Bad Request: chat not found",
        )
        with patch.object(
            bot,
            "get_chat",
            AsyncMock(side_effect=telegram_error),
        ):
            confirmed = await parser_runner.validate_parser_target()

        self.assertFalse(confirmed)

    async def test_parser_target_validation_rejects_channel(self):
        from bot import bot

        with (
            patch.object(
                bot,
                "get_chat",
                AsyncMock(return_value=MagicMock(type="channel")),
            ),
            self.assertRaisesRegex(
                parser_runner.ParserTargetError,
                "группу или супергруппу",
            ),
        ):
            await parser_runner.validate_parser_target()

    async def test_parser_cycle_stops_after_target_chat_not_found(self):
        parsed = [
            {
                "title": "Title",
                "url": "https://example.com/1",
                "source": "Example",
                "desc": "Description",
            },
            {
                "title": "Title 2",
                "url": "https://example.com/2",
                "source": "Example",
                "desc": "Description",
            },
        ]
        telegram_error = TelegramBadRequest(
            method=SendMessage(chat_id=stg.GROUP_ID, text="test"),
            message="Bad Request: chat not found",
        )

        with (
            patch.object(parser_runner, "_parser_enabled", True),
            patch.object(parser_runner, "_parser_generation", 8),
            patch.object(
                parser_runner,
                "collect_news",
                AsyncMock(return_value=parsed),
            ),
            patch.object(
                parser_runner.news_feed_publisher,
                "publish",
                AsyncMock(side_effect=telegram_error),
            ) as publish,
            patch.object(
                parser_runner.scheduler,
                "get_job",
                return_value=None,
            ),
            patch.object(
                parser_runner,
                "stop_parser",
                wraps=parser_runner.stop_parser,
            ) as stop,
        ):
            await parser_runner.run_parser_cycle(generation=8)

        publish.assert_awaited_once()
        stop.assert_called_once_with()

    async def test_parser_cycle_limits_and_sorts_candidates(self):
        now = datetime.now(timezone.utc)
        parsed = [
            {
                "title": f"Title {index}",
                "url": f"https://example.com/{index}",
                "source": "Example",
                "desc": "Description",
                "published_at": now + timedelta(minutes=index),
            }
            for index in range(3)
        ]

        with (
            patch.object(parser_runner, "_parser_enabled", True),
            patch.object(parser_runner, "_parser_generation", 9),
            patch.object(
                parser_runner,
                "collect_news",
                AsyncMock(return_value=parsed),
            ),
            patch.object(
                parser_runner.news_feed_publisher,
                "publish",
                AsyncMock(return_value=True),
            ) as publish,
            patch.object(
                parser_runner.stg,
                "PARSER_MAX_POSTS_PER_CYCLE",
                2,
            ),
            patch.object(
                parser_runner.stg,
                "PARSER_PUBLISH_DELAY_SECONDS",
                0,
            ),
        ):
            await parser_runner.run_parser_cycle(generation=9)

        self.assertEqual(
            [call.args[0].url for call in publish.await_args_list],
            [
                "https://example.com/2",
                "https://example.com/1",
            ],
        )

    async def test_parser_limit_counts_only_newly_published_items(self):
        now = datetime.now(timezone.utc)
        parsed = [
            {
                "title": f"Title {index}",
                "url": f"https://example.com/{index}",
                "source": "Example",
                "desc": "Description",
                "published_at": now - timedelta(minutes=index),
            }
            for index in range(4)
        ]

        with (
            patch.object(parser_runner, "_parser_enabled", True),
            patch.object(parser_runner, "_parser_generation", 10),
            patch.object(
                parser_runner,
                "collect_news",
                AsyncMock(return_value=parsed),
            ),
            patch.object(
                parser_runner.news_feed_publisher,
                "publish",
                AsyncMock(
                    side_effect=[False, True, False, True]
                ),
            ) as publish,
            patch.object(
                parser_runner.stg,
                "PARSER_MAX_POSTS_PER_CYCLE",
                2,
            ),
            patch.object(
                parser_runner.stg,
                "PARSER_PUBLISH_DELAY_SECONDS",
                0,
            ),
        ):
            await parser_runner.run_parser_cycle(generation=10)

        self.assertEqual(publish.await_count, 4)

    async def test_telegram_retry_after_is_respected(self):
        operation = AsyncMock(
            side_effect=[
                TelegramRetryAfter(
                    method=SendMessage(chat_id=1, text="test"),
                    message="retry",
                    retry_after=2,
                ),
                "ok",
            ]
        )

        with (
            patch.object(
                parser_runner.stg,
                "PARSER_TELEGRAM_MAX_RETRIES",
                3,
            ),
            patch(
                "bot.services.news_feed.asyncio.sleep",
                AsyncMock(),
            ) as sleep,
        ):
            result = await news_feed_publisher._telegram_call(operation)

        self.assertEqual(result, "ok")
        self.assertEqual(operation.await_count, 2)
        sleep.assert_awaited_once_with(2.5)

    async def test_final_telegram_retry_gives_up_without_extra_sleep(self):
        errors = [
            TelegramRetryAfter(
                method=SendMessage(chat_id=1, text="test"),
                message="retry",
                retry_after=1,
            )
            for _ in range(2)
        ]
        operation = AsyncMock(side_effect=errors)

        with (
            patch.object(
                parser_runner.stg,
                "PARSER_TELEGRAM_MAX_RETRIES",
                2,
            ),
            patch(
                "bot.services.news_feed.asyncio.sleep",
                AsyncMock(),
            ) as sleep,
        ):
            with self.assertRaises(TelegramRetryAfter):
                await news_feed_publisher._telegram_call(operation)

        sleep.assert_awaited_once_with(1.5)

    async def test_stop_cancels_active_collection(self):
        collection_started = asyncio.Event()

        async def slow_collection():
            collection_started.set()
            await asyncio.Future()

        with (
            patch.object(parser_runner, "_parser_enabled", True),
            patch.object(parser_runner, "_parser_generation", 12),
            patch.object(parser_runner, "_collection_tasks", {}),
            patch.object(parser_runner, "collect_news", slow_collection),
            patch.object(
                parser_runner.scheduler,
                "get_job",
                return_value=None,
            ),
        ):
            cycle = asyncio.create_task(
                parser_runner.run_parser_cycle(generation=12)
            )
            await collection_started.wait()
            self.assertTrue(parser_runner.stop_parser())
            await asyncio.wait_for(cycle, timeout=1)

        self.assertTrue(cycle.done())


if __name__ == "__main__":
    unittest.main()
