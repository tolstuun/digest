"""
Regression test for migration 0014 INSERT statement.

Verifies that the JSONB bindparam approach (no ::jsonb cast in SQL) works
against a real PostgreSQL connection — this was the exact syntax that caused
the deploy-blocking bug where :section_scope::jsonb mixed two parameter styles.
"""
import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB


def test_migration_0014_jsonb_insert_no_syntax_error(db):
    """
    The INSERT used in migration 0014 must execute without a syntax error.

    The previous broken form used :section_scope::jsonb which mixed SQLAlchemy
    named-param syntax with PostgreSQL cast syntax.  The fix uses bindparam
    with type_=JSONB() and no ::jsonb cast in the SQL string.
    """
    stmt = text(
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

    # Should not raise — any syntax error in the SQL or parameter binding
    # surfaces here against a real Postgres connection.
    db.execute(
        stmt,
        {
            "id": str(uuid.uuid4()),
            "name": "Migration0014 Regression Test Source",
            "url": "https://data.sec.gov/rss?cik=0000000000&type=8-K&count=1",
            "section_scope": ["companies_business"],
            "request_headers": {"User-Agent": "security-digest test"},
        },
    )
    db.commit()
