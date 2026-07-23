"""Product -> catalog formula link (Scrum 58 auto-load gap)

The Scrum 58 done-when reads "creating a product auto-loads the template by
formula x region". That needs the product to know WHICH catalog formula it is
priced by — this adds the association. NULL = not catalog-linked (products
predating the catalog, or hand-modelled ones). ON DELETE SET NULL: removing a
template must never take products down with it.

Revision ID: pf1a2b3c4d5e
Revises: rgc2b3c4d5e6
Create Date: 2026-07-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "pf1a2b3c4d5e"
down_revision: Union[str, None] = "rgc2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("formula_template_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_products_formula_template", "products", "formula_templates",
        ["formula_template_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_products_formula_template_id", "products", ["formula_template_id"])


def downgrade() -> None:
    op.drop_index("ix_products_formula_template_id", table_name="products")
    op.drop_constraint("fk_products_formula_template", "products", type_="foreignkey")
    op.drop_column("products", "formula_template_id")
