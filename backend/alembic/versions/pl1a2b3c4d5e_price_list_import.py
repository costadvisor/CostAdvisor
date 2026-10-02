"""Scrum 30 — supplier price-list import (draft rows -> ActualPrice)

price_list_runs carries team_id directly (same policy shape as
quote_extraction_runs / cost_model_notes); price_list_rows is transitively
scoped through its parent run (same shape as quote_extraction_lines).

Revision ID: pl1a2b3c4d5e
Revises: txr1a2b3c4d5e
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "pl1a2b3c4d5e"
down_revision: Union[str, None] = "txr1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
_MEMBER_OF = f"team_id IN (SELECT team_id FROM team_memberships WHERE user_id = {_UID})"
_RUN_VISIBLE = f"""
    run_id IN (
        SELECT id FROM price_list_runs
        WHERE team_id IN (SELECT team_id FROM team_memberships WHERE user_id = {_UID})
    )
"""


def upgrade() -> None:
    op.create_table(
        "price_list_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("supplier_id", sa.Integer(),
                  sa.ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="extracted"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_plr_team_id", "price_list_runs", ["team_id"])

    op.create_table(
        "price_list_rows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("price_list_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("matched_cost_model_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cost_models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("match_confidence", sa.String(16), nullable=False, server_default="unmatched"),
        sa.Column("match_candidates", postgresql.JSONB(), nullable=True),
        sa.Column("period_year", sa.SmallInteger(), nullable=True),
        sa.Column("period_quarter", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        # SET NULL rather than CASCADE: deleting a price should not silently
        # erase the record that this row was the thing that created it.
        sa.Column("actual_price_id", sa.Integer(),
                  sa.ForeignKey("actual_prices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "match_confidence IN ('exact','fuzzy','ambiguous','unmatched')",
            name="ck_plrow_match_confidence",
        ),
        sa.CheckConstraint(
            "status IN ('pending','committed','skipped')", name="ck_plrow_status",
        ),
        sa.CheckConstraint(
            "period_quarter IS NULL OR period_quarter BETWEEN 1 AND 4",
            name="ck_plrow_period_quarter",
        ),
    )
    op.create_index("ix_plrow_run_id", "price_list_rows", ["run_id"])

    op.execute("ALTER TABLE price_list_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE price_list_runs FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON price_list_runs AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_MEMBER_OF})
        WITH CHECK ({_BYPASS} OR {_MEMBER_OF})
    """)

    op.execute("ALTER TABLE price_list_rows ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE price_list_rows FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON price_list_rows AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_RUN_VISIBLE})
        WITH CHECK ({_BYPASS} OR {_RUN_VISIBLE})
    """)


def downgrade() -> None:
    for table in ("price_list_rows", "price_list_runs"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_plrow_run_id", table_name="price_list_rows")
    op.drop_table("price_list_rows")
    op.drop_index("ix_plr_team_id", table_name="price_list_runs")
    op.drop_table("price_list_runs")
