"""Add unique constraint on sources(name, url) — NULLS NOT DISTINCT.

Duplicate sources are defined as same name + same url (including both NULL).

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULLS NOT DISTINCT (Postgres 15+) ensures that two rows with the same
    # name and url=NULL are also treated as duplicates by the constraint.
    op.execute(
        "ALTER TABLE sources "
        "ADD CONSTRAINT uq_sources_name_url "
        "UNIQUE NULLS NOT DISTINCT (name, url)"
    )


def downgrade() -> None:
    op.drop_constraint("uq_sources_name_url", "sources", type_="unique")
