import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random

import httpx2

from ai.errors import TranslationError
from core.config import logger, stg
from utils.language import resolve_deepl_target_language, resolve_language


_RETRYABLE_HTTP_STATUSES = {
    408,
    429,
    500,
    502,
    503,
    504,
    529,
}


class DeepLClient:
    def __init__(self, client: httpx2.AsyncClient):
        self.client = client
        self._semaphore = asyncio.Semaphore(
            max(1, stg.DEEPL_MAX_CONCURRENCY)
        )

    async def translate(
        self,
        texts: list[str],
        *,
        target_language: str,
    ) -> list[str]:
        if not texts:
            return []

        api_key = (
            stg.DEEPL_API_KEY.get_secret_value()
            if stg.DEEPL_API_KEY is not None
            else ""
        )

        if api_key:
            payload = {
                "text": texts,
                "target_lang": resolve_deepl_target_language(
                    target_language
                ),
                "preserve_formatting": True,
            }
            headers = {
                "Authorization": f"DeepL-Auth-Key {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            response = await self._post_with_retries(
                headers=headers,
                payload=payload,
            )
            return _translation_texts(response, expected_count=len(texts))

        return await self._translate_with_google(
            texts, target_language=target_language,
        )

    async def _translate_with_google(
        self,
        texts: list[str],
        *,
        target_language: str,
    ) -> list[str]:
        from deep_translator import GoogleTranslator
        from langdetect import detect

        target = resolve_language(target_language).code
        results: list[str] = []

        for text in texts:
            try:
                source = detect(text)
                if source == target:
                    results.append(text)
                    continue
                loop = asyncio.get_running_loop()
                translated = await loop.run_in_executor(
                    None,
                    lambda s=source, t=target, txt=text: (
                        GoogleTranslator(source=s, target=t).translate(txt)
                    ),
                )
                if not translated or not str(translated).strip():
                    raise TranslationError(
                        "GoogleTranslator returned empty result"
                    )
                results.append(str(translated).strip())
            except TranslationError:
                raise
            except Exception as error:
                raise TranslationError(
                    f"GoogleTranslator failed: {error}"
                ) from error

        return results

    async def _post_with_retries(
        self,
        *,
        headers: dict[str, str],
        payload: dict,
    ) -> object:
        attempts = max(1, stg.DEEPL_MAX_RETRIES + 1)
        last_error: Exception | None = None

        for attempt in range(attempts):
            response: httpx2.Response | None = None
            try:
                async with self._semaphore:
                    response = await self.client.post(
                        stg.DEEPL_TRANSLATE_URL,
                        headers=headers,
                        json=payload,
                        timeout=stg.DEEPL_REQUEST_TIMEOUT,
                    )
                if response.status_code in _RETRYABLE_HTTP_STATUSES:
                    last_error = TranslationError(
                        "DeepL returned HTTP "
                        f"{response.status_code}"
                    )
                elif response.is_error:
                    raise TranslationError(
                        "DeepL returned HTTP "
                        f"{response.status_code}"
                    )
                else:
                    try:
                        return response.json()
                    except ValueError as error:
                        raise TranslationError(
                            "DeepL returned invalid JSON"
                        ) from error
            except (httpx2.TimeoutException, httpx2.TransportError) as error:
                last_error = error

            if attempt + 1 >= attempts:
                break
            await asyncio.sleep(
                _retry_delay(response, attempt=attempt)
            )

        raise TranslationError(
            f"DeepL is unavailable after {attempts} attempt(s)"
        ) from last_error


def _translation_texts(
    data: object,
    *,
    expected_count: int,
) -> list[str]:
    if not isinstance(data, dict):
        raise TranslationError("DeepL returned an invalid response")
    translations = data.get("translations")
    if (
        not isinstance(translations, list)
        or len(translations) != expected_count
    ):
        raise TranslationError(
            "DeepL returned an unexpected number of translations"
        )

    result: list[str] = []
    for translation in translations:
        if not isinstance(translation, dict):
            raise TranslationError(
                "DeepL returned an invalid translation"
            )
        text = translation.get("text")
        if not isinstance(text, str) or not text.strip():
            raise TranslationError(
                "DeepL returned an empty translation"
            )
        result.append(text.strip())
    return result


def _retry_delay(
    response: httpx2.Response | None,
    *,
    attempt: int,
) -> float:
    if response is not None and response.status_code in {429, 503}:
        parsed_delay = _parse_retry_after(
            response.headers.get("retry-after")
        )
        if parsed_delay is not None:
            return parsed_delay
    return min((2 ** attempt) + random.random(), 30.0)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        seconds = (
            retry_at - datetime.now(timezone.utc)
        ).total_seconds()
    return max(0.0, min(seconds, 60.0))
