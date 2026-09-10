"""Scrum 27b — sheet_import_runs / sheet_import_row_diffs

Storage for the sheet round-trip mechanism: export a filtered slice of a
registered payload (e.g. FormulaRegionCoverage base prices), edit it
offline, reimport it. Importing always inserts a NEW run + its diff rows and
never mutates the underlying payload table — applying is a separate,
explicit call, so a run is a durable record of "what would change"
independent of whether it's ever applied.

Platform-level like index_projection_runs/commodity_indexes (no team_id) —
no RLS policy needed, same reasoning: this backs platform catalog data, not
tenant data.

Revision ID: srt1a2b3c4d5e
Revises: tpc1a2b3c4d5e
Create Date: 2026-08-22

"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "srt1a2b3c4d5e"
down_revision: Union[str, None] = "tpc1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sheet_import_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload_key", sa.String(length=64), nullable=False),
        sa.Column("filter_spec", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("imported_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["imported_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["applied_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_sheet_import_runs_payload",
        "sheet_import_runs",
        ["payload_key", "created_at"],
    )

    op.create_table(
        "sheet_import_row_diffs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_key", postgresql.JSONB(), nullable=False),
        sa.Column("column", sa.String(length=64), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["sheet_import_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_sheet_import_row_diffs_run",
        "sheet_import_row_diffs",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_sheet_import_row_diffs_run", table_name="sheet_import_row_diffs")
    op.drop_table("sheet_import_row_diffs")
    op.drop_index("idx_sheet_import_runs_payload", table_name="sheet_import_runs")
    op.drop_table("sheet_import_runs")
