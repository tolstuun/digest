"""Add request_headers to sources; seed 8 SEC EDGAR RSS sources.

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-01
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import bindparam
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# SEC EDGAR RSS — returns 8-K, 10-Q, 10-K filings for one CIK.
_SEC_URL = "https://data.sec.gov/rss?cik={cik}&type=8-K,10-Q,10-K&count=40"

_SEC_SOURCES = [
    ("CrowdStrike (SEC EDGAR)",        "0001535527"),
    ("Palo Alto Networks (SEC EDGAR)", "0001327567"),
    ("SentinelOne (SEC EDGAR)",        "0001583708"),
    ("Fortinet (SEC EDGAR)",           "0001262039"),
    ("Zscaler (SEC EDGAR)",            "0001713683"),
    ("Rapid7 (SEC EDGAR)",             "0001560327"),
    ("Tenable (SEC EDGAR)",            "0001660280"),
    ("Okta (SEC EDGAR)",               "0001660134"),
]

# Native Python objects — passed through SQLAlchemy's JSONB bind processor.
_SECTION_SCOPE = ["companies_business"]
_REQUEST_HEADERS = {"User-Agent": "security-digest contact@security-digest.example.com"}

# Prepared statement with JSONB-typed bindparams.
# No ::jsonb casts in the SQL — those conflict with SQLAlchemy's :param syntax.
_INSERT_STMT = sa.text(
    """
    INSERT INTO sources
        (id, name, type, url, enabled, priority, parser_type,
         poll_frequency_minutes, section_scope, request_headers,
         created_at, updated_at)
    VALUES
        (:id, :name, 'rss', :url, true, 9, 'feedparser',
         240, :section_scope, :request_headers,
         now(), now())
    ON CONFLICT ON CONSTRAINT uq_sources_name_url DO NOTHING
    """
).bindparams(
    bindparam("section_scope", type_=JSONB()),
    bindparam("request_headers", type_=JSONB()),
)


def upgrade() -> None:
    # 1. Add request_headers column.
    op.add_column(
        "sources",
        sa.Column("request_headers", JSONB, nullable=True),
    )

    # 2. Seed SEC EDGAR sources — idempotent via ON CONFLICT DO NOTHING.
    conn = op.get_bind()
    for name, cik in _SEC_SOURCES:
        conn.execute(
            _INSERT_STMT,
            {
                "id": str(uuid.uuid4()),
                "name": name,
                "url": _SEC_URL.format(cik=cik),
                "section_scope": _SECTION_SCOPE,
                "request_headers": _REQUEST_HEADERS,
            },
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM sources WHERE name LIKE '%(SEC EDGAR)'"))
    op.drop_column("sources", "request_headers")
