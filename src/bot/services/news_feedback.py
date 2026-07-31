from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import stg
from core.db.models import NewsRaw, NewsVote


@dataclass(frozen=True, slots=True)
class VoteResult:
    status: str
    likes: int
    dislikes: int


async def record_vote(
    session: AsyncSession,
    *,
    news_id: int,
    user_id: int,
    value: int,
) -> VoteResult | None:
    if value not in {-1, 1}:
        raise ValueError("Vote must be -1 or 1")

    async with session.begin():
        news = await session.scalar(
            select(NewsRaw)
            .where(NewsRaw.id == news_id)
            .with_for_update()
        )
        if news is None:
            return None
        if news.voting_closed_at <= datetime.now(timezone.utc):
            return VoteResult("closed", news.likes, news.dislikes)

        vote = await session.get(NewsVote, (news_id, user_id))
        if vote is None:
            session.add(
                NewsVote(
                    news_id=news_id,
                    user_id=user_id,
                    value=value,
                )
            )
            if value == 1:
                news.likes += 1
            else:
                news.dislikes += 1
            status = "recorded"
        elif vote.value == value:
            status = "unchanged"
        else:
            if vote.value == 1:
                news.likes = max(0, news.likes - 1)
                news.dislikes += 1
            else:
                news.dislikes = max(0, news.dislikes - 1)
                news.likes += 1
            vote.value = value
            vote.updated_at = datetime.now(timezone.utc)
            status = "switched"

        news.is_hit = (
            news.likes - news.dislikes >= stg.NEWS_HIT_SCORE
        )

    return VoteResult(status, news.likes, news.dislikes)
