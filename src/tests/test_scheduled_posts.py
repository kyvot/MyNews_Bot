import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


os.environ.setdefault("XTGTOK", "test-secret")
os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_FOR_UNIT_TESTS")
os.environ.setdefault("DEFAULT_ADMIN_ID", "1")
os.environ.setdefault("GROUP_ID", "-1001")
os.environ.setdefault("CHANNEL_ID", "-1002")
os.environ.setdefault("BASE_URL", "https://example.test")

import bot
from bot.services import scheduled_posts


class _BeginContext:
    def __init__(self):
        self.session = SimpleNamespace(execute=AsyncMock())

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_):
        return False


class _FakeSessionFactory:
    def begin(self):
        return _BeginContext()


class ScheduledPostsTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_persists_source_message_before_adding_timer(self):
        session = SimpleNamespace(
            add=MagicMock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )

        async def assign_id(post):
            post.id = 41

        session.refresh.side_effect = assign_id
        run_at = datetime.now(timezone.utc) + timedelta(hours=1)

        with patch.object(scheduled_posts, "add_post_job") as add_post_job:
            post = await scheduled_posts.create_scheduled_post(
                session=session,
                source_chat_id=100,
                source_message_id=200,
                target_chat_id=300,
                created_by_id=400,
                run_at=run_at,
            )

        self.assertEqual(post.source_chat_id, 100)
        self.assertEqual(post.source_message_id, 200)
        self.assertEqual(post.source_message_ids, [200])
        session.commit.assert_awaited_once()
        session.refresh.assert_awaited_once_with(post)
        add_post_job.assert_called_once_with(41, post.run_at)

    async def test_publish_forwards_saved_album(self):
        forward_messages = AsyncMock(
            return_value=[
                SimpleNamespace(message_id=555),
                SimpleNamespace(message_id=556),
            ]
        )

        with (
            patch.object(
                scheduled_posts,
                "_claim_post",
                AsyncMock(return_value=(111, [222, 223], 333)),
            ),
            patch.object(
                scheduled_posts,
                "async_session",
                _FakeSessionFactory(),
            ),
            patch.object(bot.bot, "forward_messages", forward_messages),
        ):
            await scheduled_posts.publish_scheduled_post(7)

        forward_messages.assert_awaited_once_with(
            chat_id=333,
            from_chat_id=111,
            message_ids=[222, 223],
        )

    def test_naive_publication_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            scheduled_posts._as_utc(datetime(2026, 7, 25, 12, 0))


if __name__ == "__main__":
    unittest.main()
