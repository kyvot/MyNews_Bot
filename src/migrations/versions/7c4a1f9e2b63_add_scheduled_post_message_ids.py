"""Add message ID arrays to scheduled posts.

Revision ID: 7c4a1f9e2b63
Revises: f62c7a91d04e
Create Date: 2026-07-28 00:00:00

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "7c4a1f9e2b63"
down_revision: str | Sequence[str] | None = "f62c7a91d04e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scheduled_posts",
        sa.Column(
            "source_message_ids",
            postgresql.ARRAY(sa.Integer()),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE scheduled_posts "
        "SET source_message_ids = ARRAY[source_message_id]"
    )
    op.alter_column(
        "scheduled_posts",
        "source_message_ids",
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("scheduled_posts", "source_message_ids")
