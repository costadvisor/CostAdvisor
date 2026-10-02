"""Scrum 27 — nested cost models ("Lego" formulas)

formula_components.child_cost_model_id: a component that IS another cost
model. ON DELETE RESTRICT, not SET NULL — a deleted child would silently
change the parent's price, so the delete is refused instead, the same choice
the catalog already makes for a template used as an input.

component_type gains "model". The CHECK that admits only index/fixed/NULL
was added by lnk1a2b3c4d5e and is NOT declared on the model class, so it is
invisible from app/models/cost_model.py — found by the insert failing, not by
reading. Recreated rather than dropped: a value the app does not understand
should still be rejected at the database.

Revision ID: lego1a2b3c4d5e
Revises: pu1a2b3c4d5e
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "lego1a2b3c4d5e"
down_revision: Union[str, None] = "pu1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "formula_components",
        sa.Column("child_cost_model_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_formula_components_child_cost_model",
        "formula_components", "cost_models",
        ["child_cost_model_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index(
        "ix_formula_components_child_cost_model",
        "formula_components", ["child_cost_model_id"],
    )

    op.drop_constraint("ck_formula_components_component_type", "formula_components", type_="check")
    op.create_check_constraint(
        "ck_formula_components_component_type", "formula_components",
        "component_type IS NULL OR component_type IN ('index', 'fixed', 'model')",
    )


def downgrade() -> None:
    # Any nested line has to go before the narrower CHECK can be restored,
    # or the downgrade fails on rows the old vocabulary cannot describe.
    op.execute("DELETE FROM formula_components WHERE component_type = 'model'")
    op.drop_constraint("ck_formula_components_component_type", "formula_components", type_="check")
    op.create_check_constraint(
        "ck_formula_components_component_type", "formula_components",
        "component_type IS NULL OR component_type IN ('index', 'fixed')",
    )
    op.drop_index("ix_formula_components_child_cost_model", table_name="formula_components")
    op.drop_constraint("fk_formula_components_child_cost_model", "formula_components", type_="foreignkey")
    op.drop_column("formula_components", "child_cost_model_id")
