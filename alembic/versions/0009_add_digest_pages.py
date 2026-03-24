"""Add digest_pages table

Revision ID: 0009
Revises: 0008
Create Date: 2026-03-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "digest_pages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("digest_run_id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(256), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("html_content", sa.Text(), nullable=False),
        sa.Column("rendered_at", sa.DateTime(timezone=True), nullable=True),
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
            ["digest_run_id"], ["digest_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest_run_id", name="uq_digest_pages_run_id"),
        sa.UniqueConstraint("slug", name="uq_digest_pages_slug"),
    )
    op.create_index("ix_digest_pages_digest_run_id", "digest_pages", ["digest_run_id"])
    op.create_index("ix_digest_pages_slug", "digest_pages", ["slug"])


def downgrade() -> None:
    op.drop_index("ix_digest_pages_slug", table_name="digest_pages")
    op.drop_index("ix_digest_pages_digest_run_id", table_name="digest_pages")
    op.drop_table("digest_pages")
