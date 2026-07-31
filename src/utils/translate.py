from dataclasses import dataclass
import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from lingua import Language, LanguageDetectorBuilder

from ai.errors import TranslationError
from core.config import logger, stg
from utils.language import (
    resolve_deepl_target_language,
    resolve_language,
)

if TYPE_CHECKING:
    from utils.deepl import DeepLClient


_LETTER_PATTERN = re.compile(r"[^\W\d_]", flags=re.UNICODE)
_CYRILLIC_PATTERN = re.compile(
    r"[\u0400-\u052f\u2de0-\u2dff\ua640-\ua69f]"
)
_JAPANESE_PATTERN = re.compile(r"[\u3040-\u30ff]")
_KOREAN_PATTERN = re.compile(r"[\uac00-\ud7af]")
_HAN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_TECHNICAL_TOKEN_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z0-9+#._-]*"
)
_TECHNICAL_ENGLISH_WORDS = frozenset(
    """
    a action actions algorithm algorithms an and api application applications
    async asynchronous available build built c cli code codebase context
    copilot database databases developer developers development docker dto
    framework from git github gitlab go guide hacker http httpx important in
    into java javascript kernel kotlin kubernetes laravel library libraries
    linux migration model models module modules my new news node npm of open
    openai openrouter package packages parser post postgresql python react
    redis release released request requests resource resources rust server
    sqlalchemy summary swift telegram the this to tool tools trafilatura
    typescript update updated updates using version vue web with without
    """.split()
)
_LANGUAGE_NEUTRAL_TECHNICAL_NAMES = frozenset(
    {
        "api",
        "astro",
        "claude",
        "copilot",
        "deepl",
        "django",
        "docker",
        "fastapi",
        "gemini",
        "git",
        "github",
        "gitlab",
        "grafana",
        "http",
        "httpx",
        "java",
        "javascript",
        "kotlin",
        "kubernetes",
        "laravel",
        "linux",
        "mistral",
        "nextjs",
        "nginx",
        "node",
        "npm",
        "openai",
        "openrouter",
        "postgresql",
        "prometheus",
        "pydantic",
        "python",
        "react",
        "redis",
        "rust",
        "sqlalchemy",
        "svelte",
        "swift",
        "telegram",
        "terraform",
        "trafilatura",
        "typescript",
        "vue",
    }
)
_LANGUAGE_DETECTOR = LanguageDetectorBuilder.from_languages(
    Language.CATALAN,
    Language.DANISH,
    Language.DUTCH,
    Language.ENGLISH,
    Language.FRENCH,
    Language.GERMAN,
    Language.ITALIAN,
    Language.PORTUGUESE,
    Language.ROMANIAN,
    Language.RUSSIAN,
    Language.SPANISH,
    Language.SWEDISH,
    Language.UKRAINIAN,
).build()
# Load the selected FSTs before the bot's event loop starts.
_LANGUAGE_DETECTOR.detect_language_of(
    "Initialize the short English text detector."
)
@dataclass(frozen=True, slots=True)
class NewsTranslation:
    title: str
    content: str
    title_language: str
    content_language: str
    translated_fields: frozenset[str]

    @property
    def changed(self) -> bool:
        return bool(self.translated_fields)


def detect_language(text: str) -> str:
    """Return an ISO 639-1 language code, or ``unknown``."""
    clean = text.strip()
    if not clean:
        return "unknown"
    if _JAPANESE_PATTERN.search(clean) is not None:
        return "ja"
    if _KOREAN_PATTERN.search(clean) is not None:
        return "ko"
    if _HAN_PATTERN.search(clean) is not None:
        return "zh"

    language = _LANGUAGE_DETECTOR.detect_language_of(clean)
    if language is None:
        return "unknown"
    return language.iso_code_639_1.name.casefold()


async def translate_text(
    text: str,
    target_language: str | None = None,
    *,
    client: "DeepLClient | None" = None,
) -> dict[str, str]:
    """Detect and translate one text through DeepL over HTTPX2.

    The return shape intentionally matches the original utility. When
    ``PARSER_TRANSLATION_REQUIRED`` is enabled, an API failure raises
    ``TranslationError`` instead of leaking foreign text into the feed.
    """
    requested_target = target_language or stg.AI_LANGUAGE
    target = resolve_language(requested_target).code
    source_language = detect_language(text)
    translated_text = text
    if _needs_translation(text, source_language, target):
        translated = await _translate_fields(
            {"text": _clip(text, stg.PARSER_TRANSLATION_MAX_CHARS)},
            target_language=requested_target,
            client=client,
        )
        translated_text = translated.get("text", text)

    return {
        "original": text,
        "detected_language": source_language,
        "translated_text": translated_text,
        "target_language": target,
    }


async def translate_news(
    title: str,
    content: str,
    *,
    client: "DeepLClient | None" = None,
    target_language: str | None = None,
    context_url: str | None = None,
) -> NewsTranslation:
    """Translate only foreign fields, keeping title/content independent."""
    title_language = detect_language(title)
    content_language = detect_language(content)
    requested_target = target_language or stg.AI_LANGUAGE
    target = resolve_language(requested_target).code

    if not stg.PARSER_TRANSLATION_ENABLED:
        return NewsTranslation(
            title=title,
            content=content,
            title_language=title_language,
            content_language=content_language,
            translated_fields=frozenset(),
        )

    pending: dict[str, str] = {}
    content_needs_translation = _needs_translation(
        content,
        content_language,
        target,
    )
    title_needs_translation = _needs_translation(
        title,
        title_language,
        target,
    )
    if (
        title_needs_translation
        and (
            (
                not content_needs_translation
                and _language_code(target) == "en"
                and _is_technical_title_supported_by_context(
                    title,
                    content,
                    context_url=context_url,
                )
            )
            or _is_language_neutral_technical_title(
                title,
                content,
                context_url=context_url,
            )
        )
    ):
        title_needs_translation = False

    if title_needs_translation:
        pending["title"] = _clip(title, 500)
    if content_needs_translation:
        pending["content"] = _clip(
            content,
            stg.PARSER_TRANSLATION_MAX_CHARS,
        )

    translations = await _translate_fields(
        pending,
        target_language=requested_target,
        client=client,
    )
    translated_fields = frozenset(
        field
        for field, translated in translations.items()
        if translated and translated != {"title": title, "content": content}[field]
    )
    return NewsTranslation(
        title=translations.get("title", title),
        content=translations.get("content", content),
        title_language=title_language,
        content_language=content_language,
        translated_fields=translated_fields,
    )


async def _translate_fields(
    fields: dict[str, str],
    *,
    target_language: str,
    client: "DeepLClient | None",
) -> dict[str, str]:
    if not fields:
        return {}

    target = resolve_language(target_language)
    target_code = target.code
    target_name = target.name
    if client is None:
        # Delayed import avoids an ai.service -> utils.translate cycle.
        from ai.service import summary_service

        client = summary_service.deepl_client

    field_names = list(fields)
    try:
        translated_values = await client.translate(
            [fields[field] for field in field_names],
            target_language=resolve_deepl_target_language(
                target_language
            ),
        )
        if len(translated_values) != len(field_names):
            raise TranslationError(
                "DeepL returned an unexpected number of translations"
            )
        return _validated_translations(
            dict(zip(field_names, translated_values, strict=True)),
            fields,
            target_code=target_code,
        )
    except TranslationError as error:
        if stg.PARSER_TRANSLATION_REQUIRED:
            raise TranslationError(
                f"{target_name} translation is required but failed"
            ) from error
        logger.warning(
            "Translation failed; publishing original text: %s", error,
        )
        return {}


def _validated_translations(
    translations: dict,
    source_fields: dict[str, str],
    *,
    target_code: str,
) -> dict[str, str]:
    validated: dict[str, str] = {}
    for field, source_text in source_fields.items():
        value = translations.get(field)
        if not isinstance(value, str) or not value.strip():
            raise TranslationError(
                f"DeepL omitted translated field {field!r}"
            )
        clean = value.strip()
        if clean == source_text.strip():
            raise TranslationError(
                f"DeepL did not translate field {field!r}"
            )
        translated_language = detect_language(clean)
        if not _matches_target_language(
            clean,
            translated_language,
            target_code,
        ):
            raise TranslationError(
                f"DeepL returned field {field!r} in "
                f"{translated_language}, expected {target_code}"
            )
        validated[field] = clean
    return validated


def _needs_translation(
    text: str,
    detected_language: str,
    target_language: str,
) -> bool:
    if not text.strip():
        return False
    target_code = _language_code(target_language)
    if target_code == "en":
        return not _is_likely_english(text)
    if detected_language.casefold() == target_code:
        return False

    if detected_language != "unknown":
        return True

    # Very short titles often cannot be classified. Translate them only when
    # they contain at least three letters, and leave numbers/symbols alone.
    return len(_LETTER_PATTERN.findall(text)) >= 3


def _matches_target_language(
    text: str,
    detected_language: str,
    target_code: str,
) -> bool:
    if target_code == "en":
        return _is_likely_english(text)
    if detected_language.casefold() == target_code:
        return True

    return detected_language == "unknown" and not _needs_translation(
        text,
        detected_language,
        target_code,
    )


def _is_likely_english(text: str) -> bool:
    if _CYRILLIC_PATTERN.search(text) is not None:
        return False
    letters = _LETTER_PATTERN.findall(text)
    if not letters:
        return True

    if (
        _LANGUAGE_DETECTOR.detect_language_of(text)
        == Language.ENGLISH
    ):
        return True
    return _is_technical_english_phrase(text)


def _is_technical_english_phrase(text: str) -> bool:
    letters = _LETTER_PATTERN.findall(text)
    if (
        not letters
        or len(letters) >= 120
        or not all(letter.isascii() for letter in letters)
    ):
        return False

    tokens = _TECHNICAL_TOKEN_PATTERN.findall(text)
    if not tokens:
        return False
    return all(_is_technical_english_token(token) for token in tokens)


def _is_technical_title_supported_by_context(
    title: str,
    content: str,
    *,
    context_url: str | None,
) -> bool:
    """Treat ambiguous product names as language-neutral when evidenced.

    A short identifier such as ``nginx`` is not reliably classifiable as a
    natural language. It is safe to preserve when the already-English
    description mentions it, or when it matches the article hostname.
    URL paths are deliberately ignored because they commonly contain a
    transliterated copy of a foreign title.
    """
    letters = _LETTER_PATTERN.findall(title)
    if (
        not letters
        or not all(letter.isascii() for letter in letters)
        or _CYRILLIC_PATTERN.search(title) is not None
    ):
        return False

    title_tokens = _TECHNICAL_TOKEN_PATTERN.findall(title)
    if not title_tokens:
        return False

    content_identifiers = {
        _normalize_identifier(token)
        for token in _TECHNICAL_TOKEN_PATTERN.findall(content)
    }
    hostname = ""
    if context_url:
        hostname = (urlsplit(context_url).hostname or "").casefold()
    hostname_labels = {
        _normalize_identifier(label)
        for label in re.split(r"[^a-z0-9]+", hostname)
        if label
    }

    for token in title_tokens:
        if _is_technical_english_token(token):
            continue
        identifier = _normalize_identifier(token)
        if not identifier:
            return False
        if identifier in content_identifiers:
            continue
        if identifier in hostname_labels:
            continue
        if len(identifier) >= 4 and any(
            identifier in label for label in hostname_labels
        ):
            continue
        return False
    return True


def _is_language_neutral_technical_title(
    title: str,
    content: str,
    *,
    context_url: str | None,
) -> bool:
    """Preserve short product-only titles in every target language."""
    if len(title) > 80 or _CYRILLIC_PATTERN.search(title) is not None:
        return False
    letters = _LETTER_PATTERN.findall(title)
    if not letters or not all(letter.isascii() for letter in letters):
        return False

    tokens = _TECHNICAL_TOKEN_PATTERN.findall(title)
    if not tokens or len(tokens) > 3:
        return False
    content_identifiers = {
        _normalize_identifier(token)
        for token in _TECHNICAL_TOKEN_PATTERN.findall(content)
    }
    hostname = (
        (urlsplit(context_url).hostname or "").casefold()
        if context_url
        else ""
    )
    hostname_identifiers = {
        _normalize_identifier(label)
        for label in re.split(r"[^a-z0-9]+", hostname)
        if label
    }

    for token in tokens:
        identifier = _normalize_identifier(token)
        if identifier in _LANGUAGE_NEUTRAL_TECHNICAL_NAMES:
            continue
        evidenced = (
            identifier in content_identifiers
            or identifier in hostname_identifiers
        )
        has_technical_shape = (
            any(character.isdigit() for character in token)
            or any(character.isupper() for character in token[1:])
            or any(character in "+#._-" for character in token)
            or token.isupper()
        )
        if (
            evidenced
            and identifier not in _TECHNICAL_ENGLISH_WORDS
            and has_technical_shape
        ):
            continue
        return False
    return True


def _is_technical_english_token(token: str) -> bool:
    return (
        token.casefold() in _TECHNICAL_ENGLISH_WORDS
        or any(character.isdigit() for character in token)
        or (
            len(token) > 1
            and (
                token.isupper()
                or any(character.isupper() for character in token[1:])
            )
        )
    )


def _normalize_identifier(token: str) -> str:
    return "".join(
        character
        for character in token.casefold()
        if character.isascii() and character.isalnum()
    )


def _language_code(language: str) -> str:
    return resolve_language(language).code


def _clip(text: str, limit: int) -> str:
    clean = text.strip()
    if len(clean) <= limit:
        return clean
    shortened = clean[: limit - 1].rsplit(" ", 1)[0].rstrip()
    if not shortened:
        shortened = clean[: limit - 1].rstrip()
    return f"{shortened}…"
