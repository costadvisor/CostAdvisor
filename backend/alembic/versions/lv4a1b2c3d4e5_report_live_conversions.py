"""LIVE-4 — typed report numbers to be converted into live tokens.

Revision ID: lv4a1b2c3d4e5
Revises: lvn1a2b3c4d5e
Create Date: 2026-11-10

One platform table, `report_live_conversions`: a typed number in a report
(report, section, anchor, the number as typed), the live token that replaces
it, and whether it was approved. The report loader applies approved rows on
every load, because a reload from the content drop would otherwise undo a
conversion made in place. Nothing else changes.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "lv4a1b2c3d4e5"
down_revision: Union[str, None] = "lvn1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_live_conversions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("section_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("anchor", sa.Text(), nullable=False),
        sa.Column("typed", sa.String(64), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("today", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("slug", "section_id", "kind", "anchor", "typed", name="uq_rlc_target"),
        sa.CheckConstraint("kind IN ('cost_bar','date','pct_move')", name="ck_rlc_kind"),
        sa.CheckConstraint("status IN ('proposed','approved','rejected')", name="ck_rlc_status"),
    )
    op.create_index("ix_rlc_slug", "report_live_conversions", ["slug"])


def downgrade() -> None:
    op.drop_index("ix_rlc_slug", table_name="report_live_conversions")
    op.drop_table("report_live_conversions")
