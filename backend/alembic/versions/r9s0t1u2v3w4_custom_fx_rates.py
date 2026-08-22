"""add custom_fx_rates table for team-level FX overrides

Revision ID: r9s0t1u2v3w4
Revises: s0t1u2v3w4x5
Create Date: 2026-06-17
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'r9s0t1u2v3w4'
down_revision: Union[str, None] = 's0t1u2v3w4x5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'custom_fx_rates',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('team_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('teams.id', ondelete='CASCADE'), nullable=False),
        sa.Column('from_currency', sa.String(3), nullable=False),
        sa.Column('to_currency', sa.String(3), nullable=False),
        sa.Column('year', sa.SmallInteger, nullable=False),
        sa.Column('quarter', sa.SmallInteger, nullable=False),
        sa.Column('rate', sa.Numeric(12, 6), nullable=False),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('team_id', 'from_currency', 'to_currency', 'year', 'quarter',
                            name='uq_custom_fx_rates'),
    )

    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE custom_fx_rates ENABLE ROW LEVEL SECURITY"))
    conn.execute(sa.text("ALTER TABLE custom_fx_rates FORCE ROW LEVEL SECURITY"))
    conn.execute(sa.text("""
        CREATE POLICY tenant_isolation ON custom_fx_rates
        USING (
            current_setting('app.bypass_rls', true) = 'on'
            OR team_id::text = current_setting('app.current_team_id', true)
        )
        WITH CHECK (
            current_setting('app.bypass_rls', true) = 'on'
            OR team_id::text = current_setting('app.current_team_id', true)
        )
    """))


def downgrade() -> None:
    op.drop_table('custom_fx_rates')
