"""Correct commodity_indexes.frequency: non-FX = Quarterly, FX = Daily

Revision ID: idxfq2b3c4d5e
Revises: idxpf1a2b3c4
Create Date: 2026-06-26

The previous migration (idxpf1a2b3c4) seeded `frequency` from each source's
*native publish cadence* (Monthly/Weekly), which is misleading: the platform
stores and costs every non-FX index at quarter granularity (index_values has
only year+quarter). Only FX carries daily series (fx_daily_rate). So the
displayed frequency should reflect how the data is used here, not the source's
upstream cadence.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = 'idxfq2b3c4d5e'
down_revision: Union[str, None] = 'idxpf1a2b3c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE commodity_indexes SET frequency = 'Daily' WHERE category = 'FX'"))
    op.execute(sa.text("UPDATE commodity_indexes SET frequency = 'Quarterly' WHERE category IS DISTINCT FROM 'FX'"))


def downgrade() -> None:
    # No-op: the prior native-cadence values aren't worth restoring.
    pass
