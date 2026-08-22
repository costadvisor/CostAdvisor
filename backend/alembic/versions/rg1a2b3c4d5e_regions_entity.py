"""Region as a first-class entity: regions table + FK-ify 5 region columns (Scrum 56)

Promotes free-text `region` into a managed reference table:

- New `regions` table (code = stable natural key, self-referential parent_id for
  subregions). Seed 7 top-level regions + GLOBAL, plus a few subregions (NWE,
  France, USA, China) that reconcile the finer grain feeds carry in their names.
- Backfill: insert every DISTINCT region string currently held by the 5 tables
  (index_values, index_overrides, team_index_sources, cost_models, freight_lanes)
  so nothing orphans, THEN add FK constraints on each region column -> regions.code.

The columns stay VARCHAR (the resolver/costing/scraper code matches on the region
string, e.g. the "GLOBAL" fallback) — but each value is now a validated FK to a
Region row rather than free text.

Revision ID: rg1a2b3c4d5e
Revises: tx1a2b3c4d5e
Create Date: 2026-07-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "rg1a2b3c4d5e"
down_revision: Union[str, None] = "tx1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# code, name — 7 top-level regions + the GLOBAL sentinel (preserved for the
# data_resolver fallback chain). Existing data uses Europe/NA/Latam/Asia/GLOBAL,
# so those codes are kept verbatim to avoid orphaning.
_TOP_LEVEL = [
    ("GLOBAL", "Global"),
    ("Europe", "Europe"),
    ("NA", "North America"),
    ("Latam", "Latin America"),
    ("Asia", "Asia"),
    ("ME", "Middle East"),
    ("Africa", "Africa"),
    ("Oceania", "Oceania"),
]

# code, name, parent_code — subregions reconciling feed grain that is finer than
# the top level (NWE is finer than Europe; several feeds carry region in the name:
# Eurostat FR, FRED "… USA", "Labor China").
_SUBREGIONS = [
    ("NWE", "Northwest Europe", "Europe"),
    ("France", "France", "Europe"),
    ("USA", "United States", "NA"),
    ("China", "China", "Asia"),
]

# (constraint_name, table, column) — every region-bearing column.
_REGION_FKS = [
    ("fk_index_values_region", "index_values", "region"),
    ("fk_index_overrides_region", "index_overrides", "region"),
    ("fk_team_index_sources_region", "team_index_sources", "region"),
    ("fk_cost_models_region", "cost_models", "region"),
    ("fk_cost_models_destination_region", "cost_models", "destination_region"),
    ("fk_freight_lanes_origin_region", "freight_lanes", "origin_region"),
    ("fk_freight_lanes_destination_region", "freight_lanes", "destination_region"),
]


def upgrade() -> None:
    op.create_table(
        "regions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["parent_id"], ["regions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_regions_code"),
    )

    # Seed top-level regions.
    for code, name in _TOP_LEVEL:
        op.execute(sa.text("INSERT INTO regions (code, name) VALUES (:c, :n)").bindparams(c=code, n=name))
    # Seed subregions (resolve parent by code).
    for code, name, parent_code in _SUBREGIONS:
        op.execute(sa.text(
            "INSERT INTO regions (code, name, parent_id) "
            "SELECT :c, :n, id FROM regions WHERE code = :p"
        ).bindparams(c=code, n=name, p=parent_code))

    # Backfill every distinct region string currently in use so no value orphans
    # when the FK constraints go on. Unknown/free-text values become top-level rows.
    op.execute("""
        INSERT INTO regions (code, name)
        SELECT DISTINCT r, r FROM (
            SELECT region AS r FROM index_values
            UNION SELECT region FROM index_overrides
            UNION SELECT region FROM team_index_sources
            UNION SELECT region FROM cost_models
            UNION SELECT destination_region FROM cost_models WHERE destination_region IS NOT NULL
            UNION SELECT origin_region FROM freight_lanes
            UNION SELECT destination_region FROM freight_lanes
        ) x
        WHERE r IS NOT NULL AND r <> ''
        ON CONFLICT (code) DO NOTHING
    """)

    # Now the FK constraints — all existing values are guaranteed present above.
    for name, table, column in _REGION_FKS:
        op.create_foreign_key(name, table, "regions", [column], ["code"])


def downgrade() -> None:
    for name, table, _ in _REGION_FKS:
        op.drop_constraint(name, table, type_="foreignkey")
    op.drop_table("regions")
