"""Add cluster_feedback table for human editorial overrides.

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cluster_feedback",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "event_cluster_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("event_clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Action semantics:
        #   include          → always include (bypass all filters)
        #   exclude          → always hide (suppress regardless of score)
        #   noise            → always hide (mark as irrelevant noise)
        #   section_override → include only in the specified section (field: section)
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("section", sa.String(64), nullable=True),   # for section_override
        sa.Column("reason", sa.Text, nullable=True),           # optional human note
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_cluster_feedback_event_cluster_id",
        "cluster_feedback",
        ["event_cluster_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_cluster_feedback_event_cluster_id", table_name="cluster_feedback")
    op.drop_table("cluster_feedback")
