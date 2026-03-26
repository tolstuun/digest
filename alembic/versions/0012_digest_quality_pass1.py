"""Phase 5 digest quality pass 1:
- Add source_url, source_name, final_summary, final_why_it_matters to digest_entries
- Add llm_usages table for token/cost accounting

Revision ID: 0012
Revises: 0011
Create Date: 2026-03-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to digest_entries
    op.add_column("digest_entries", sa.Column("source_url", sa.String(2048), nullable=True))
    op.add_column("digest_entries", sa.Column("source_name", sa.String(256), nullable=True))
    op.add_column("digest_entries", sa.Column("final_summary", sa.Text(), nullable=True))
    op.add_column("digest_entries", sa.Column("final_why_it_matters", sa.Text(), nullable=True))

    # Create llm_usages table
    op.create_table(
        "llm_usages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("stage_name", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("related_object_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_llm_usages_stage_name", "llm_usages", ["stage_name"])
    op.create_index("ix_llm_usages_related_object_id", "llm_usages", ["related_object_id"])
    op.create_index("ix_llm_usages_created_at", "llm_usages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_usages_created_at", table_name="llm_usages")
    op.drop_index("ix_llm_usages_related_object_id", table_name="llm_usages")
    op.drop_index("ix_llm_usages_stage_name", table_name="llm_usages")
    op.drop_table("llm_usages")

    op.drop_column("digest_entries", "final_why_it_matters")
    op.drop_column("digest_entries", "final_summary")
    op.drop_column("digest_entries", "source_name")
    op.drop_column("digest_entries", "source_url")
