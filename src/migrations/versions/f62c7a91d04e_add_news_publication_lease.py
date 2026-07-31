"""Add a lease for idempotent news publication.

Revision ID: f62c7a91d04e
Revises: e4173d92a6b0
Create Date: 2026-07-25 23:50:00

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f62c7a91d04e"
down_revision: str | Sequence[str] | None = "e4173d92a6b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "news_raw",
        sa.Column("publication_token", sa.String(length=32)),
    )
    op.add_column(
        "news_raw",
        sa.Column(
            "publishing_started_at",
            sa.DateTime(timezone=True),
        ),
    )


def downgrade() -> None:
    op.drop_column("news_raw", "publishing_started_at")
    op.drop_column("news_raw", "publication_token")
