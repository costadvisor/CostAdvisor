"""commodity_indexes -> taxonomy link (Scrum 17 follow-up)

Grouping the Index Library grid by family has been a display-layer mapping
(IndexLibraryArea.jsx's CATEGORY_OVERRIDES/CATEGORY_RULES over free-text
`category`), not real data. This adds family_id/subfamily_id so a "Used by"
column can be a real FK read instead of an N+1-prone join through
FormulaTemplateComponent -> FormulaTemplate.family_id on every grid render.

NULL = no derivable family (a custom/legacy commodity with no recipe usage,
or one whose recipes disagree on family) — the mapping pass populates these
best-effort from existing usage, it never fabricates a family for a
commodity nobody's formula references. ON DELETE SET NULL: deleting a
family must never take a commodity index down with it.

Revision ID: idxfam1a2b3c4d5e
Revises: val1a2b3c4d5e
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "idxfam1a2b3c4d5e"
down_revision: Union[str, None] = "val1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("commodity_indexes", sa.Column("family_id", sa.Integer(), nullable=True))
    op.add_column("commodity_indexes", sa.Column("subfamily_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_commodity_indexes_family", "commodity_indexes", "chemical_families",
        ["family_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_commodity_indexes_subfamily", "commodity_indexes", "subfamilies",
        ["subfamily_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_commodity_indexes_family_id", "commodity_indexes", ["family_id"])
    op.create_index("ix_commodity_indexes_subfamily_id", "commodity_indexes", ["subfamily_id"])


def downgrade() -> None:
    op.drop_index("ix_commodity_indexes_subfamily_id", table_name="commodity_indexes")
    op.drop_index("ix_commodity_indexes_family_id", table_name="commodity_indexes")
    op.drop_constraint("fk_commodity_indexes_subfamily", "commodity_indexes", type_="foreignkey")
    op.drop_constraint("fk_commodity_indexes_family", "commodity_indexes", type_="foreignkey")
    op.drop_column("commodity_indexes", "subfamily_id")
    op.drop_column("commodity_indexes", "family_id")
