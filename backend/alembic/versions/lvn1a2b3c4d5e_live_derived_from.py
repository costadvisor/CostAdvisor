"""Jira 210 — which data each report section, panel and lever reads.

Revision ID: lvn1a2b3c4d5e
Revises: rfr1a2b3c4d5e
Create Date: 2026-11-09

Text can now carry live-number tokens (`{{index:brent|last}}`). Each text
records which series and recipe lines its tokens read, so LIVE-5 can flag a
text whose data moved without re-reading every text.

`editorial_blocks.derived_from` already exists (created for CON-5's staleness
job, never written); this adds the same nullable JSONB column to the three
other places that carry tokens. NULL means "no token". Nothing else changes.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "lvn1a2b3c4d5e"
down_revision: Union[str, None] = "rfr1a2b3c4d5e"
branch_labels = None
depends_on = None

TABLES = ("market_report_sections", "market_report_panels", "playbook_levers")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("derived_from", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "derived_from")
