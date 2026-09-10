"""Scrum 74 / 3b — variant on the recipe line

The previous revision put `variant` on the coverage row, which is where the
ticket said the uniqueness problem was. Loading then showed the dimension
reaches one level deeper: a combo is keyed (formula, region, variant) but its
lines were keyed only (template, region), so the two variants of a formula
overwrote each other's recipes on every run — the loader reported 20 lines
created and 40 deleted in perpetuity instead of settling.

They are genuinely different recipes, not a duplicate: bentonite
activated-vs-natural carries margin 15 vs 12, talc treated-vs-untreated 14 vs
12. Without this column one of each pair is silently lost.

NOT NULL DEFAULT '' for the same reason as the coverage column: it takes part
in a lookup key, and a NULL would make two rows that should collide compare as
distinct.

Revision ID: cat3c1a2b3c4d
Revises: cat3b1a2b3c4d
Create Date: 2026-08-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "cat3c1a2b3c4d"
down_revision: Union[str, None] = "cat3b1a2b3c4d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "formula_template_components",
        sa.Column("variant", sa.String(length=32), nullable=False, server_default=""),
    )
    # The loader replaces a line set by (template, region, variant), so this is
    # the shape it reads back.
    op.create_index(
        "ix_ftc_template_region_variant",
        "formula_template_components",
        ["template_id", "region", "variant"],
    )


def downgrade() -> None:
    op.drop_index("ix_ftc_template_region_variant", table_name="formula_template_components")
    op.drop_column("formula_template_components", "variant")
