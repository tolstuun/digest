"""Add event_cluster_assessments table

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_cluster_assessments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_cluster_id", sa.UUID(), nullable=False),
        sa.Column("primary_section", sa.String(64), nullable=True),
        sa.Column("include_in_digest", sa.Boolean(), nullable=True),
        sa.Column("rule_score", sa.Float(), nullable=True),
        sa.Column("llm_score", sa.Float(), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("why_it_matters_en", sa.Text(), nullable=True),
        sa.Column("why_it_matters_ru", sa.Text(), nullable=True),
        sa.Column("editorial_notes", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(256), nullable=True),
        sa.Column("raw_model_output", postgresql.JSONB(), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["event_cluster_id"], ["event_clusters.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_cluster_id", name="uq_event_cluster_assessments_cluster_id"
        ),
    )
    op.create_index(
        "ix_event_cluster_assessments_cluster_id",
        "event_cluster_assessments",
        ["event_cluster_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_event_cluster_assessments_cluster_id",
        table_name="event_cluster_assessments",
    )
    op.drop_table("event_cluster_assessments")
