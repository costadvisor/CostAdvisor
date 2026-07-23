"""Weighted formula components + per-(formula x region) coverage (Scrum 58)

Gives FormulaTemplate a structured weighted-lines form alongside the free-form
expression:

- formula_template_components: one weighted line per row. component_type is
  'index' (tracks a commodity index, nullable commodity_id enforced coherent
  per type by CHECK), 'fixed' (flat share: margin / conversion / "other"), or
  'formula' (another template as an input — tiered chaining, resolved with a
  depth cap by services/formula_resolver.py).
- formula_region_coverage: a "combo" — the same formula priced in one region
  (base price anchor + margin per region), unique on (template, region).
  Resolution falls back exact region -> parent region -> GLOBAL -> Europe.
- formula_templates.expression becomes nullable: a purely-weighted template
  has no expression.

RLS: both child tables are transitively scoped through their parent template
(platform templates readable by all, team templates members-only) — same
pattern as formula_components -> formula_versions -> cost_models.

Revision ID: wc1a2b3c4d5e
Revises: im1a2b3c4d5e
Create Date: 2026-07-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "wc1a2b3c4d5e"
down_revision: Union[str, None] = "im1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Same RLS building blocks used across the app's policies.
_UID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
# Row visible iff its parent template is visible: platform (team_id IS NULL)
# to everyone, team-scoped to that team's members.
_TEMPLATE_VISIBLE = f"""
    template_id IN (
        SELECT ft.id FROM formula_templates ft
        WHERE ft.team_id IS NULL OR ft.team_id IN (
            SELECT team_id FROM team_memberships WHERE user_id = {_UID}
        )
    )
"""


def upgrade() -> None:
    # ── 1. Weighted component lines ──────────────────────────────────────────
    op.create_table(
        "formula_template_components",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("component_type", sa.String(length=16), nullable=False),
        sa.Column("commodity_id", sa.Integer(), nullable=True),
        sa.Column("input_template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("weight_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column("is_proxy", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["template_id"], ["formula_templates.id"], ondelete="CASCADE"),
        # No ondelete on the next two: deleting a referenced commodity index or
        # input template must fail loudly, not silently orphan formula lines.
        sa.ForeignKeyConstraint(["commodity_id"], ["commodity_indexes.id"]),
        sa.ForeignKeyConstraint(["input_template_id"], ["formula_templates.id"]),
        sa.CheckConstraint(
            "component_type IN ('index', 'fixed', 'formula')",
            name="ck_ftc_component_type",
        ),
        sa.CheckConstraint(
            "(component_type = 'index' AND commodity_id IS NOT NULL AND input_template_id IS NULL)"
            " OR (component_type = 'formula' AND input_template_id IS NOT NULL AND commodity_id IS NULL)"
            " OR (component_type = 'fixed' AND commodity_id IS NULL AND input_template_id IS NULL)",
            name="ck_ftc_target_coherence",
        ),
        sa.CheckConstraint(
            "input_template_id IS NULL OR input_template_id <> template_id",
            name="ck_ftc_no_self_reference",
        ),
    )
    op.create_index("ix_ftc_template_id", "formula_template_components", ["template_id"])
    op.create_index("ix_ftc_input_template_id", "formula_template_components", ["input_template_id"])

    # ── 2. Per-(formula x region) coverage ("combos") ────────────────────────
    op.create_table(
        "formula_region_coverage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("region", sa.String(length=20), nullable=False),
        sa.Column("base_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("margin_pct", sa.Numeric(7, 4), nullable=True),
        sa.Column("base_year", sa.SmallInteger(), nullable=True),
        sa.Column("base_quarter", sa.SmallInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["template_id"], ["formula_templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["region"], ["regions.code"]),
        sa.UniqueConstraint("template_id", "region", name="uq_frc_template_region"),
        sa.CheckConstraint(
            "base_quarter IS NULL OR base_quarter BETWEEN 1 AND 4",
            name="ck_frc_base_quarter",
        ),
    )

    # ── 3. A purely-weighted template carries no expression ──────────────────
    op.alter_column("formula_templates", "expression", existing_type=sa.Text(), nullable=True)

    # ── 4. RLS: visibility follows the parent template ───────────────────────
    for table in ("formula_template_components", "formula_region_coverage"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL
            USING ({_BYPASS} OR {_TEMPLATE_VISIBLE})
            WITH CHECK ({_BYPASS} OR {_TEMPLATE_VISIBLE})
        """)


def downgrade() -> None:
    for table in ("formula_template_components", "formula_region_coverage"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Expression-less (purely weighted) templates block the NOT NULL restore.
    op.execute("UPDATE formula_templates SET expression = '' WHERE expression IS NULL")
    op.alter_column("formula_templates", "expression", existing_type=sa.Text(), nullable=False)

    op.drop_table("formula_region_coverage")

    op.drop_index("ix_ftc_input_template_id", table_name="formula_template_components")
    op.drop_index("ix_ftc_template_id", table_name="formula_template_components")
    op.drop_table("formula_template_components")
