import asyncio
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx2
import trafilatura

from ai.errors import ArticleExtractionError, ArticleFetchError
from core.config import logger, stg


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_extraction_pool: ProcessPoolExecutor | None = None
_article_slots = asyncio.Semaphore(
    max(1, stg.ARTICLE_MAX_CONCURRENCY)
)
_extraction_slots = asyncio.Semaphore(
    max(1, stg.ARTICLE_EXTRACT_WORKERS)
)


async def fetch_article_html(
    url: str,
    client: httpx2.AsyncClient,
) -> bytes:
    current_url = url

    for _ in range(5):
        await validate_public_url(current_url)
        try:
            async with client.stream(
                "GET",
                current_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; MyNewsBot/1.0; "
                        "+https://github.com/)"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                },
                follow_redirects=False,
                timeout=stg.ARTICLE_FETCH_TIMEOUT,
            ) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise ArticleFetchError(
                            "The article returned an empty redirect"
                        )
                    current_url = urljoin(str(response.url), location)
                    continue

                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not (
                    content_type.startswith("text/html")
                    or content_type.startswith("application/xhtml+xml")
                ):
                    raise ArticleFetchError(
                        f"Unsupported article content type: {content_type}"
                    )

                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > stg.ARTICLE_MAX_BYTES:
                        raise ArticleFetchError(
                            "The article page is too large"
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
        except ArticleFetchError:
            raise
        except (httpx2.HTTPError, OSError) as error:
            raise ArticleFetchError(
                "Could not download the article"
            ) from error

    raise ArticleFetchError("The article redirected too many times")


async def extract_article_text(
    url: str,
    client: httpx2.AsyncClient,
) -> str:
    async with _article_slots:
        html = await fetch_article_html(url, client)
        loop = asyncio.get_running_loop()
        try:
            async with _extraction_slots:
                text = await asyncio.wait_for(
                    loop.run_in_executor(
                        _get_extraction_pool(),
                        _extract_article,
                        html,
                        url,
                    ),
                    timeout=stg.ARTICLE_EXTRACT_TIMEOUT,
                )
        except TimeoutError as error:
            close_extraction_pool(terminate=True)
            raise ArticleExtractionError(
                "Trafilatura extraction timed out"
            ) from error
        except BrokenProcessPool as error:
            close_extraction_pool(terminate=True)
            raise ArticleExtractionError(
                "Trafilatura worker stopped unexpectedly"
            ) from error
        except OSError:
            # Some restricted containers forbid the IPC socket used by
            # ProcessPoolExecutor. Normal deployments use the process worker.
            close_extraction_pool(terminate=True)
            logger.warning(
                "Process extraction is unavailable; using local Trafilatura"
            )
            try:
                text = _extract_article(html, url)
            except Exception as error:
                raise ArticleExtractionError(
                    "Trafilatura could not process the article"
                ) from error
        except Exception as error:
            raise ArticleExtractionError(
                "Trafilatura could not process the article"
            ) from error
        if not text or not text.strip():
            raise ArticleExtractionError(
                "Trafilatura could not find the main article text"
            )
        return text.strip()[: stg.ARTICLE_MAX_CHARS]


def close_extraction_pool(*, terminate: bool = False) -> None:
    global _extraction_pool

    pool = _extraction_pool
    _extraction_pool = None
    if pool is None:
        return

    if terminate:
        terminate_workers = getattr(pool, "terminate_workers", None)
        if terminate_workers is not None:
            try:
                terminate_workers()
                return
            except Exception:
                logger.exception(
                    "Failed to terminate the Trafilatura worker"
                )
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        logger.exception("Failed to shut down the Trafilatura worker")


def _get_extraction_pool() -> ProcessPoolExecutor:
    global _extraction_pool

    if _extraction_pool is None:
        _extraction_pool = ProcessPoolExecutor(
            max_workers=max(1, stg.ARTICLE_EXTRACT_WORKERS),
        )
    return _extraction_pool


def _extract_article(html: bytes, url: str) -> str | None:
    return trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        include_links=False,
        include_images=False,
        include_formatting=False,
        deduplicate=True,
        favor_precision=True,
    )


async def validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ArticleFetchError("Only public HTTP(S) article URLs are allowed")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ArticleFetchError("Local article URLs are not allowed")

    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            info = await asyncio.get_running_loop().getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as error:
            raise ArticleFetchError(
                "Could not resolve the article hostname"
            ) from error
        addresses = {
            ipaddress.ip_address(item[4][0])
            for item in info
        }

    if not addresses or any(not address.is_global for address in addresses):
        raise ArticleFetchError(
            "Private or reserved article addresses are not allowed"
        )
