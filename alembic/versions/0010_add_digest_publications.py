"""Add digest_publications table

Revision ID: 0010
Revises: 0009
Create Date: 2026-03-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "digest_publications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("digest_page_id", sa.UUID(), nullable=False),
        sa.Column("channel_type", sa.String(64), nullable=False),
        sa.Column("target", sa.String(256), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("provider_message_id", sa.String(256), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
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
        sa.ForeignKeyConstraint(
            ["digest_page_id"], ["digest_pages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "digest_page_id", "channel_type", "target",
            name="uq_digest_publications_page_channel_target",
        ),
    )
    op.create_index(
        "ix_digest_publications_digest_page_id",
        "digest_publications",
        ["digest_page_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_digest_publications_digest_page_id",
        table_name="digest_publications",
    )
    op.drop_table("digest_publications")
