"""add fx_pairs table and extend custom_fx_rates

Revision ID: a1b2c3d4e5f6
Revises: 6c05776d5210
Create Date: 2026-06-19 12:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'fxp1a2b3c4d5'
down_revision: Union[str, None] = '6c05776d5210'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ECB_BASE = "https://data-api.ecb.europa.eu/service/data/EXR"

_ECB_PAIRS = [
    ("EUR", "USD", "EUR/USD", f"{_ECB_BASE}/Q.USD.EUR.SP00.A"),
    ("GBP", "EUR", "GBP/EUR", f"{_ECB_BASE}/Q.GBP.EUR.SP00.A"),
    ("CNY", "EUR", "CNY/EUR", f"{_ECB_BASE}/Q.CNY.EUR.SP00.A"),
    ("JPY", "EUR", "JPY/EUR", f"{_ECB_BASE}/Q.JPY.EUR.SP00.A"),
    ("IDR", "EUR", "IDR/EUR", f"{_ECB_BASE}/Q.IDR.EUR.SP00.A"),
    ("PHP", "EUR", "PHP/EUR", f"{_ECB_BASE}/Q.PHP.EUR.SP00.A"),
]


def upgrade() -> None:
    op.create_table(
        "fx_pairs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("from_currency", sa.String(3), nullable=False),
        sa.Column("to_currency", sa.String(3), nullable=False),
        sa.Column("name", sa.String(10), nullable=False, unique=True),
        sa.Column("source_type", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("scrape_url", sa.String(512), nullable=True),
        sa.Column("scrape_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("live_rate", sa.Numeric(16, 6), nullable=True),
        sa.Column("live_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("from_currency", "to_currency", name="uq_fx_pairs_currencies"),
    )

    # Seed the 6 ECB pairs
    fx_pairs = op.get_bind().execute(
        sa.text(
            "INSERT INTO fx_pairs (from_currency, to_currency, name, source_type, scrape_url, scrape_enabled) "
            "VALUES (:from_c, :to_c, :name, 'ecb', :url, true) "
            "ON CONFLICT (from_currency, to_currency) DO NOTHING"
        ),
        [
            {"from_c": f, "to_c": t, "name": n, "url": u}
            for f, t, n, u in _ECB_PAIRS
        ],
    )

    # Extend custom_fx_rates
    op.add_column("custom_fx_rates", sa.Column(
        "value_type", sa.String(16), nullable=False, server_default="fixed"
    ))
    op.add_column("custom_fx_rates", sa.Column("ref_year", sa.SmallInteger(), nullable=True))
    op.add_column("custom_fx_rates", sa.Column("ref_quarter", sa.SmallInteger(), nullable=True))
    op.alter_column("custom_fx_rates", "rate", existing_type=sa.Numeric(12, 6), nullable=True)


def downgrade() -> None:
    op.alter_column("custom_fx_rates", "rate", existing_type=sa.Numeric(12, 6), nullable=False)
    op.drop_column("custom_fx_rates", "ref_quarter")
    op.drop_column("custom_fx_rates", "ref_year")
    op.drop_column("custom_fx_rates", "value_type")
    op.drop_table("fx_pairs")
