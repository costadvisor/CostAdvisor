"""Composite/calculated index: composite_expression + composite_variables

Revision ID: co1a2b3c4d5e
Revises: al1a2b3c4d5e
Create Date: 2026-07-25

"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "co1a2b3c4d5e"
down_revision: Union[str, None] = "al1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("commodity_indexes", sa.Column("composite_expression", sa.Text(), nullable=True))
    op.add_column("commodity_indexes", sa.Column("composite_variables", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("commodity_indexes", "composite_variables")
    op.drop_column("commodity_indexes", "composite_expression")
