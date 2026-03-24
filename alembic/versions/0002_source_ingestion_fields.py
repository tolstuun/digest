"""Add ingestion management fields to sources

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("parser_type", sa.String(50), nullable=True))
    op.add_column("sources", sa.Column("poll_frequency_minutes", sa.Integer(), nullable=True))
    op.add_column("sources", sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sources", sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sources", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("section_scope", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "section_scope")
    op.drop_column("sources", "last_error")
    op.drop_column("sources", "last_success_at")
    op.drop_column("sources", "last_polled_at")
    op.drop_column("sources", "poll_frequency_minutes")
    op.drop_column("sources", "parser_type")
