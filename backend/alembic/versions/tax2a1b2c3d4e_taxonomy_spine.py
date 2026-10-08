"""Taxonomy spine: family › sub-family › product line, platform-only.

Revision ID: tax2a1b2c3d4e
Revises: ai1a2b3c4d5e
Create Date: 2026-10-08

Structural. It destroys the earlier taxonomy data only (families, the July
sub-family rows that were really product lines, team forks of both, and every
link to them). The content loader rebuilds the platform taxonomy.

What changes:

* The taxonomy links on products, templates, dimension assertions, editorial
  blocks and supplier trust scores (`subfamily_id`), and on commodity indexes
  (`family_id`, `subfamily_id`: a family is the wrong grain for a series, and
  nothing consumed them) are dropped. So is `products.chemical_family_id`.
* Rows that carried the old `subfamily` literal are deleted, and the CHECKs
  move to `product_line` (editorial and dimension subject types, the supplier
  trust grain).
* `taxonomy_reconciliations` (a July name-mapping tool) is dropped.
* `chemical_families` becomes platform-only: rows deleted, the tenant policy
  dropped, row-level security turned off (RLS left on with no policy denies
  every row to a non-superuser role), the fork columns dropped; `UNIQUE(name)`,
  `sort_order` and `meta` added. `code` stays.
* `subfamilies` is recreated as the real sub-family tier: identity
  `(family_id, name)`, one unnamed node allowed per family, `former_names` for
  renames, and `UNIQUE(id, family_id)` so a line can reference both.
* `product_lines` is new: identity is the platform handle (`platform`, unique);
  `line_key` (`Family|||Line`) and `name` follow renames, earlier keys are kept
  in `former_keys`. A composite foreign key `(subfamily_id, family_id)` keeps a
  line under a sub-family of its own family.
* `product_line_id` (ON DELETE SET NULL, indexed) is added to
  `formula_templates`, `products`, `dimension_assertions`, `editorial_blocks`
  and `supplier_trust_scores`.

No team forks on any taxonomy tier, and no row-level security on them: only
loaders write them, and every team reads them.

Downgrade restores the `ai1a2b3c4d5e` structure. It is data-losing: product
lines, sub-families, every `product_line_id` link and every row that carries
the `product_line` literal are lost. Families stay, as platform rows.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tax2a1b2c3d4e"
down_revision: Union[str, None] = "ai1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The tables that link to a product line (and linked to a sub-family before).
LINK_TABLES = (
    "formula_templates", "products", "dimension_assertions", "editorial_blocks",
    "supplier_trust_scores",
)

# The old fork policy, exactly as tx1a2b3c4d5e wrote it (used by downgrade).
_UID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
_MEMBER_OF = f"team_id IN (SELECT team_id FROM team_memberships WHERE user_id = {_UID})"


def _check_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # ── 1–2. Drop the old taxonomy links. DROP COLUMN takes each column's
    # foreign key and index with it (several are unnamed inline FKs).
    for table in LINK_TABLES:
        op.drop_column(table, "subfamily_id")
    op.drop_index("ix_commodity_indexes_subfamily_id", table_name="commodity_indexes")
    op.drop_index("ix_commodity_indexes_family_id", table_name="commodity_indexes")
    op.drop_column("commodity_indexes", "subfamily_id")
    op.drop_column("commodity_indexes", "family_id")
    # Before the families are emptied: this FK has no ON DELETE.
    op.drop_column("products", "chemical_family_id")

    # ── 3. Rows carrying the old literal. Block versions cascade.
    op.execute("DELETE FROM editorial_blocks WHERE subject_type = 'subfamily'")
    op.execute("DELETE FROM dimension_assertions WHERE subject_type = 'subfamily'")
    op.execute("DELETE FROM supplier_trust_scores WHERE grain = 'subfamily'")

    # ── 4. The CHECKs move to `product_line`.
    op.drop_constraint("ck_editorial_subject_type", "editorial_blocks", type_="check")
    op.create_check_constraint(
        "ck_editorial_subject_type", "editorial_blocks",
        _check_in("subject_type", ("formula", "index", "product_line", "family")))
    op.drop_constraint("ck_dimension_assertion_subject_type", "dimension_assertions",
                       type_="check")
    op.create_check_constraint(
        "ck_dimension_assertion_subject_type", "dimension_assertions",
        _check_in("subject_type", ("formula", "index", "product_line", "family", "producer")))
    op.drop_constraint("ck_sts_grain", "supplier_trust_scores", type_="check")
    op.create_check_constraint(
        "ck_sts_grain", "supplier_trust_scores",
        _check_in("grain", ("product", "product_line")))

    # ── 5–6. The July name-mapping table and the old sub-family table.
    op.drop_table("taxonomy_reconciliations")
    op.drop_table("subfamilies")

    # ── 7. Families become platform-only.
    op.execute("DELETE FROM chemical_families")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON chemical_families")
    op.execute("ALTER TABLE chemical_families NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chemical_families DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS uq_chem_fam_team_name")
    op.execute("DROP INDEX IF EXISTS uq_chem_fam_platform_name")
    op.drop_constraint("fk_chemical_families_origin", "chemical_families", type_="foreignkey")
    op.drop_constraint("fk_chemical_families_team", "chemical_families", type_="foreignkey")
    op.drop_column("chemical_families", "origin_id")
    op.drop_column("chemical_families", "team_id")
    op.drop_column("chemical_families", "custom_attribute_schema")
    op.create_unique_constraint("uq_chemical_families_name", "chemical_families", ["name"])
    op.add_column("chemical_families", sa.Column(
        "sort_order", sa.Integer(), server_default="0", nullable=False))
    op.add_column("chemical_families", sa.Column("meta", postgresql.JSONB(), nullable=True))

    # ── 8. The sub-family tier.
    op.create_table(
        "subfamilies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("family_id", sa.Integer(),
                  sa.ForeignKey("chemical_families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("former_names", postgresql.JSONB(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("family_id", "name", name="uq_subfamilies_family_name"),
        sa.UniqueConstraint("id", "family_id", name="uq_subfamilies_id_family"),
    )
    op.create_index("ix_subfamilies_family_id", "subfamilies", ["family_id"])
    # NULLs are distinct in a unique constraint: this allows one unnamed node
    # per family.
    op.create_index("uq_subfamilies_family_unnamed", "subfamilies", ["family_id"],
                    unique=True, postgresql_where=sa.text("name IS NULL"))

    # ── 9. Product lines.
    op.create_table(
        "product_lines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("family_id", sa.Integer(),
                  sa.ForeignKey("chemical_families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subfamily_id", sa.Integer(), nullable=True),
        sa.Column("platform", sa.String(length=128), nullable=True),
        sa.Column("line_key", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("former_keys", postgresql.JSONB(), nullable=True),
        sa.Column("in_v1_scope", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("report_slug", sa.String(length=64), nullable=True),
        sa.Column("report_old_line", sa.String(length=255), nullable=True),
        sa.Column("flags", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.String(length=255), nullable=True),
        sa.Column("axis_meta", postgresql.JSONB(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("platform", name="uq_product_lines_platform"),
        sa.UniqueConstraint("line_key", name="uq_product_lines_line_key"),
        # No ON DELETE: a sub-family that still has lines cannot be deleted.
        sa.ForeignKeyConstraint(
            ["subfamily_id", "family_id"], ["subfamilies.id", "subfamilies.family_id"],
            name="fk_product_lines_subfamily_family"),
    )
    op.create_index("ix_product_lines_family_id", "product_lines", ["family_id"])
    op.create_index("ix_product_lines_subfamily_id", "product_lines", ["subfamily_id"])

    # ── 10. The product-line link.
    for table in LINK_TABLES:
        op.add_column(table, sa.Column(
            "product_line_id", sa.Integer(),
            sa.ForeignKey("product_lines.id", ondelete="SET NULL"), nullable=True))
        op.create_index(f"ix_{table}_product_line_id", table, ["product_line_id"])


def downgrade() -> None:
    # ── Product-line links, lines and the sub-family tier go.
    for table in LINK_TABLES:
        op.drop_index(f"ix_{table}_product_line_id", table_name=table)
        op.drop_column(table, "product_line_id")
    op.drop_table("product_lines")
    op.drop_table("subfamilies")

    # ── Families get their fork columns and tenant policy back.
    op.drop_column("chemical_families", "meta")
    op.drop_column("chemical_families", "sort_order")
    op.drop_constraint("uq_chemical_families_name", "chemical_families", type_="unique")
    op.add_column("chemical_families", sa.Column(
        "custom_attribute_schema", postgresql.JSONB(), nullable=True))
    op.add_column("chemical_families", sa.Column(
        "team_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("chemical_families", sa.Column("origin_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_chemical_families_team", "chemical_families", "teams",
                          ["team_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_chemical_families_origin", "chemical_families",
                          "chemical_families", ["origin_id"], ["id"], ondelete="SET NULL")
    op.execute("CREATE UNIQUE INDEX uq_chem_fam_platform_name ON chemical_families (name) "
               "WHERE team_id IS NULL")
    op.execute("CREATE UNIQUE INDEX uq_chem_fam_team_name ON chemical_families (team_id, name) "
               "WHERE team_id IS NOT NULL")

    # ── The old sub-family table (team-forkable), empty.
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
    op.execute("CREATE UNIQUE INDEX uq_subfamily_platform_name ON subfamilies (family_id, name) "
               "WHERE team_id IS NULL")
    op.execute("CREATE UNIQUE INDEX uq_subfamily_team_name ON subfamilies "
               "(team_id, family_id, name) WHERE team_id IS NOT NULL")
    for table in ("chemical_families", "subfamilies"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL
            USING ({_BYPASS} OR team_id IS NULL OR {_MEMBER_OF})
        """)

    # ── The July name-mapping table, empty.
    op.create_table(
        "taxonomy_reconciliations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("drop_family", sa.String(length=200), nullable=False),
        sa.Column("drop_subfamily", sa.String(length=200), server_default="", nullable=False),
        sa.Column("family_id", sa.Integer(),
                  sa.ForeignKey("chemical_families.id", ondelete="SET NULL"), nullable=True),
        sa.Column("subfamily_id", sa.Integer(),
                  sa.ForeignKey("subfamilies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("level", "drop_family", "drop_subfamily",
                            name="uq_taxonomy_reconciliation_key"),
        sa.CheckConstraint("level IN ('family', 'subfamily')",
                           name="ck_taxonomy_reconciliation_level"),
    )

    # ── The CHECKs move back; rows with the new literal cannot stay.
    op.execute("DELETE FROM editorial_blocks WHERE subject_type = 'product_line'")
    op.execute("DELETE FROM dimension_assertions WHERE subject_type = 'product_line'")
    op.execute("DELETE FROM supplier_trust_scores WHERE grain = 'product_line'")
    op.drop_constraint("ck_editorial_subject_type", "editorial_blocks", type_="check")
    op.create_check_constraint(
        "ck_editorial_subject_type", "editorial_blocks",
        _check_in("subject_type", ("formula", "index", "subfamily", "family")))
    op.drop_constraint("ck_dimension_assertion_subject_type", "dimension_assertions",
                       type_="check")
    op.create_check_constraint(
        "ck_dimension_assertion_subject_type", "dimension_assertions",
        _check_in("subject_type", ("formula", "index", "subfamily", "family", "producer")))
    op.drop_constraint("ck_sts_grain", "supplier_trust_scores", type_="check")
    op.create_check_constraint(
        "ck_sts_grain", "supplier_trust_scores", _check_in("grain", ("product", "subfamily")))

    # ── The old links, empty, with their original constraint names.
    op.add_column("products", sa.Column("chemical_family_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_products_chemical_family", "products", "chemical_families",
                          ["chemical_family_id"], ["id"])
    op.add_column("products", sa.Column("subfamily_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_products_subfamily", "products", "subfamilies",
                          ["subfamily_id"], ["id"], ondelete="SET NULL")
    op.add_column("formula_templates", sa.Column("subfamily_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_formula_templates_subfamily", "formula_templates", "subfamilies",
                          ["subfamily_id"], ["id"], ondelete="SET NULL")
    # These three were inline FKs: Postgres names them <table>_subfamily_id_fkey.
    for table in ("dimension_assertions", "editorial_blocks", "supplier_trust_scores"):
        op.add_column(table, sa.Column(
            "subfamily_id", sa.Integer(),
            sa.ForeignKey("subfamilies.id", ondelete="SET NULL"), nullable=True))
    op.add_column("commodity_indexes", sa.Column("family_id", sa.Integer(), nullable=True))
    op.add_column("commodity_indexes", sa.Column("subfamily_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_commodity_indexes_family", "commodity_indexes",
                          "chemical_families", ["family_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_commodity_indexes_subfamily", "commodity_indexes",
                          "subfamilies", ["subfamily_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_commodity_indexes_family_id", "commodity_indexes", ["family_id"])
    op.create_index("ix_commodity_indexes_subfamily_id", "commodity_indexes", ["subfamily_id"])
