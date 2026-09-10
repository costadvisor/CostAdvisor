"""Scrum 28b — link priced cost models to the library recipe

FormulaVersion gains source_coverage_id (which catalog combo it was priced
from) + link_mode ("pinned" = frozen forever, "tracking" = recomputed live
from the catalog recipe on every evaluation). Both NULL for anything not
catalog-linked — the entire new code path is skipped in that case, so this
is a no-op for every existing cost model.

FormulaComponent gains component_type ("index"|"fixed"|NULL) so
commodity_id IS NULL stops being ambiguous between "deliberately a fixed
line" and "was supposed to be index-linked but the name-match failed" —
plus depth/via_template_id/line_region, provenance-only fields carried
through for inspection (never read by the calculation path).

All columns nullable, all new FKs ON DELETE SET NULL — a deleted coverage
or template orphans the reference rather than breaking the version/component
it's attached to.

Revision ID: lnk1a2b3c4d5e
Revises: srt1a2b3c4d5e
Create Date: 2026-08-22

"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "lnk1a2b3c4d5e"
down_revision: Union[str, None] = "srt1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("formula_versions", sa.Column(
        "source_coverage_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("formula_versions", sa.Column(
        "link_mode", sa.String(length=16), nullable=True))
    op.create_foreign_key(
        "fk_formula_versions_source_coverage", "formula_versions",
        "formula_region_coverage", ["source_coverage_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_formula_versions_link_mode",
        "formula_versions",
        "link_mode IS NULL OR link_mode IN ('pinned', 'tracking')",
    )

    op.add_column("formula_components", sa.Column(
        "component_type", sa.String(length=16), nullable=True))
    op.add_column("formula_components", sa.Column(
        "depth", sa.SmallInteger(), nullable=True))
    op.add_column("formula_components", sa.Column(
        "via_template_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("formula_components", sa.Column(
        "line_region", sa.String(length=20), nullable=True))
    op.add_column("formula_components", sa.Column(
        "is_proxy", sa.Boolean(), nullable=True))
    op.create_foreign_key(
        "fk_formula_components_via_template", "formula_components",
        "formula_templates", ["via_template_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_formula_components_component_type",
        "formula_components",
        "component_type IS NULL OR component_type IN ('index', 'fixed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_formula_components_component_type", "formula_components", type_="check")
    op.drop_constraint("fk_formula_components_via_template", "formula_components", type_="foreignkey")
    op.drop_column("formula_components", "is_proxy")
    op.drop_column("formula_components", "line_region")
    op.drop_column("formula_components", "via_template_id")
    op.drop_column("formula_components", "depth")
    op.drop_column("formula_components", "component_type")

    op.drop_constraint("ck_formula_versions_link_mode", "formula_versions", type_="check")
    op.drop_constraint("fk_formula_versions_source_coverage", "formula_versions", type_="foreignkey")
    op.drop_column("formula_versions", "link_mode")
    op.drop_column("formula_versions", "source_coverage_id")
