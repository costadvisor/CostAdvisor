"""LIVE-5 — stale flags on report sections, panels, platform levers and blocks.

Revision ID: lv5a1b2c3d4e5
Revises: lv4a1b2c3d4e5
Create Date: 2026-11-10

Numbers in text can now follow the data (210, LIVE-4); the words around them
cannot. Each text records the data it was written against (`baseline`) and,
from a daily check, why it is out of date (`stale_reasons`, with `is_stale`
for a quick filter and `stale_checked_at`).

`editorial_blocks` already has `is_stale` (created for CON-5's staleness job,
never written until now); it gains the other three. All columns nullable or
defaulted, nothing else changes.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "lv5a1b2c3d4e5"
down_revision: Union[str, None] = "lv4a1b2c3d4e5"
branch_labels = None
depends_on = None

NEW_IS_STALE = ("market_report_sections", "market_report_panels", "playbook_levers")
ALL = NEW_IS_STALE + ("editorial_blocks",)


def upgrade() -> None:
    for table in ALL:
        op.add_column(table, sa.Column("baseline", postgresql.JSONB(), nullable=True))
        op.add_column(table, sa.Column("stale_reasons", postgresql.JSONB(), nullable=True))
        op.add_column(table, sa.Column("stale_checked_at", sa.DateTime(timezone=True), nullable=True))
    for table in NEW_IS_STALE:
        op.add_column(table, sa.Column("is_stale", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))


def downgrade() -> None:
    for table in NEW_IS_STALE:
        op.drop_column(table, "is_stale")
    for table in ALL:
        op.drop_column(table, "stale_checked_at")
        op.drop_column(table, "stale_reasons")
        op.drop_column(table, "baseline")
