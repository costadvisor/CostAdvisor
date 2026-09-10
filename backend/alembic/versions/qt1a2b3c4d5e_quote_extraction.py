"""Scrum 31b — supplier quote extraction (draft + confirmed quote record)

quote_extraction_runs / quote_extraction_lines: the draft, persisted
immediately on upload (mirrors sheet_import_runs / sheet_import_row_diffs).
quote_records / quote_record_lines: the permanent destination, written only
on an explicit per-line confirm.

RLS: quote_extraction_runs and quote_records carry team_id directly (same
policy shape as cost_model_notes); quote_extraction_lines and
quote_record_lines are transitively scoped through their parent (same shape
as formula_template_components -> formula_templates).

Creation order matters: quote_extraction_lines.quote_record_line_id FKs into
quote_record_lines, so quote_record_lines (and its own parent quote_records)
must exist first, even though quote_extraction_runs is the "earlier" table
conceptually.

Revision ID: qt1a2b3c4d5e
Revises: lnk1a2b3c4d5e
Create Date: 2026-08-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "qt1a2b3c4d5e"
down_revision: Union[str, None] = "lnk1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
_MEMBER_OF = f"team_id IN (SELECT team_id FROM team_memberships WHERE user_id = {_UID})"
_RUN_VISIBLE = f"""
    run_id IN (
        SELECT id FROM quote_extraction_runs
        WHERE team_id IN (SELECT team_id FROM team_memberships WHERE user_id = {_UID})
    )
"""
_RECORD_VISIBLE = f"""
    quote_record_id IN (
        SELECT id FROM quote_records
        WHERE team_id IN (SELECT team_id FROM team_memberships WHERE user_id = {_UID})
    )
"""


def upgrade() -> None:
    # ── 1. Draft header ──────────────────────────────────────────────────────
    op.create_table(
        "quote_extraction_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="extracted"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"]),
    )
    op.create_index("ix_qer_team_id", "quote_extraction_runs", ["team_id"])

    # ── 2. Permanent destination header ─────────────────────────────────────
    op.create_table(
        "quote_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_run_id"], ["quote_extraction_runs.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_qr_team_id", "quote_records", ["team_id"])

    # ── 3. Permanent lines (the "quote record") ─────────────────────────────
    op.create_table(
        "quote_record_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quote_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_reference", sa.String(length=128), nullable=True),
        sa.Column("resolved_product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("price", sa.Numeric(14, 4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("unit", sa.String(length=10), nullable=True),
        sa.Column("volume_tier", sa.String(length=64), nullable=True),
        sa.Column("incoterm", sa.String(length=8), nullable=True),
        sa.Column("named_place", sa.String(length=128), nullable=True),
        sa.Column("quote_date", sa.Date(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("field_confidence", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["quote_record_id"], ["quote_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_product_id"], ["products.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_qrl_quote_record_id", "quote_record_lines", ["quote_record_id"])

    # ── 4. Draft lines (references quote_record_lines, so created last) ────
    op.create_table(
        "quote_extraction_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("quote_record_line_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["quote_extraction_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["quote_record_line_id"], ["quote_record_lines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
    )
    op.create_index("ix_qel_run_id", "quote_extraction_lines", ["run_id"])

    # ── 5. RLS ───────────────────────────────────────────────────────────────
    for table in ("quote_extraction_runs", "quote_records"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL
            USING ({_BYPASS} OR {_MEMBER_OF})
            WITH CHECK ({_BYPASS} OR {_MEMBER_OF})
        """)

    op.execute("ALTER TABLE quote_extraction_lines ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE quote_extraction_lines FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON quote_extraction_lines AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_RUN_VISIBLE})
        WITH CHECK ({_BYPASS} OR {_RUN_VISIBLE})
    """)

    op.execute("ALTER TABLE quote_record_lines ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE quote_record_lines FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON quote_record_lines AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_RECORD_VISIBLE})
        WITH CHECK ({_BYPASS} OR {_RECORD_VISIBLE})
    """)


def downgrade() -> None:
    for table in ("quote_record_lines", "quote_extraction_lines", "quote_records", "quote_extraction_runs"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_qel_run_id", table_name="quote_extraction_lines")
    op.drop_table("quote_extraction_lines")

    op.drop_index("ix_qrl_quote_record_id", table_name="quote_record_lines")
    op.drop_table("quote_record_lines")

    op.drop_index("ix_qr_team_id", table_name="quote_records")
    op.drop_table("quote_records")

    op.drop_index("ix_qer_team_id", table_name="quote_extraction_runs")
    op.drop_table("quote_extraction_runs")
