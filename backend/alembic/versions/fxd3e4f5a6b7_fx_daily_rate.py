"""Daily FX rate history table

Revision ID: fxd3e4f5a6b7
Revises: fxf2b3c4d5e6
Create Date: 2026-06-23
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = 'fxd3e4f5a6b7'
down_revision: Union[str, None] = 'fxf2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fx_daily_rate",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("from_currency", sa.String(length=3), nullable=False),
        sa.Column("to_currency", sa.String(length=3), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(16, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("from_currency", "to_currency", "date", name="uq_fx_daily_rate_pair_date"),
    )
    # Index the common History-tab query: pair filter + newest-first ordering
    op.create_index(
        "ix_fx_daily_rate_pair_date",
        "fx_daily_rate",
        ["from_currency", "to_currency", "date"],
    )
    # No RLS policy — platform-level reference data, like fx_rates / fx_pairs.


def downgrade() -> None:
    op.drop_index("ix_fx_daily_rate_pair_date", table_name="fx_daily_rate")
    op.drop_table("fx_daily_rate")
