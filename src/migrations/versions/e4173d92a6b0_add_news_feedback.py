"""Add news summaries and per-user feedback.

Revision ID: e4173d92a6b0
Revises: b83d9e6c1f42
Create Date: 2026-07-25 23:10:00

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e4173d92a6b0"
down_revision: str | Sequence[str] | None = "b83d9e6c1f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "news_raw",
        sa.Column("summary", sa.Text(), nullable=True),
    )
    op.alter_column(
        "news_raw",
        "fetched_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        postgresql_using="fetched_at AT TIME ZONE 'UTC'",
        server_default=sa.text("now()"),
        existing_nullable=False,
    )
    op.alter_column(
        "news_raw",
        "views",
        existing_type=sa.Integer(),
        server_default="0",
        existing_nullable=False,
    )
    op.alter_column(
        "news_raw",
        "likes",
        existing_type=sa.Integer(),
        server_default="0",
        existing_nullable=False,
    )
    op.alter_column(
        "news_raw",
        "dislikes",
        existing_type=sa.Integer(),
        server_default="0",
        existing_nullable=False,
    )
    op.alter_column(
        "news_raw",
        "is_hit",
        existing_type=sa.Boolean(),
        server_default=sa.text("false"),
        existing_nullable=False,
    )
    op.alter_column(
        "news_raw",
        "voting_closed_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        postgresql_using="voting_closed_at AT TIME ZONE 'UTC'",
        existing_nullable=False,
    )
    op.create_index(
        "ix_news_raw_url",
        "news_raw",
        ["url"],
        unique=False,
    )
    op.create_table(
        "news_votes",
        sa.Column("news_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("value", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "value IN (-1, 1)",
            name="ck_news_votes_value",
        ),
        sa.ForeignKeyConstraint(
            ["news_id"],
            ["news_raw.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("news_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("news_votes")
    op.drop_index("ix_news_raw_url", table_name="news_raw")
    op.alter_column(
        "news_raw",
        "voting_closed_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        postgresql_using="voting_closed_at AT TIME ZONE 'UTC'",
        existing_nullable=False,
    )
    op.alter_column(
        "news_raw",
        "is_hit",
        existing_type=sa.Boolean(),
        server_default=None,
        existing_nullable=False,
    )
    for column_name in ("dislikes", "likes", "views"):
        op.alter_column(
            "news_raw",
            column_name,
            existing_type=sa.Integer(),
            server_default=None,
            existing_nullable=False,
        )
    op.alter_column(
        "news_raw",
        "fetched_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        postgresql_using="fetched_at AT TIME ZONE 'UTC'",
        server_default=None,
        existing_nullable=False,
    )
    op.drop_column("news_raw", "summary")
