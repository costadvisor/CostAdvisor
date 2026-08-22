"""Formula template forking: origin_id back-link

Revision ID: ff1a2b3c4d5e
Revises: co1a2b3c4d5e
Create Date: 2026-07-25

"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ff1a2b3c4d5e"
down_revision: Union[str, None] = "co1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "formula_templates",
        sa.Column("origin_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_formula_templates_origin", "formula_templates", "formula_templates",
        ["origin_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_formula_templates_origin", "formula_templates", type_="foreignkey")
    op.drop_column("formula_templates", "origin_id")
