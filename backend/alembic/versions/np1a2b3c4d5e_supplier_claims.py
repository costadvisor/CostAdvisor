"""Scrum 29 — supplier claims for the negotiation prep flow

Strict tenant RLS, the cost_model_notes shape: a claim is a team fact about one
of that team's cost models.

Revision ID: np1a2b3c4d5e
Revises: pl1a2b3c4d5e
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "np1a2b3c4d5e"
down_revision: Union[str, None] = "pl1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
_MEMBER_OF = f"team_id IN (SELECT team_id FROM team_memberships WHERE user_id = {_UID})"


def upgrade() -> None:
    op.create_table(
        "supplier_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cost_model_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cost_models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("quarter", sa.SmallInteger(), nullable=False),
        sa.Column("said", sa.Text(), nullable=False),
        sa.Column("driver_label", sa.String(128), nullable=True),
        sa.Column("claimed_change_pct", sa.Numeric(8, 2), nullable=True),
        sa.Column("include_in_script", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("quarter BETWEEN 1 AND 4", name="ck_supplier_claims_quarter"),
    )
    op.create_index("ix_supplier_claims_cost_model", "supplier_claims",
                    ["cost_model_id", "year", "quarter"])

    op.execute("ALTER TABLE supplier_claims ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supplier_claims FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON supplier_claims AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_MEMBER_OF})
        WITH CHECK ({_BYPASS} OR {_MEMBER_OF})
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON supplier_claims")
    op.execute("ALTER TABLE supplier_claims NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supplier_claims DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_supplier_claims_cost_model", table_name="supplier_claims")
    op.drop_table("supplier_claims")
