"""Add raw_items table

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "raw_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("external_id", sa.String(512), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.String(1024), nullable=True),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "content_hash", name="uq_raw_items_source_hash"),
    )
    op.create_index("ix_raw_items_source_id", "raw_items", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_raw_items_source_id", table_name="raw_items")
    op.drop_table("raw_items")
