"""Add event_clusters table and stories.event_cluster_id

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_clusters",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("cluster_key", sa.String(512), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=True),
        sa.Column("representative_story_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_key", name="uq_event_clusters_cluster_key"),
    )
    op.create_index("ix_event_clusters_cluster_key", "event_clusters", ["cluster_key"])

    op.add_column(
        "stories",
        sa.Column("event_cluster_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_stories_event_cluster_id",
        "stories",
        "event_clusters",
        ["event_cluster_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_stories_event_cluster_id", "stories", ["event_cluster_id"])


def downgrade() -> None:
    op.drop_index("ix_stories_event_cluster_id", table_name="stories")
    op.drop_constraint("fk_stories_event_cluster_id", "stories", type_="foreignkey")
    op.drop_column("stories", "event_cluster_id")
    op.drop_index("ix_event_clusters_cluster_key", table_name="event_clusters")
    op.drop_table("event_clusters")
