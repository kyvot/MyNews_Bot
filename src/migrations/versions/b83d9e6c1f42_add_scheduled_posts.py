"""Add persistent scheduled posts.

Revision ID: b83d9e6c1f42
Revises: 58a1827a9a85
Create Date: 2026-07-25 22:30:00

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b83d9e6c1f42"
down_revision: str | Sequence[str] | None = "58a1827a9a85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=False),
        sa.Column("target_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("published_message_id", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'published', 'failed', 'cancelled')",
            name="ck_scheduled_posts_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_scheduled_posts_status_run_at",
        "scheduled_posts",
        ["status", "run_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scheduled_posts_status_run_at",
        table_name="scheduled_posts",
    )
    op.drop_table("scheduled_posts")
