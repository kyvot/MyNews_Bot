from datetime import datetime, timezone
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY


class Base(DeclarativeBase, AsyncAttrs):
    pass


class NewsRaw(Base):
    __tablename__ = "news_raw"
    __table_args__ = (
        Index("ix_news_raw_url", "url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str]
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    telegram_msg_id: Mapped[int | None] = mapped_column(nullable=True)
    publication_token: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    publishing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    views: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    likes: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    dislikes: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    is_hit: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    voting_closed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class NewsVote(Base):
    __tablename__ = "news_votes"
    __table_args__ = (
        CheckConstraint(
            "value IN (-1, 1)",
            name="ck_news_votes_value",
        ),
    )

    news_id: Mapped[int] = mapped_column(
        ForeignKey("news_raw.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    value: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Admins(Base):
    __tablename__ = "admins"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    perm_id: Mapped[int]


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'published', 'failed', 'cancelled')",
            name="ck_scheduled_posts_status",
        ),
        Index("ix_scheduled_posts_status_run_at", "status", "run_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_message_ids: Mapped[list[int]] = mapped_column(
        ARRAY(Integer),
        nullable=False,
    )
    target_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="pending",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    published_message_id: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
