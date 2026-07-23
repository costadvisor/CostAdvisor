"""Taxonomy spine: add subfamily tier + platform/team forking

Turns the flat, global ChemicalFamily into a forkable taxonomy spine
(family -> subfamily -> product):

- chemical_families gains team_id (NULL = platform, set = a team's private fork),
  origin_id (back-link a fork to the platform row it copied), and code.
- New subfamilies table with the same team_id/origin_id fork columns.
- products gains a nullable subfamily_id (family link is preserved untouched).
- The old global UNIQUE(name) on chemical_families is dropped: an un-renamed fork
  would collide with its platform original. Uniqueness is re-scoped platform-vs-team
  via partial indexes.
- RLS on both new-owner tables: platform rows (team_id IS NULL) readable by all;
  team rows visible only to that team (same policy shape as formula_templates).

Backfill: existing products keep their chemical_family_id, so every product that
mapped to a family still does. subfamily_id starts NULL for all rows.

Revision ID: tx1a2b3c4d5e
Revises: idxfq2b3c4d5e
Create Date: 2026-07-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "tx1a2b3c4d5e"
down_revision: Union[str, None] = "idxfq2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Same RLS building blocks used by cost_models / formula_templates.
_UID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
_MEMBER_OF = f"team_id IN (SELECT team_id FROM team_memberships WHERE user_id = {_UID})"


def upgrade() -> None:
    # ── 1. Extend chemical_families with the fork columns ────────────────────
    op.add_column("chemical_families", sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("chemical_families", sa.Column("origin_id", sa.Integer(), nullable=True))
    op.add_column("chemical_families", sa.Column("code", sa.String(length=16), nullable=True))
    op.create_foreign_key(
        "fk_chemical_families_team", "chemical_families", "teams",
        ["team_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_chemical_families_origin", "chemical_families", "chemical_families",
        ["origin_id"], ["id"], ondelete="SET NULL",
    )

    # The old global UNIQUE(name) breaks forking (a fork that keeps its origin's
    # name would collide). Re-scope uniqueness: platform names unique among platform
    # rows; team names unique within each team.
    op.execute("ALTER TABLE chemical_families DROP CONSTRAINT IF EXISTS chemical_families_name_key")
    op.execute("CREATE UNIQUE INDEX uq_chem_fam_platform_name ON chemical_families (name) WHERE team_id IS NULL")
    op.execute("CREATE UNIQUE INDEX uq_chem_fam_team_name ON chemical_families (team_id, name) WHERE team_id IS NOT NULL")

    # ── 2. New subfamilies tier ──────────────────────────────────────────────
    op.create_table(
        "subfamilies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("family_id", sa.Integer(), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin_id", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(length=16), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["family_id"], ["chemical_families.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["origin_id"], ["subfamilies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subfamilies_family_id", "subfamilies", ["family_id"])
    op.execute("CREATE UNIQUE INDEX uq_subfamily_platform_name ON subfamilies (family_id, name) WHERE team_id IS NULL")
    op.execute("CREATE UNIQUE INDEX uq_subfamily_team_name ON subfamilies (team_id, family_id, name) WHERE team_id IS NOT NULL")

    # ── 3. Repoint products (additive: family link is left intact) ───────────
    op.add_column("products", sa.Column("subfamily_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_products_subfamily", "products", "subfamilies",
        ["subfamily_id"], ["id"], ondelete="SET NULL",
    )

    # ── 4. RLS: platform readable by all, team rows scoped to the team ───────
    for table in ("chemical_families", "subfamilies"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL
            USING ({_BYPASS} OR team_id IS NULL OR {_MEMBER_OF})
        """)


def downgrade() -> None:
    for table in ("chemical_families", "subfamilies"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_constraint("fk_products_subfamily", "products", type_="foreignkey")
    op.drop_column("products", "subfamily_id")

    op.execute("DROP INDEX IF EXISTS uq_subfamily_team_name")
    op.execute("DROP INDEX IF EXISTS uq_subfamily_platform_name")
    op.drop_index("ix_subfamilies_family_id", table_name="subfamilies")
    op.drop_table("subfamilies")

    op.execute("DROP INDEX IF EXISTS uq_chem_fam_team_name")
    op.execute("DROP INDEX IF EXISTS uq_chem_fam_platform_name")
    op.drop_constraint("fk_chemical_families_origin", "chemical_families", type_="foreignkey")
    op.drop_constraint("fk_chemical_families_team", "chemical_families", type_="foreignkey")
    op.drop_column("chemical_families", "code")
    op.drop_column("chemical_families", "origin_id")
    op.drop_column("chemical_families", "team_id")
    op.create_unique_constraint("chemical_families_name_key", "chemical_families", ["name"])
