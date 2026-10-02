"""Scrum 32 — AI cost modeler drafts

Strict tenant RLS on the draft (the cost_model_notes shape); lines are
transitive through the draft (the quote_extraction_lines shape).

Not an extension of estimator_proposals: that table is keyed (template_id,
region) with template_id NOT NULL and approves into the catalog, while this is
about a team's own product and approves into a FormulaVersion. Same rules,
different subject.

Revision ID: ai1a2b3c4d5e
Revises: lego1a2b3c4d5e
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "ai1a2b3c4d5e"
down_revision: Union[str, None] = "lego1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
_MEMBER_OF = f"team_id IN (SELECT team_id FROM team_memberships WHERE user_id = {_UID})"
_DRAFT_VISIBLE = f"""
    draft_id IN (
        SELECT id FROM ai_cost_drafts
        WHERE team_id IN (SELECT team_id FROM team_memberships WHERE user_id = {_UID})
    )
"""


def upgrade() -> None:
    op.create_table(
        "ai_cost_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("sector", sa.String(64), nullable=True),
        sa.Column("rough_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("unit", sa.String(10), nullable=True),
        sa.Column("region", sa.String(20), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ai_draft"),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_cost_model_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cost_models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('ai_draft','approved','rejected')", name="ck_aicd_status"),
    )
    op.create_index("ix_aicd_team_id", "ai_cost_drafts", ["team_id"])

    op.create_table(
        "ai_cost_draft_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ai_cost_drafts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("weight_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column("component_type", sa.String(16), nullable=False, server_default="index"),
        sa.Column("suggested_index", sa.String(128), nullable=True),
        sa.Column("commodity_id", sa.Integer(), sa.ForeignKey("commodity_indexes.id"), nullable=True),
        sa.Column("index_resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("confidence", sa.String(16), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.CheckConstraint("component_type IN ('index','fixed')", name="ck_aicdl_component_type"),
    )
    op.create_index("ix_aicdl_draft_id", "ai_cost_draft_lines", ["draft_id"])

    op.execute("ALTER TABLE ai_cost_drafts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_cost_drafts FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON ai_cost_drafts AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_MEMBER_OF})
        WITH CHECK ({_BYPASS} OR {_MEMBER_OF})
    """)
    op.execute("ALTER TABLE ai_cost_draft_lines ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_cost_draft_lines FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON ai_cost_draft_lines AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_DRAFT_VISIBLE})
        WITH CHECK ({_BYPASS} OR {_DRAFT_VISIBLE})
    """)


def downgrade() -> None:
    for table in ("ai_cost_draft_lines", "ai_cost_drafts"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_aicdl_draft_id", table_name="ai_cost_draft_lines")
    op.drop_table("ai_cost_draft_lines")
    op.drop_index("ix_aicd_team_id", table_name="ai_cost_drafts")
    op.drop_table("ai_cost_drafts")
