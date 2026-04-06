"""add pipeline_run_id to llm_usages

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_usages",
        sa.Column(
            "pipeline_run_id",
            sa.UUID(),
            sa.ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_llm_usages_pipeline_run_id",
        "llm_usages",
        ["pipeline_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_usages_pipeline_run_id", table_name="llm_usages")
    op.drop_column("llm_usages", "pipeline_run_id")
