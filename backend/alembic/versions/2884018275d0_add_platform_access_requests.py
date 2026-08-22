"""add platform_access_requests

Revision ID: 2884018275d0
Revises: dbf93108d6fe
Create Date: 2026-06-11 07:06:10.506461
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '2884018275d0'
down_revision: Union[str, None] = 'dbf93108d6fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('platform_access_requests',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=True),
    sa.Column('company', sa.String(length=128), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reviewed_by_id', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['reviewed_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_platform_access_requests_email'), 'platform_access_requests', ['email'], unique=False)
    # Prevent duplicate pending requests for the same email; re-applications after rejection are allowed.
    op.create_index(
        'ix_platform_access_requests_email_pending',
        'platform_access_requests',
        ['email'],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index('ix_platform_access_requests_email_pending', table_name='platform_access_requests')
    op.drop_index(op.f('ix_platform_access_requests_email'), table_name='platform_access_requests')
    op.drop_table('platform_access_requests')