"""Scrum 33 — cost-structure estimator (provenance + draft staging tables)

formula_region_coverage.provenance: "imported" (default, backfilled onto
every existing row) / "ai_draft" / "human_approved" — orthogonal to
data_confidence, extends the existing review state machine rather than
forking it.

estimator_proposals / estimator_proposal_lines: the draft staging area.
Never mutates FormulaTemplateComponent/FormulaRegionCoverage until a human
approves (services/formula_estimator.py). RLS: transitive through the
parent template — same shape as formula_template_components/
formula_region_coverage (wc1a2b3c4d5e).

Revision ID: es1a2b3c4d5e
Revises: st1a2b3c4d5e
Create Date: 2026-08-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "es1a2b3c4d5e"
down_revision: Union[str, None] = "st1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
_TEMPLATE_VISIBLE = f"""
    template_id IN (
        SELECT ft.id FROM formula_templates ft
        WHERE ft.team_id IS NULL OR ft.team_id IN (
            SELECT team_id FROM team_memberships WHERE user_id = {_UID}
        )
    )
"""
_PROPOSAL_TEMPLATE_VISIBLE = f"""
    proposal_id IN (
        SELECT ep.id FROM estimator_proposals ep
        JOIN formula_templates ft ON ft.id = ep.template_id
        WHERE ft.team_id IS NULL OR ft.team_id IN (
            SELECT team_id FROM team_memberships WHERE user_id = {_UID}
        )
    )
"""


def upgrade() -> None:
    op.add_column(
        "formula_region_coverage",
        sa.Column("provenance", sa.String(length=16), nullable=False, server_default="imported"),
    )

    op.create_table(
        "estimator_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("region", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ai_draft"),
        sa.Column("evidence_summary", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["template_id"], ["formula_templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["region"], ["regions.code"]),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.UniqueConstraint("template_id", "region", name="uq_ep_template_region"),
    )
    op.create_index("ix_ep_template_id", "estimator_proposals", ["template_id"])

    op.create_table(
        "estimator_proposal_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("component_type", sa.String(length=16), nullable=False),
        sa.Column("commodity_id", sa.Integer(), nullable=True),
        sa.Column("weight_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column("is_proxy", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("series_available", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("candidate_reason", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["proposal_id"], ["estimator_proposals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["commodity_id"], ["commodity_indexes.id"]),
        sa.CheckConstraint("component_type IN ('index', 'fixed')", name="ck_epl_component_type"),
    )
    op.create_index("ix_epl_proposal_id", "estimator_proposal_lines", ["proposal_id"])

    for table, policy in (
        ("estimator_proposals", _TEMPLATE_VISIBLE),
        ("estimator_proposal_lines", _PROPOSAL_TEMPLATE_VISIBLE),
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL
            USING ({_BYPASS} OR {policy})
            WITH CHECK ({_BYPASS} OR {policy})
        """)


def downgrade() -> None:
    for table in ("estimator_proposal_lines", "estimator_proposals"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_epl_proposal_id", table_name="estimator_proposal_lines")
    op.drop_table("estimator_proposal_lines")

    op.drop_index("ix_ep_template_id", table_name="estimator_proposals")
    op.drop_table("estimator_proposals")

    op.drop_column("formula_region_coverage", "provenance")
