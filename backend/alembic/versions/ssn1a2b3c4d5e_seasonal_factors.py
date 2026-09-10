"""SCRUM-69 — index_seasonal_factors: generate the seasonality, do not import it.

Revision ID: ssn1a2b3c4d5e
Revises: dsr1a2b3c4d5e
Create Date: 2026-08-28

Platform-level, no RLS — a seasonal profile is a fact about a public price
series, the same treatment as `commodity_indexes` and the dossier tables.

Nothing is imported: `INDEX_SEASONALITY.json` and `INDEX_SEASON_NOTES.json` are
a cache of this computation and a set of templates rendering it. The method that
reproduces them (ratio to a centred 12-month moving average) is recorded on
every row, so two differently-computed sets can never be silently compared.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ssn1a2b3c4d5e"
down_revision: Union[str, None] = "dsr1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "index_seasonal_factors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("commodity_id", sa.Integer(),
                  sa.ForeignKey("commodity_indexes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("region", sa.String(length=20), nullable=True),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("factor", sa.Numeric(7, 3), nullable=False),
        sa.Column("method", sa.String(length=48), nullable=False),
        sa.Column("window_months", sa.SmallInteger(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="ck_seasonal_month"),
        sa.CheckConstraint("factor > 0", name="ck_seasonal_factor_positive"),
    )
    op.create_index("ix_seasonal_factors_commodity_id", "index_seasonal_factors",
                    ["commodity_id"])
    # `region` is nullable and Postgres treats every NULL as distinct in a unique
    # constraint, so the series-wide profile could otherwise be inserted twice
    # and the recompute would stop being idempotent.
    op.execute("""
        CREATE UNIQUE INDEX uq_seasonal_factor_series_wide
        ON index_seasonal_factors (commodity_id, month) WHERE region IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_seasonal_factor_region
        ON index_seasonal_factors (commodity_id, region, month)
        WHERE region IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_seasonal_factor_region")
    op.execute("DROP INDEX IF EXISTS uq_seasonal_factor_series_wide")
    op.drop_index("ix_seasonal_factors_commodity_id",
                  table_name="index_seasonal_factors")
    op.drop_table("index_seasonal_factors")
