"""add company to users

Revision ID: 2c697bf00541
Revises: 2884018275d0
Create Date: 2026-06-11 07:47:11.771533
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '2c697bf00541'
down_revision: Union[str, None] = '2884018275d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('company', sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'company')