"""Per-region component lines + combo confidence/review fields (Scrum 60)

SEED-2 loads the 676 weighted combos as real formula components. Two schema
gaps close here:

- formula_template_components gains a nullable `region`: the source recipes
  genuinely differ per region (a combo is formula x region), so a seeded line
  set is keyed (template, region). NULL keeps meaning "applies to all
  regions" — the Scrum 58 API-authored lines — and the resolver falls back
  region-specific -> NULL.
- formula_region_coverage gains the trust layer: data_confidence
  (CONF-HIGH/MED/LOW), coverage_tier (worst retrieval tier among the combo's
  inputs), needs_review (CONF-LOW rows load flagged — placeholders, not
  facts), reviewed_by/reviewed_at for the expert pass, and review_metadata
  (the correction_plan_log entry for the formula, so the reviewer has the
  reasoning on hand instead of reverse-engineering it).

Revision ID: cm1a2b3c4d5e
Revises: sd1a2b3c4d5e
Create Date: 2026-07-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "cm1a2b3c4d5e"
down_revision: Union[str, None] = "sd1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("formula_template_components",
                  sa.Column("region", sa.String(length=20), nullable=True))
    op.create_foreign_key(
        "fk_ftc_region", "formula_template_components", "regions",
        ["region"], ["code"],
    )
    op.create_index("ix_ftc_template_region", "formula_template_components",
                    ["template_id", "region"])

    op.add_column("formula_region_coverage",
                  sa.Column("data_confidence", sa.String(length=16), nullable=True))
    op.add_column("formula_region_coverage",
                  sa.Column("coverage_tier", sa.String(length=16), nullable=True))
    op.add_column("formula_region_coverage",
                  sa.Column("needs_review", sa.Boolean(), nullable=False,
                            server_default=sa.text("false")))
    op.add_column("formula_region_coverage",
                  sa.Column("reviewed_by", sa.String(length=128), nullable=True))
    op.add_column("formula_region_coverage",
                  sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("formula_region_coverage",
                  sa.Column("review_metadata", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("formula_region_coverage", "review_metadata")
    op.drop_column("formula_region_coverage", "reviewed_at")
    op.drop_column("formula_region_coverage", "reviewed_by")
    op.drop_column("formula_region_coverage", "needs_review")
    op.drop_column("formula_region_coverage", "coverage_tier")
    op.drop_column("formula_region_coverage", "data_confidence")

    op.drop_index("ix_ftc_template_region", table_name="formula_template_components")
    op.drop_constraint("fk_ftc_region", "formula_template_components", type_="foreignkey")
    op.drop_column("formula_template_components", "region")
