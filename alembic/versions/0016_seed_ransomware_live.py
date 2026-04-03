"""Seed Ransomware.live as an incidents RSS source.

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-03
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import bindparam
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INSERT_STMT = sa.text(
    """
    INSERT INTO sources
        (id, name, type, url, enabled, priority, parser_type,
         poll_frequency_minutes, section_scope, request_headers,
         created_at, updated_at)
    VALUES
        (:id, :name, 'rss', :url, true, 9, 'feedparser',
         15, :section_scope, :request_headers,
         now(), now())
    ON CONFLICT ON CONSTRAINT uq_sources_name_url DO NOTHING
    """
).bindparams(
    bindparam("section_scope", type_=JSONB()),
    bindparam("request_headers", type_=JSONB()),
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        _INSERT_STMT,
        {
            "id": str(uuid.uuid4()),
            "name": "Ransomware.live",
            "url": "https://www.ransomware.live/rss.xml",
            "section_scope": ["incidents"],
            "request_headers": {},
        },
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM sources WHERE name = 'Ransomware.live'")
    )
