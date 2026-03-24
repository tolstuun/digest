"""Add story_facts table

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "story_facts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("story_id", sa.UUID(), nullable=False),
        sa.Column("model_name", sa.String(256), nullable=False),
        sa.Column("raw_model_output", postgresql.JSONB(), nullable=True),
        sa.Column("extraction_confidence", sa.Float(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_language", sa.String(16), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=True),
        sa.Column("company_names", postgresql.JSONB(), nullable=True),
        sa.Column("person_names", postgresql.JSONB(), nullable=True),
        sa.Column("product_names", postgresql.JSONB(), nullable=True),
        sa.Column("geography_names", postgresql.JSONB(), nullable=True),
        sa.Column("amount_text", sa.String(256), nullable=True),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("canonical_summary_en", sa.String(2048), nullable=True),
        sa.Column("canonical_summary_ru", sa.String(2048), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("story_id", name="uq_story_facts_story_id"),
    )
    op.create_index("ix_story_facts_story_id", "story_facts", ["story_id"])


def downgrade() -> None:
    op.drop_index("ix_story_facts_story_id", table_name="story_facts")
    op.drop_table("story_facts")
