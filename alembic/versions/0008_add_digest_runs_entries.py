"""Add digest_runs and digest_entries tables

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "digest_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("digest_date", sa.Date(), nullable=False),
        sa.Column("section_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="assembled"),
        sa.Column("total_candidate_clusters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_included_clusters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest_date", "section_name", name="uq_digest_run_date_section"),
    )
    op.create_index("ix_digest_runs_digest_date", "digest_runs", ["digest_date"])

    op.create_table(
        "digest_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("digest_run_id", sa.UUID(), nullable=False),
        sa.Column("event_cluster_id", sa.UUID(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("title", sa.String(1024), nullable=True),
        sa.Column("canonical_summary_en", sa.Text(), nullable=True),
        sa.Column("canonical_summary_ru", sa.Text(), nullable=True),
        sa.Column("why_it_matters_en", sa.Text(), nullable=True),
        sa.Column("why_it_matters_ru", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["event_cluster_id"], ["event_clusters.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_digest_entries_digest_run_id", "digest_entries", ["digest_run_id"])
    op.create_index("ix_digest_entries_event_cluster_id", "digest_entries", ["event_cluster_id"])


def downgrade() -> None:
    op.drop_index("ix_digest_entries_event_cluster_id", table_name="digest_entries")
    op.drop_index("ix_digest_entries_digest_run_id", table_name="digest_entries")
    op.drop_table("digest_entries")
    op.drop_index("ix_digest_runs_digest_date", table_name="digest_runs")
    op.drop_table("digest_runs")
