"""make fx_rates uploaded_by nullable for auto-sync

Revision ID: 6c05776d5210
Revises: r9s0t1u2v3w4
Create Date: 2026-06-19 09:41:34.729225
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '6c05776d5210'
down_revision: Union[str, None] = 'r9s0t1u2v3w4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('fx_rates', 'uploaded_by', existing_type=sa.UUID(), nullable=True)


def downgrade() -> None:
    op.alter_column('fx_rates', 'uploaded_by', existing_type=sa.UUID(), nullable=False)