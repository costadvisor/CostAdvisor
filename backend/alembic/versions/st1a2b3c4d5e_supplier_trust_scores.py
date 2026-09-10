"""Scrum 32 — supplier trust & margin grading

supplier_trust_scores: a team's own read on a supplier (team-scoped, direct
membership RLS — same shape as cost_model_notes). One row per (supplier,
grain, grain_key); grain is "product" or "subfamily" per the ticket's
per-(supplier, product-or-subfamily) grain requirement.

Revision ID: st1a2b3c4d5e
Revises: qt1a2b3c4d5e
Create Date: 2026-08-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "st1a2b3c4d5e"
down_revision: Union[str, None] = "qt1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
_MEMBER_OF = f"team_id IN (SELECT team_id FROM team_memberships WHERE user_id = {_UID})"


def upgrade() -> None:
    op.create_table(
        "supplier_trust_scores",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("grain", sa.String(length=16), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subfamily_id", sa.Integer(), nullable=True),
        sa.Column("grain_key", sa.String(length=64), nullable=False),
        sa.Column("insufficient_data", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("grade", sa.String(length=2), nullable=True),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subfamily_id"], ["subfamilies.id"], ondelete="SET NULL"),
        sa.CheckConstraint("grain IN ('product', 'subfamily')", name="ck_sts_grain"),
        sa.UniqueConstraint("supplier_id", "grain", "grain_key", name="uq_sts_supplier_grain_key"),
    )
    op.create_index("ix_sts_team_id", "supplier_trust_scores", ["team_id"])
    op.create_index("ix_sts_supplier_id", "supplier_trust_scores", ["supplier_id"])

    op.execute("ALTER TABLE supplier_trust_scores ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supplier_trust_scores FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON supplier_trust_scores AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_MEMBER_OF})
        WITH CHECK ({_BYPASS} OR {_MEMBER_OF})
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON supplier_trust_scores")
    op.execute("ALTER TABLE supplier_trust_scores NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supplier_trust_scores DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_sts_supplier_id", table_name="supplier_trust_scores")
    op.drop_index("ix_sts_team_id", table_name="supplier_trust_scores")
    op.drop_table("supplier_trust_scores")
