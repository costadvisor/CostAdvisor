"""Add provider + frequency to commodity_indexes, seed from source URL

Revision ID: idxpf1a2b3c4
Revises: fxd3e4f5a6b7
Create Date: 2026-06-24
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = 'idxpf1a2b3c4'
down_revision: Union[str, None] = 'fxd3e4f5a6b7'
branch_labels = None
depends_on = None


# (source_url host fragment, provider, native publish frequency)
SEED = [
    ('insee.fr', 'INSEE', 'Monthly'),
    ('businessanalytiq.com', 'BusinessAnalytiq', 'Monthly'),
    ('api.worldbank.org', 'World Bank', 'Monthly'),
    ('tradingeconomics.com', 'Trading Economics', 'Monthly'),
    ('fred.stlouisfed.org', 'FRED', 'Monthly'),
    ('ec.europa.eu', 'Eurostat', 'Monthly'),
    ('drewry.co.uk', 'Drewry', 'Weekly'),
    ('eia.gov', 'EIA', 'Weekly'),
    ('imarcgroup.com', 'IMARC', 'Monthly'),
    ('data-api.ecb.europa.eu', 'ECB', 'Daily'),
]


def upgrade() -> None:
    op.add_column('commodity_indexes', sa.Column('provider', sa.String(length=64), nullable=True))
    op.add_column('commodity_indexes', sa.Column('frequency', sa.String(length=16), nullable=True))

    # Default frequency = Quarterly (the system stores quarterly granularity);
    # provider stays NULL where the source host isn't recognised.
    op.execute(sa.text("UPDATE commodity_indexes SET frequency = 'Quarterly' WHERE frequency IS NULL"))

    for host, provider, freq in SEED:
        op.execute(
            sa.text(
                "UPDATE commodity_indexes SET provider = :p, frequency = :f "
                "WHERE source_url LIKE :pat"
            ).bindparams(p=provider, f=freq, pat=f"%{host}%")
        )


def downgrade() -> None:
    op.drop_column('commodity_indexes', 'frequency')
    op.drop_column('commodity_indexes', 'provider')
