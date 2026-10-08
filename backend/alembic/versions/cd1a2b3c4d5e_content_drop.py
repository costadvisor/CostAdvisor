"""Content drop: catalogue status, maker evidence, demand axis, reports, strategy.

Revision ID: cd1a2b3c4d5e
Revises: tax2a1b2c3d4e
Create Date: 2026-10-08

The data layer the content loaders (taxonomy, indexes, catalogue, content,
reports, playbooks) write into, on top of the taxonomy spine:

* **Widenings** where the drop's text overflows the old widths: series name,
  provider and category; type-code labels; index-card category, access and
  agency; cost-line names.
* **Catalogue** (`formula_templates`): product metadata (`cas_number`, `form`,
  `volatile`, `reference_grade`, `full_name`), groups (`is_group`,
  `group_members`, `absorbed_into`), the internal `archival_note`; and two
  separate facts per card: `card_kind` (product, group, absorbed, pointer,
  duplicate, withdrawn) and `supply_status` (live, supply_exception,
  supply_pending, not_audited; NULL for non-listed kinds), with
  `supply_status_detail`, `redirect_to` and `internal_meta` (prose and
  provenance that no API schema maps). `formula_region_coverage.withdrawn_at`
  hides a combo that left the drop without deleting it.
  `formula_template_components.cost_category` (+ CHECK).
* **Makers**: `producers.is_bucket`; `producer_formulas` gets the maker
  evidence columns and the derived `counts_toward_floor`, `evidence_label` and
  `row_order`. No quote, source, share or audit column is added: what is not
  stored cannot be served.
* **Demand axis** (industries, categories, shared objects, members, refs,
  build items, out rows with `to_industries`, placements on
  `product_line_id`), **reports** (`market_report_lines.product_line_id`) and
  **playbooks**: platform tables, no `team_id`, no RLS.
* **Strategy team state**: five strict-tenant tables (USING and WITH CHECK,
  FORCE ROW LEVEL SECURITY). The 8 gemstones and 7 objectives are fixed
  vocabulary, seeded here.
* `strategy.view` / `strategy.edit`, granted to the Dream Plan (the plan
  ceiling applies before roles), the SuperAdmin role and the per-team
  Owner / Admin / Member roles.
* `content_loads`: one row per real content load (source commit and date).

Downgrade works with content loaded. It drops everything above. Before the
widened columns are narrowed back, over-long values on platform rows are cut
to the old width (a series name keeps a short hash so it stays unique). Those
rows are reference data that team cost models can point at, so they are kept
rather than deleted, and the content loader rewrites them. Team rows are
never changed: a team template's cost line that no longer fits stops the
downgrade.
"""
import uuid as _uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "cd1a2b3c4d5e"
down_revision: Union[str, None] = "tax2a1b2c3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UID = "(NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid"
_BYPASS = "current_setting('app.bypass_rls'::text, true) = 'on'::text"
_MEMBER_OF = (
    "team_id IN (SELECT team_memberships.team_id FROM team_memberships "
    f"WHERE team_memberships.user_id = {_UID})"
)

STRATEGY_PERMS = {
    "strategy.view": ("View Strategy", "strategy", "view"),
    "strategy.edit": ("Edit Strategy", "strategy", "edit"),
}

TEAM_TABLES = (
    "strategy_records", "team_objectives", "lever_scores", "custom_levers",
    "strategy_actions",
)

# Frozen copy of app/models/strategy.py GEMSTONES / OBJECTIVES: a migration must
# not change meaning when the model module changes later.
GEMSTONES = [
    ("VC", "Volume Concentration", "#0F6E56"),
    ("PC", "Pricing & Conditions", "#0B6E6E"),
    ("GS", "Global Sourcing", "#146B8C"),
    ("CM", "Category Management", "#3D6B99"),
    ("PS", "Product Spec Improvement", "#BA7517"),
    ("JP", "Joint Process Improvement", "#A0522D"),
    ("RR", "Relationship Restructuring", "#534AB7"),
    ("DF", "Differentiation", "#8C3A6B"),
]
OBJECTIVES = [
    ("cost_reduction", "Cost Reduction",
     "Unit price, TCO, index-linked mechanisms, rebates/discounts"),
    ("cash_working_capital", "Cash / Working Capital",
     "Payment terms (DPO), inventory optimization, consignment stock, VMI"),
    ("supply_security", "Supply Security",
     "Dual sourcing, geographic diversification, contingency planning"),
    ("quality", "Quality",
     "Spec consistency, compliance, COA/traceability"),
    ("innovation", "Innovation",
     "Joint development, early supplier involvement, access to new chemistries"),
    ("sustainability_esg", "Sustainability & ESG",
     "Carbon footprint, EcoVadis, circular feedstocks"),
    ("simplification", "Simplification",
     "SKU/spec rationalization, supplier base rationalization"),
]

# Frozen copies of app/models/formula_template.py CARD_KINDS / SUPPLY_STATUSES.
CARD_KINDS = ("product", "group", "absorbed", "pointer", "duplicate", "withdrawn")
SUPPLY_STATUSES = ("live", "supply_exception", "supply_pending", "not_audited")

_LEVER_STATUS_SQL = "'Identified', 'Under evaluation', 'Approved', 'Actioned', 'Rejected'"
_ACTION_STATUS_SQL = "'Not started', 'In progress', 'Done', 'Blocked'"
_PRIORITY_SQL = "'High', 'Medium', 'Low'"

# (table, column, narrow type, narrow length or None for no limit, wide type)
WIDENINGS = [
    ("commodity_indexes", "name", sa.String(length=64), 64, sa.String(length=128)),
    ("commodity_indexes", "provider", sa.String(length=255), 255, sa.Text()),
    ("commodity_indexes", "category", sa.String(length=64), 64, sa.String(length=255)),
    ("type_codes", "label", sa.String(length=128), 128, sa.String(length=255)),
    ("index_cards", "category", sa.String(length=64), 64, sa.String(length=255)),
    ("index_cards", "access", sa.String(length=32), 32, sa.String(length=255)),
    ("index_cards", "agency", sa.String(length=255), 255, sa.Text()),
    ("formula_template_components", "name", sa.String(length=64), 64, sa.String(length=255)),
]
_NOT_NULL = {("commodity_indexes", "name"), ("formula_template_components", "name")}

# formula_templates columns, in the order they are added.
TEMPLATE_COLUMNS = (
    "cas_number", "form", "volatile", "reference_grade", "archival_note", "is_group",
    "group_members", "absorbed_into", "full_name", "card_kind", "supply_status",
    "supply_status_detail", "redirect_to", "internal_meta",
)
# producer_formulas columns, in the order they are added.
PRODUCER_FORMULA_COLUMNS = (
    "role", "maker_evidence", "counted", "counts_toward_floor", "evidence_label",
    "weak_reading", "corp_group", "integration_status", "integrated", "integration_basis",
    "origin_restriction", "floor_eligible_eu", "region_uncertain", "sites", "row_order",
)


def _now_col(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True),
                     server_default=sa.text("now()"), nullable=False)


def _check_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_MEMBER_OF})
        WITH CHECK ({_BYPASS} OR {_MEMBER_OF})
    """)


def upgrade() -> None:
    # ── Widenings ────────────────────────────────────────────────────────────
    for table, column, narrow, _, wide in WIDENINGS:
        op.alter_column(table, column, existing_type=narrow, type_=wide,
                        existing_nullable=(table, column) not in _NOT_NULL)

    # ── Catalogue ────────────────────────────────────────────────────────────
    for col in (
        sa.Column("cas_number", sa.String(length=64), nullable=True),
        sa.Column("form", sa.String(length=128), nullable=True),
        sa.Column("volatile", sa.Boolean(), nullable=True),
        sa.Column("reference_grade", sa.Text(), nullable=True),
        sa.Column("archival_note", sa.Text(), nullable=True),
        sa.Column("is_group", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("group_members", postgresql.JSONB(), nullable=True),
        sa.Column("absorbed_into", sa.String(length=64), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("card_kind", sa.String(length=16), server_default="product", nullable=False),
        sa.Column("supply_status", sa.String(length=24), nullable=True),
        sa.Column("supply_status_detail", postgresql.JSONB(), nullable=True),
        sa.Column("redirect_to", sa.String(length=64), nullable=True),
        sa.Column("internal_meta", postgresql.JSONB(), nullable=True),
    ):
        op.add_column("formula_templates", col)
    op.create_check_constraint("ck_formula_templates_card_kind", "formula_templates",
                               _check_in("card_kind", CARD_KINDS))
    op.create_check_constraint(
        "ck_formula_templates_supply_status", "formula_templates",
        f"supply_status IS NULL OR {_check_in('supply_status', SUPPLY_STATUSES)}")

    op.add_column("formula_region_coverage",
                  sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("formula_template_components",
                  sa.Column("cost_category", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        "ck_ftc_cost_category", "formula_template_components",
        "cost_category IS NULL OR cost_category IN "
        "('feedstock', 'utility', 'margin', 'fixed', 'packaging')",
    )

    # ── Makers ───────────────────────────────────────────────────────────────
    op.add_column("producers", sa.Column(
        "is_bucket", sa.Boolean(), server_default="false", nullable=False))
    for col in (
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("maker_evidence", sa.String(length=24), nullable=True),
        sa.Column("counted", sa.Boolean(), nullable=True),
        sa.Column("counts_toward_floor", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("evidence_label", sa.String(length=16), server_default="not_audited",
                  nullable=False),
        sa.Column("weak_reading", sa.Boolean(), nullable=True),
        sa.Column("corp_group", sa.String(length=255), nullable=True),
        sa.Column("integration_status", sa.String(length=32), nullable=True),
        sa.Column("integrated", sa.Boolean(), nullable=True),
        sa.Column("integration_basis", sa.Text(), nullable=True),
        sa.Column("origin_restriction", sa.String(length=32), nullable=True),
        sa.Column("floor_eligible_eu", sa.Boolean(), nullable=True),
        sa.Column("region_uncertain", sa.Boolean(), nullable=True),
        sa.Column("sites", postgresql.JSONB(), nullable=True),
        sa.Column("row_order", sa.Integer(), server_default="0", nullable=False),
    ):
        op.add_column("producer_formulas", col)

    # ── Demand axis ──────────────────────────────────────────────────────────
    op.create_table(
        "industries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("slug", sa.String(length=64), nullable=False, unique=True),
        sa.Column("anchor", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("ratified_on", sa.Date(), nullable=True),
        sa.Column("scope_status", sa.String(length=32), nullable=True),
        sa.Column("buyer_one_line", sa.Text(), nullable=True),
        sa.Column("buyer", sa.Text(), nullable=True),
        sa.Column("in_scope", postgresql.JSONB(), nullable=True),
        sa.Column("out_of_scope", postgresql.JSONB(), nullable=True),
        sa.Column("boundaries", postgresql.JSONB(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("industry_id", sa.Integer(),
                  sa.ForeignKey("industries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False, unique=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("alias", sa.Text(), nullable=True),
        sa.Column("fn", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("src", sa.Text(), nullable=True),
        sa.Column("extra", postgresql.JSONB(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("status IN ('servable', 'partial', 'build')",
                           name="ck_category_status"),
    )
    op.create_index("ix_categories_industry_id", "categories", ["industry_id"])

    op.create_table(
        "category_shared",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("fn", sa.String(length=128), nullable=True),
        sa.Column("owner", sa.String(length=255), nullable=True),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("extra", postgresql.JSONB(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "category_shared_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shared_id", sa.Integer(),
                  sa.ForeignKey("category_shared.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_key", sa.String(length=255), nullable=False),
        sa.Column("pids", postgresql.JSONB(), nullable=True),
        sa.Column("is_whole_line", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("shared_id", "sort_order", name="uq_category_shared_member_pos"),
    )
    op.create_index("ix_category_shared_members_line_key", "category_shared_members",
                    ["line_key"])
    op.create_table(
        "category_refs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shared_id", sa.Integer(),
                  sa.ForeignKey("category_shared.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pids", postgresql.JSONB(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("category_id", "shared_id", name="uq_category_ref"),
    )
    op.create_table(
        "category_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_key", sa.String(length=255), nullable=False),
        sa.Column("pids", postgresql.JSONB(), nullable=True),
        sa.Column("is_whole_line", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("extra", postgresql.JSONB(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("category_id", "sort_order", name="uq_category_member_pos"),
    )
    op.create_index("ix_category_members_line_key", "category_members", ["line_key"])
    op.create_table(
        "category_build_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.UniqueConstraint("category_id", "seq", name="uq_category_build_item_seq"),
    )
    op.create_table(
        "industry_out",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("industry_id", sa.Integer(),
                  sa.ForeignKey("industries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pid", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("to_industry", sa.String(length=128), nullable=True),
        sa.Column("to_industries", postgresql.JSONB(), nullable=True),
        sa.Column("to_was", sa.String(length=128), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("industry_id", "pid", name="uq_industry_out_pid"),
    )
    op.create_index("ix_industry_out_pid", "industry_out", ["pid"])
    op.create_table(
        "category_placements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("industry_id", sa.Integer(),
                  sa.ForeignKey("industries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_key", sa.String(length=255), nullable=True),
        sa.Column("product_line_id", sa.Integer(),
                  sa.ForeignKey("product_lines.id", ondelete="SET NULL"), nullable=True),
        sa.Column("pid", sa.String(length=64), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("formula_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("fn", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("via_shared_id", sa.Integer(),
                  sa.ForeignKey("category_shared.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("category_id", "pid", name="uq_category_placement_pid"),
    )
    op.create_index("ix_category_placements_pid", "category_placements", ["pid"])
    op.create_index("ix_category_placements_line_key", "category_placements", ["line_key"])
    op.create_index("ix_category_placements_industry_id", "category_placements",
                    ["industry_id"])
    op.create_index("ix_category_placements_product_line_id", "category_placements",
                    ["product_line_id"])

    # ── Market reports ───────────────────────────────────────────────────────
    op.create_table(
        "market_reports",
        sa.Column("slug", sa.String(length=64), primary_key=True),
        sa.Column("report_file", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("family", sa.String(length=128), nullable=True),
        sa.Column("old_line_name", sa.String(length=255), nullable=True),
        sa.Column("old_line_key", sa.String(length=255), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("in_v1_scope", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("kraljic", postgresql.JSONB(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True),
        _now_col("created_at"),
        _now_col("updated_at"),
    )
    op.create_table(
        "market_report_sections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(length=64),
                  sa.ForeignKey("market_reports.slug", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", sa.String(length=32), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("heading", sa.String(length=255), nullable=True),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("src_block_count", sa.Integer(), nullable=True),
        sa.UniqueConstraint("slug", "section_id", name="uq_market_report_section"),
    )
    op.create_table(
        "market_report_lines",
        sa.Column("slug", sa.String(length=64),
                  sa.ForeignKey("market_reports.slug", ondelete="CASCADE"), primary_key=True),
        sa.Column("line_key", sa.String(length=255), primary_key=True),
        sa.Column("product_line_id", sa.Integer(),
                  sa.ForeignKey("product_lines.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.CheckConstraint("source IN ('report_map', 'key_map')",
                           name="ck_market_report_line_source"),
    )
    op.create_index("ix_market_report_lines_line_key", "market_report_lines", ["line_key"])
    op.create_index("ix_market_report_lines_product_line_id", "market_report_lines",
                    ["product_line_id"])
    op.create_table(
        "market_report_panels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(length=64),
                  sa.ForeignKey("market_reports.slug", ondelete="CASCADE"), nullable=False),
        sa.Column("panel", sa.String(length=32), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("heading", sa.String(length=255), nullable=True),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("slug", "panel", name="uq_market_report_panel"),
    )

    # ── Playbooks (platform) ─────────────────────────────────────────────────
    gem = op.create_table(
        "gemstone_categories",
        sa.Column("code", sa.String(length=2), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("color", sa.String(length=16), nullable=True),
    )
    obj = op.create_table(
        "strategic_objectives",
        sa.Column("code", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
    )
    op.bulk_insert(gem, [
        {"code": c, "name": n, "sort_order": i, "color": col}
        for i, (c, n, col) in enumerate(GEMSTONES)
    ])
    op.bulk_insert(obj, [
        {"code": c, "name": n, "description": d, "sort_order": i}
        for i, (c, n, d) in enumerate(OBJECTIVES)
    ])

    op.create_table(
        "playbooks",
        sa.Column("slug", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("family", sa.String(length=128), nullable=True),
        sa.Column("report_slug", sa.String(length=64), nullable=True),
        sa.Column("legacy_line_key", sa.String(length=255), nullable=True),
        sa.Column("kraljic", postgresql.JSONB(), nullable=True),
        sa.Column("last_updated", sa.Date(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        _now_col("created_at"),
        _now_col("updated_at"),
    )
    op.create_index("ix_playbooks_report_slug", "playbooks", ["report_slug"])
    op.create_table(
        "playbook_levers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("playbook_slug", sa.String(length=64),
                  sa.ForeignKey("playbooks.slug", ondelete="CASCADE"), nullable=False),
        sa.Column("lever_code", sa.String(length=64), nullable=False, unique=True),
        sa.Column("gemstone_code", sa.String(length=2),
                  sa.ForeignKey("gemstone_categories.code"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("guidance", sa.Text(), nullable=True),
        sa.Column("default_applies", sa.Boolean(), nullable=True),
        sa.Column("default_ease", sa.SmallInteger(), nullable=True),
        sa.Column("savings_score", sa.SmallInteger(), nullable=True),
        sa.Column("default_status", sa.String(length=32), nullable=True),
        sa.Column("default_notes", sa.Text(), nullable=True),
        sa.Column("scales", postgresql.JSONB(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("default_ease IS NULL OR default_ease BETWEEN 1 AND 5",
                           name="ck_pbl_default_ease"),
        sa.CheckConstraint("savings_score IS NULL OR savings_score BETWEEN 1 AND 5",
                           name="ck_pbl_savings_score"),
        sa.CheckConstraint(f"default_status IS NULL OR default_status IN ({_LEVER_STATUS_SQL})",
                           name="ck_pbl_default_status"),
    )
    op.create_index("ix_playbook_levers_playbook", "playbook_levers", ["playbook_slug"])
    op.create_table(
        "playbook_lever_objectives",
        sa.Column("lever_id", sa.Integer(),
                  sa.ForeignKey("playbook_levers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("objective_code", sa.String(length=32),
                  sa.ForeignKey("strategic_objectives.code"), primary_key=True),
    )
    op.create_table(
        "playbook_objectives",
        sa.Column("playbook_slug", sa.String(length=64),
                  sa.ForeignKey("playbooks.slug", ondelete="CASCADE"), primary_key=True),
        sa.Column("objective_code", sa.String(length=32),
                  sa.ForeignKey("strategic_objectives.code"), primary_key=True),
        sa.Column("priority", sa.String(length=16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
        sa.CheckConstraint(f"priority IS NULL OR priority IN ({_PRIORITY_SQL})",
                           name="ck_pbo_priority"),
    )

    # ── Strategy team state (strict tenant) ──────────────────────────────────
    def _team_id():
        return sa.Column("team_id", postgresql.UUID(as_uuid=True),
                         sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)

    def _playbook_slug():
        return sa.Column("playbook_slug", sa.String(length=64),
                         sa.ForeignKey("playbooks.slug", ondelete="CASCADE"), nullable=False)

    def _user_fk(name):
        return sa.Column(name, postgresql.UUID(as_uuid=True),
                         sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    op.create_table(
        "strategy_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _team_id(),
        _playbook_slug(),
        _user_fk("owner_user_id"),
        sa.Column("status", sa.String(length=32), server_default="Active", nullable=False),
        _now_col("created_at"),
        _now_col("updated_at"),
        sa.UniqueConstraint("team_id", "playbook_slug", name="uq_strategy_record_team_playbook"),
    )
    op.create_index("ix_strategy_records_team_id", "strategy_records", ["team_id"])

    op.create_table(
        "team_objectives",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _team_id(),
        _playbook_slug(),
        sa.Column("objective_code", sa.String(length=32),
                  sa.ForeignKey("strategic_objectives.code"), nullable=False),
        sa.Column("selected", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        _user_fk("updated_by"),
        _now_col("updated_at"),
        sa.UniqueConstraint("team_id", "playbook_slug", "objective_code",
                            name="uq_team_objective"),
        sa.CheckConstraint(f"priority IS NULL OR priority IN ({_PRIORITY_SQL})",
                           name="ck_team_objective_priority"),
    )
    op.create_index("ix_team_objectives_team_id", "team_objectives", ["team_id"])

    op.create_table(
        "lever_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _team_id(),
        sa.Column("lever_id", sa.Integer(),
                  sa.ForeignKey("playbook_levers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("applies", sa.Boolean(), nullable=True),
        sa.Column("ease", sa.SmallInteger(), nullable=True),
        sa.Column("savings_score", sa.SmallInteger(), nullable=True),
        sa.Column("savings_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _user_fk("updated_by"),
        _now_col("updated_at"),
        sa.UniqueConstraint("team_id", "lever_id", name="uq_lever_score_team_lever"),
        sa.CheckConstraint("ease IS NULL OR ease BETWEEN 1 AND 5", name="ck_lever_score_ease"),
        sa.CheckConstraint("savings_score IS NULL OR savings_score BETWEEN 1 AND 5",
                           name="ck_lever_score_savings_score"),
        sa.CheckConstraint(f"status IS NULL OR status IN ({_LEVER_STATUS_SQL})",
                           name="ck_lever_score_status"),
    )
    op.create_index("ix_lever_scores_team_id", "lever_scores", ["team_id"])

    op.create_table(
        "custom_levers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _team_id(),
        _playbook_slug(),
        sa.Column("gemstone_code", sa.String(length=2),
                  sa.ForeignKey("gemstone_categories.code"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("guidance", sa.Text(), nullable=True),
        sa.Column("applies", sa.Boolean(), nullable=True),
        sa.Column("ease", sa.SmallInteger(), nullable=True),
        sa.Column("savings_score", sa.SmallInteger(), nullable=True),
        sa.Column("savings_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("objectives", postgresql.JSONB(), nullable=True),
        _user_fk("created_by"),
        _now_col("created_at"),
        _now_col("updated_at"),
        sa.CheckConstraint("ease IS NULL OR ease BETWEEN 1 AND 5", name="ck_custom_lever_ease"),
        sa.CheckConstraint("savings_score IS NULL OR savings_score BETWEEN 1 AND 5",
                           name="ck_custom_lever_savings_score"),
        sa.CheckConstraint(f"status IS NULL OR status IN ({_LEVER_STATUS_SQL})",
                           name="ck_custom_lever_status"),
    )
    op.create_index("ix_custom_levers_team_playbook", "custom_levers",
                    ["team_id", "playbook_slug"])

    op.create_table(
        "strategy_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        _team_id(),
        _playbook_slug(),
        sa.Column("lever_id", sa.Integer(),
                  sa.ForeignKey("playbook_levers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("custom_lever_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("custom_levers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _user_fk("assignee_user_id"),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="Not started", nullable=False),
        sa.Column("pct_complete", sa.SmallInteger(), server_default="0", nullable=False),
        _user_fk("created_by"),
        _now_col("created_at"),
        _now_col("updated_at"),
        sa.CheckConstraint(f"status IN ({_ACTION_STATUS_SQL})",
                           name="ck_strategy_action_status"),
        sa.CheckConstraint("pct_complete BETWEEN 0 AND 100", name="ck_strategy_action_pct"),
    )
    op.create_index("ix_strategy_actions_team_playbook", "strategy_actions",
                    ["team_id", "playbook_slug"])

    for table in TEAM_TABLES:
        _enable_rls(table)

    # ── Content loads (platform) ─────────────────────────────────────────────
    op.create_table(
        "content_loads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_commit", sa.String(length=64), nullable=False),
        sa.Column("source_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_branch", sa.String(length=128), nullable=True),
        sa.Column("extractor_version", sa.String(length=255), nullable=True),
        sa.Column("drop_dir", sa.Text(), nullable=True),
        _now_col("started_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("loaded_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dry_run", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("counts", postgresql.JSONB(), nullable=True),
        sa.Column("notes", postgresql.JSONB(), nullable=True),
    )

    # ── Permissions (the edb1a2b3c4d5e helpers) ──────────────────────────────
    conn = op.get_bind()
    perm_ids = {}
    for key, (label, category, action) in STRATEGY_PERMS.items():
        existing = conn.execute(
            sa.text("SELECT id FROM permissions WHERE key = :k"), {"k": key}
        ).scalar()
        if existing:
            perm_ids[key] = str(existing)
            continue
        pid = str(_uuid.uuid4())
        perm_ids[key] = pid
        conn.execute(sa.text("""
            INSERT INTO permissions (id, key, label, category, action)
            VALUES (:id, :key, :label, :category, :action)
        """), {"id": pid, "key": key, "label": label,
               "category": category, "action": action})

    def grant_plan(plan_name, keys):
        plan = conn.execute(
            sa.text("SELECT id FROM plans WHERE name = :n"), {"n": plan_name}
        ).scalar()
        if not plan:
            return
        for key in keys:
            conn.execute(sa.text("""
                INSERT INTO plan_permissions (plan_id, permission_id) VALUES (:p, :q)
                ON CONFLICT DO NOTHING
            """), {"p": str(plan), "q": perm_ids[key]})

    # The plan ceiling applies before roles: a key missing from the Dream Plan
    # is denied to every non-super-admin.
    grant_plan("Dream Plan", list(STRATEGY_PERMS))

    def grant_role_ids(role_ids, keys):
        for role_id in role_ids:
            for key in keys:
                conn.execute(sa.text("""
                    INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)
                    ON CONFLICT DO NOTHING
                """), {"r": str(role_id), "p": perm_ids[key]})

    superadmin = conn.execute(sa.text(
        "SELECT id FROM roles WHERE team_id IS NULL AND name = 'SuperAdmin'"
    )).scalar()
    if superadmin:
        grant_role_ids([superadmin], list(STRATEGY_PERMS))

    # A member with any custom role skips the membership fallback, so the
    # existing per-team roles need the keys too. View and edit to all three: a
    # strategy is worked by the category team, not only by its admins.
    for role_name in ("Owner", "Admin", "Member"):
        rows = conn.execute(sa.text(
            "SELECT id FROM roles WHERE team_id IS NOT NULL AND name = :n"
        ), {"n": role_name}).fetchall()
        grant_role_ids([r[0] for r in rows], list(STRATEGY_PERMS))


def downgrade() -> None:
    conn = op.get_bind()
    for key in STRATEGY_PERMS:
        pid = conn.execute(
            sa.text("SELECT id FROM permissions WHERE key = :k"), {"k": key}
        ).scalar()
        if not pid:
            continue
        conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :p"),
                     {"p": str(pid)})
        conn.execute(sa.text("DELETE FROM plan_permissions WHERE permission_id = :p"),
                     {"p": str(pid)})
        conn.execute(sa.text("DELETE FROM permissions WHERE id = :p"), {"p": str(pid)})

    op.drop_table("content_loads")
    for table in TEAM_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    # Team tables first (they reference the playbook tables), then platform.
    for table in ("strategy_actions", "custom_levers", "lever_scores", "team_objectives",
                  "strategy_records",
                  "playbook_objectives", "playbook_lever_objectives", "playbook_levers",
                  "playbooks", "strategic_objectives", "gemstone_categories",
                  "market_report_panels", "market_report_lines", "market_report_sections",
                  "market_reports",
                  "category_placements", "industry_out", "category_build_items",
                  "category_members", "category_refs", "category_shared_members",
                  "category_shared", "categories", "industries"):
        op.drop_table(table)

    for column in reversed(PRODUCER_FORMULA_COLUMNS):
        op.drop_column("producer_formulas", column)
    op.drop_column("producers", "is_bucket")

    op.drop_constraint("ck_ftc_cost_category", "formula_template_components", type_="check")
    op.drop_column("formula_template_components", "cost_category")
    op.drop_column("formula_region_coverage", "withdrawn_at")
    op.drop_constraint("ck_formula_templates_supply_status", "formula_templates", type_="check")
    op.drop_constraint("ck_formula_templates_card_kind", "formula_templates", type_="check")
    for column in reversed(TEMPLATE_COLUMNS):
        op.drop_column("formula_templates", column)

    # Cut over-long platform values to the old width, then narrow. Series,
    # type codes and index cards are platform tables; cost lines are cut only
    # on platform templates, so a team row that does not fit stops here.
    for table, column, narrow, length, wide in reversed(WIDENINGS):
        if table == "commodity_indexes" and column == "name":
            # Unique: keep a short hash of the full name.
            op.execute(f"UPDATE {table} SET {column} = left({column}, {length - 9}) || '~' "
                       f"|| left(md5({column}), 8) WHERE length({column}) > {length}")
        elif table == "formula_template_components":
            op.execute(f"UPDATE {table} SET {column} = left({column}, {length}) "
                       f"WHERE length({column}) > {length} AND template_id IN "
                       f"(SELECT id FROM formula_templates WHERE team_id IS NULL)")
        else:
            op.execute(f"UPDATE {table} SET {column} = left({column}, {length}) "
                       f"WHERE length({column}) > {length}")
        op.alter_column(table, column, existing_type=wide, type_=narrow,
                        existing_nullable=(table, column) not in _NOT_NULL)
