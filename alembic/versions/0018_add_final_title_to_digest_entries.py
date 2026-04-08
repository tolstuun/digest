"""add final_title to digest_entries

Revision ID: 0018
Revises: 0017
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "digest_entries",
        sa.Column("final_title", sa.String(1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("digest_entries", "final_title")
