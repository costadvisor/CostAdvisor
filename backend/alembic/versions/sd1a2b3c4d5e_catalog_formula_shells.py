"""Catalog formula shells: code + taxonomy links + catalog_meta (Scrum 59)

SEED-1 loads the 257-formula catalog as platform FormulaTemplate rows. That
needs three things the table didn't have:

- code: the catalog formula_id (e.g. OLE-FAC-SAT) — the stable upsert key so
  re-running the loader updates rows in place instead of duplicating. Unique
  among platform rows only (a team fork keeps its origin's code, same rule as
  chemical_families.code).
- family_id / subfamily_id: the taxonomy spine links (family -> subfamily ->
  formula). subfamily_id stays NULL for now — the reference workbook doesn't
  carry the formula->subfamily mapping yet.
- catalog_meta (JSONB): form / coverage_tier / data_confidence / region_count
  from the reference drop; SEED-2 reads data_confidence to gate low-confidence
  rows.

Revision ID: sd1a2b3c4d5e
Revises: wc1a2b3c4d5e
Create Date: 2026-07-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "sd1a2b3c4d5e"
down_revision: Union[str, None] = "wc1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("formula_templates", sa.Column("code", sa.String(length=32), nullable=True))
    op.add_column("formula_templates", sa.Column("family_id", sa.Integer(), nullable=True))
    op.add_column("formula_templates", sa.Column("subfamily_id", sa.Integer(), nullable=True))
    op.add_column("formula_templates", sa.Column("catalog_meta", postgresql.JSONB(), nullable=True))
    op.create_foreign_key(
        "fk_formula_templates_family", "formula_templates", "chemical_families",
        ["family_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_formula_templates_subfamily", "formula_templates", "subfamilies",
        ["subfamily_id"], ["id"], ondelete="SET NULL",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_formula_templates_platform_code ON formula_templates (code) "
        "WHERE team_id IS NULL AND code IS NOT NULL"
    )
    op.create_index("ix_formula_templates_family_id", "formula_templates", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_formula_templates_family_id", table_name="formula_templates")
    op.execute("DROP INDEX IF EXISTS uq_formula_templates_platform_code")
    op.drop_constraint("fk_formula_templates_subfamily", "formula_templates", type_="foreignkey")
    op.drop_constraint("fk_formula_templates_family", "formula_templates", type_="foreignkey")
    op.drop_column("formula_templates", "catalog_meta")
    op.drop_column("formula_templates", "subfamily_id")
    op.drop_column("formula_templates", "family_id")
    op.drop_column("formula_templates", "code")
