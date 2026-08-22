"""add advanced formula mode to formula_versions

Revision ID: p7q8r9s0t1u2
Revises: c7d8e9f0a1b2
Create Date: 2026-06-11
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'p7q8r9s0t1u2'
down_revision: Union[str, None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # formula_type: 'simple' (default, existing behaviour) or 'advanced' (expression-based)
    op.add_column('formula_versions',
        sa.Column('formula_type', sa.String(10), nullable=False,
                  server_default='simple'))

    # expression: the mathematical formula string, e.g. "0.92*[(0.75*ACN+1500)*(1-h)+h*AA/0.8]+FC"
    op.add_column('formula_versions',
        sa.Column('expression', sa.Text(), nullable=True))

    # variables: JSONB dict mapping variable names to definitions
    # {"ACN": {"type": "index", "commodity_id": 42},
    #  "h":   {"type": "fixed", "value": 0.3}}
    op.add_column('formula_versions',
        sa.Column('variables', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('formula_versions', 'variables')
    op.drop_column('formula_versions', 'expression')
    op.drop_column('formula_versions', 'formula_type')
