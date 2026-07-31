import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable, TypeVar
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx2
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import (
    BufferedInputFile,
    InputMediaPhoto,
    LinkPreviewOptions,
)
from sqlalchemy import select, text, update

from ai.article import validate_public_url
from ai.service import summary_service
from bot.news_ui import (
    format_news_card,
    news_keyboard,
    normalize_source_label,
)
from core.config import logger, stg
from core.db.db import async_session
from core.db.models import NewsRaw
from utils.translate import translate_news


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ParsedNews:
    title: str
    url: str
    source: str
    content: str
    image_urls: tuple[str, ...]

    @classmethod
    def from_mapping(cls, item: dict[str, Any]) -> "ParsedNews":
        url = str(item.get("url") or "").strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("News URL must be HTTP(S)")
        if len(url) > 1_500:
            raise ValueError("News URL is too long")

        title = str(item.get("title") or "").strip()
        if not title:
            raise ValueError("News title is empty")

        content = str(
            item.get("desc")
            or item.get("content")
            or title
        ).strip()
        source = str(
            item.get("source") or "Неизвестный источник"
        ).strip()

        images: list[str] = []
        for raw_url in item.get("img_links") or ():
            image_url = urljoin(url, str(raw_url).strip())
            image_parts = urlsplit(image_url)
            if (
                image_parts.scheme in {"http", "https"}
                and image_parts.netloc
                and image_url not in images
            ):
                images.append(image_url)

        return cls(
            title=title,
            url=url,
            source=source,
            content=content,
            image_urls=tuple(images[: stg.PARSER_MAX_IMAGES]),
        )


@dataclass(frozen=True, slots=True)
class PublicationClaim:
    news: NewsRaw
    token: str


class PublicationClaimLostError(RuntimeError):
    pass


class NewsFeedPublisher:
    def __init__(self) -> None:
        self._client = httpx2.AsyncClient(
            follow_redirects=False,
            trust_env=False,
        )

    async def publish(self, item: ParsedNews) -> bool:
        claim = await self._claim(item)
        if claim is None:
            return False
        news = claim.news

        from bot import bot

        sent_message_ids: list[int] = []
        owner_task = asyncio.current_task()
        if owner_task is None:
            raise RuntimeError("News publication must run in an asyncio task")
        heartbeat_stop = asyncio.Event()
        heartbeat_failure: asyncio.Future[Exception] = (
            asyncio.get_running_loop().create_future()
        )
        heartbeat_task = asyncio.create_task(
            self._maintain_publication_claim(
                news.id,
                claim.token,
                stop=heartbeat_stop,
                owner_task=owner_task,
                failure=heartbeat_failure,
            ),
            name=f"news-publication-heartbeat:{news.id}",
        )
        try:
            await self._translate_claimed_news(news, claim.token)
            images = await self._download_images(item.image_urls)
            try:
                if len(images) == 1:
                    photo_message = await self._telegram_call(
                        lambda: bot.send_photo(
                            chat_id=stg.GROUP_ID,
                            photo=images[0],
                        )
                    )
                    sent_message_ids.append(photo_message.message_id)
                elif len(images) >= 2:
                    media_messages = await self._telegram_call(
                        lambda: bot.send_media_group(
                            chat_id=stg.GROUP_ID,
                            media=[
                                InputMediaPhoto(media=image)
                                for image in images
                            ],
                        )
                    )
                    sent_message_ids.extend(
                        message.message_id
                        for message in media_messages
                    )
            except Exception:
                # A broken image must not prevent the card from being sent.
                logger.exception(
                    "Failed to send images for news %s",
                    news.id,
                )

            message = await self._telegram_call(
                lambda: bot.send_message(
                    chat_id=stg.GROUP_ID,
                    text=format_news_card(news),
                    reply_markup=news_keyboard(
                        news.id,
                        news.likes,
                        news.dislikes,
                    ),
                    link_preview_options=LinkPreviewOptions(
                        is_disabled=True
                    ),
                )
            )
            sent_message_ids.append(message.message_id)
            # Give _mark_published() a full lease window, then stop the
            # heartbeat before that method intentionally clears the token.
            await self._refresh_publication_claim(news.id, claim.token)
            await self._stop_publication_heartbeat(
                heartbeat_task,
                heartbeat_stop,
            )
            await self._mark_published(
                news.id,
                claim.token,
                message.message_id,
            )
        except (Exception, asyncio.CancelledError) as error:
            await self._stop_publication_heartbeat(
                heartbeat_task,
                heartbeat_stop,
            )
            cleaned_up = await self._cleanup_messages(
                bot,
                sent_message_ids,
            )
            if cleaned_up:
                try:
                    await self._release_claim(news.id, claim.token)
                except Exception:
                    logger.exception(
                        "Failed to release publication claim for news %s",
                        news.id,
                    )
            else:
                logger.error(
                    "Keeping publication lease for news %s because Telegram "
                    "cleanup was incomplete",
                    news.id,
                )
            if (
                isinstance(error, asyncio.CancelledError)
                and heartbeat_failure.done()
            ):
                raise heartbeat_failure.result() from error
            raise
        finally:
            await self._stop_publication_heartbeat(
                heartbeat_task,
                heartbeat_stop,
            )

        logger.info("Published news %s to the parser group", news.id)
        return True

    async def _maintain_publication_claim(
        self,
        news_id: int,
        token: str,
        *,
        stop: asyncio.Event,
        owner_task: asyncio.Task,
        failure: asyncio.Future[Exception],
    ) -> None:
        interval = max(
            0.2,
            min(stg.NEWS_PUBLICATION_LEASE_SECONDS / 3, 60.0),
        )
        try:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(
                        stop.wait(),
                        timeout=interval,
                    )
                    return
                except TimeoutError:
                    pass
                await self._refresh_publication_claim(news_id, token)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not failure.done():
                failure.set_result(error)
            owner_task.cancel()

    async def _refresh_publication_claim(
        self,
        news_id: int,
        token: str,
    ) -> None:
        async with async_session.begin() as session:
            result = await session.execute(
                update(NewsRaw)
                .where(
                    NewsRaw.id == news_id,
                    NewsRaw.publication_token == token,
                    NewsRaw.telegram_msg_id.is_(None),
                )
                .values(
                    publishing_started_at=datetime.now(timezone.utc),
                )
                .returning(NewsRaw.id)
            )
            if result.scalar_one_or_none() is None:
                raise PublicationClaimLostError(
                    f"Publication claim for news {news_id} was lost"
                )

    async def _stop_publication_heartbeat(
        self,
        task: asyncio.Task,
        stop: asyncio.Event,
    ) -> None:
        stop.set()
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _translate_claimed_news(
        self,
        news: NewsRaw,
        token: str,
    ) -> None:
        translation = await translate_news(
            news.title,
            news.content,
            client=summary_service.deepl_client,
            target_language=stg.AI_LANGUAGE,
            context_url=news.url,
        )
        normalized_source = normalize_source_label(news.source)
        source_changed = normalized_source != news.source
        if not translation.changed and not source_changed:
            return

        async with async_session.begin() as session:
            result = await session.execute(
                update(NewsRaw)
                .where(
                    NewsRaw.id == news.id,
                    NewsRaw.publication_token == token,
                    NewsRaw.telegram_msg_id.is_(None),
                )
                .values(
                    title=translation.title,
                    content=translation.content,
                    source=normalized_source,
                )
                .returning(NewsRaw.id)
            )
            if result.scalar_one_or_none() is None:
                raise RuntimeError(
                    f"Publication claim for news {news.id} was lost "
                    "during translation"
                )

        news.title = translation.title
        news.content = translation.content
        news.source = normalized_source
        changed_fields = set(translation.translated_fields)
        if source_changed:
            changed_fields.add("source")
        logger.info(
            "Normalized news %s fields=%s languages=%s/%s",
            news.id,
            ",".join(sorted(changed_fields)),
            translation.title_language,
            translation.content_language,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _claim(
        self,
        item: ParsedNews,
    ) -> PublicationClaim | None:
        async with async_session.begin() as session:
            # A transaction-scoped PostgreSQL advisory lock prevents two
            # bot processes from inserting the same RSS URL concurrently.
            await session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:url, 0))"
                ),
                {"url": item.url},
            )
            news = await session.scalar(
                select(NewsRaw)
                .where(NewsRaw.url == item.url)
                .order_by(NewsRaw.id)
                .limit(1)
                .with_for_update()
            )
            now = datetime.now(timezone.utc)
            if news is None:
                news = NewsRaw(
                    url=item.url,
                    source=item.source,
                    title=item.title,
                    content=item.content,
                    views=0,
                    likes=0,
                    dislikes=0,
                    is_hit=False,
                    voting_closed_at=now + timedelta(
                        hours=stg.PARSER_VOTING_HOURS
                    ),
                )
                session.add(news)
                await session.flush()

            if news.telegram_msg_id is not None:
                return None

            lease_cutoff = now - timedelta(
                seconds=stg.NEWS_PUBLICATION_LEASE_SECONDS
            )
            if (
                news.publication_token
                and news.publishing_started_at
                and news.publishing_started_at > lease_cutoff
            ):
                return None

            token = uuid4().hex
            news.publication_token = token
            news.publishing_started_at = now
            await session.flush()
            return PublicationClaim(news=news, token=token)

    async def _mark_published(
        self,
        news_id: int,
        token: str,
        telegram_message_id: int,
    ) -> None:
        async with async_session.begin() as session:
            result = await session.execute(
                update(NewsRaw)
                .where(
                    NewsRaw.id == news_id,
                    NewsRaw.publication_token == token,
                    NewsRaw.telegram_msg_id.is_(None),
                )
                .values(
                    telegram_msg_id=telegram_message_id,
                    publication_token=None,
                    publishing_started_at=None,
                )
                .returning(NewsRaw.id)
            )
            if result.scalar_one_or_none() is None:
                raise RuntimeError(
                    f"Publication claim for news {news_id} was lost"
                )

    async def _release_claim(self, news_id: int, token: str) -> None:
        async with async_session.begin() as session:
            await session.execute(
                update(NewsRaw)
                .where(
                    NewsRaw.id == news_id,
                    NewsRaw.publication_token == token,
                    NewsRaw.telegram_msg_id.is_(None),
                )
                .values(
                    publication_token=None,
                    publishing_started_at=None,
                )
            )

    async def _telegram_call(
        self,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        attempts = max(1, stg.PARSER_TELEGRAM_MAX_RETRIES)
        for attempt in range(attempts):
            try:
                return await operation()
            except TelegramRetryAfter as error:
                if attempt + 1 >= attempts:
                    raise
                delay = max(0.0, float(error.retry_after)) + 0.5
                logger.warning(
                    "Telegram flood control: retrying in %.1f seconds",
                    delay,
                )
                await asyncio.sleep(delay)
        raise RuntimeError("Telegram retry loop exited unexpectedly")

    async def _cleanup_messages(
        self,
        bot,
        message_ids: list[int],
    ) -> bool:
        cleaned_up = True
        for message_id in reversed(message_ids):
            try:
                await self._telegram_call(
                    lambda message_id=message_id: bot.delete_message(
                        chat_id=stg.GROUP_ID,
                        message_id=message_id,
                    )
                )
            except Exception:
                cleaned_up = False
                logger.exception(
                    "Failed to clean up Telegram message %s",
                    message_id,
                )
        return cleaned_up

    async def _download_images(
        self,
        image_urls: tuple[str, ...],
    ) -> list[BufferedInputFile]:
        results = await asyncio.gather(
            *(self._download_image(url) for url in image_urls),
            return_exceptions=True,
        )
        images: list[BufferedInputFile] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Skipping an RSS image: %s", result)
            elif result is not None:
                images.append(result)
        return images

    async def _download_image(
        self,
        url: str,
    ) -> BufferedInputFile | None:
        current_url = url
        for _ in range(4):
            await validate_public_url(current_url)
            async with self._client.stream(
                "GET",
                current_url,
                headers={"User-Agent": "MyNewsBot/1.0"},
                follow_redirects=False,
                timeout=stg.TIMEOUT_FETCH,
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        return None
                    current_url = urljoin(str(response.url), location)
                    continue

                response.raise_for_status()
                content_type = response.headers.get(
                    "content-type",
                    "",
                ).split(";", 1)[0].lower()
                if not content_type.startswith("image/"):
                    return None

                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 10_000_000:
                        return None
                    chunks.append(chunk)
                content = b"".join(chunks)

            suffix = PurePosixPath(urlsplit(current_url).path).suffix
            if not suffix or len(suffix) > 6:
                suffix = {
                    "image/jpeg": ".jpg",
                    "image/png": ".png",
                    "image/webp": ".webp",
                    "image/gif": ".gif",
                }.get(content_type, ".jpg")
            return BufferedInputFile(
                content,
                filename=f"news{suffix}",
            )
        return None


news_feed_publisher = NewsFeedPublisher()
