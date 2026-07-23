"""Merge typo region rows onto their canonical rows (Scrum 56 cleanup)

The Scrum 56 backfill inserted every DISTINCT existing region string as a row
so no data would orphan. That absorbed real dev-data typos as first-class
regions: EU/eu (meaning Europe), ASIA (Asia), INDIA (India), and the misspelled
BLOBAL/GLOBSL (GLOBAL). They're worse than cosmetic — the resolver's fallback
chain (exact -> parents -> GLOBAL -> Europe) never looks at 'BLOBAL', so data
stored under a typo is invisible to resolution.

This repoints every referencing column onto the canonical row, then deletes
the typo rows. Collision-safe: where a canonical row already exists with the
same unique key, the canonical row wins and the typo row is dropped (merges
run sequentially, so same-target typos can't collide with each other either).

Irreversible data migration: downgrade recreates the empty typo Region rows so
old code doesn't 500 on a missing FK target, but merged values stay merged.

Revision ID: rgc2b3c4d5e6
Revises: cm1a2b3c4d5e
Create Date: 2026-07-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "rgc2b3c4d5e6"
down_revision: Union[str, None] = "cm1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (typo code, canonical code) — processed in order; each merge lands its rows
# on the canonical code before the next starts.
MERGES = [
    ("EU", "Europe"),
    ("eu", "Europe"),
    ("ASIA", "Asia"),
    ("INDIA", "India"),
    ("BLOBAL", "GLOBAL"),
    ("GLOBSL", "GLOBAL"),
]

# Canonical targets that must exist (India/APAC/MEA arrived with the SEED-2
# loader; a prod DB that never ran it would be missing them).
CANONICAL = [("Europe", "Europe"), ("Asia", "Asia"), ("India", "India"), ("GLOBAL", "Global")]

# region-bearing columns with a UNIQUE constraint the merge could violate:
# (table, region column, other key columns in that constraint)
UNIQUE_KEYED = [
    ("index_values", "region", ("commodity_id", "year", "quarter")),
    ("index_overrides", "region", ("team_id", "commodity_id", "year", "quarter")),
    ("team_index_sources", "region", ("team_id", "commodity_id")),
    ("freight_lanes", "origin_region", ("destination_region", "mode")),
    ("freight_lanes", "destination_region", ("origin_region", "mode")),
    ("formula_region_coverage", "region", ("template_id",)),
]

# region-bearing columns with no unique constraint involving them
PLAIN = [
    ("cost_models", "region"),
    ("cost_models", "destination_region"),
    ("formula_template_components", "region"),
]


def upgrade() -> None:
    conn = op.get_bind()

    for code, name in CANONICAL:
        conn.execute(sa.text(
            "INSERT INTO regions (code, name) VALUES (:c, :n) ON CONFLICT (code) DO NOTHING"
        ), {"c": code, "n": name})

    for old, new in MERGES:
        for table, col, keys in UNIQUE_KEYED:
            join = " AND ".join(f"canon.{k} = typo.{k}" for k in keys)
            # Canonical row already holds this key -> the typo row is a stray.
            conn.execute(sa.text(f"""
                DELETE FROM {table} typo USING {table} canon
                WHERE typo.{col} = :old AND canon.{col} = :new AND {join}
            """), {"old": old, "new": new})
            conn.execute(sa.text(
                f"UPDATE {table} SET {col} = :new WHERE {col} = :old"
            ), {"old": old, "new": new})

        for table, col in PLAIN:
            conn.execute(sa.text(
                f"UPDATE {table} SET {col} = :new WHERE {col} = :old"
            ), {"old": old, "new": new})

        # Defensive: no children hang off typo rows today, but re-parent any
        # that might exist elsewhere rather than orphaning them.
        conn.execute(sa.text("""
            UPDATE regions SET parent_id = (SELECT id FROM regions WHERE code = :new)
            WHERE parent_id IN (SELECT id FROM regions WHERE code = :old)
        """), {"old": old, "new": new})

        conn.execute(sa.text("DELETE FROM regions WHERE code = :old"), {"old": old})


def downgrade() -> None:
    # Recreate the typo rows (empty, top-level) so any old references resolve;
    # the merged data itself is not moved back.
    conn = op.get_bind()
    for old, _ in MERGES:
        conn.execute(sa.text(
            "INSERT INTO regions (code, name) VALUES (:c, :c) ON CONFLICT (code) DO NOTHING"
        ), {"c": old})
