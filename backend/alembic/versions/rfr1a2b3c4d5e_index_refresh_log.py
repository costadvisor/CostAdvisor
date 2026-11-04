"""Jira 209 — a record of every index refresh.

Revision ID: rfr1a2b3c4d5e
Revises: cd1a2b3c4d5e
Create Date: 2026-11-03

Until now a refresh left no trace except `scraped_at` on the values it wrote:
no last attempt, no last success, no error, nothing for a run that wrote
nothing. A failing source was invisible, and one exception in `scrape_all`
stopped every scraper after it without anyone knowing.

Three platform tables (no `team_id`, no RLS — following
`index_validation_runs`):

* `index_refresh_runs` — one row per execution of a refresh job, with what it
  counted, so a run that skipped work never reads as a clean one.
* `index_refresh_attempts` — one row per index (or team source) per run: the
  outcome (`ok` / `no_data` / `rejected` / `failed`), rows written, the latest
  period it brought, how many tries, and the error.
* `index_refresh_state` — the latest word per index (last attempt, last
  success, last error, consecutive failures), so "is this source broken" is
  one row, not a scan.

Team-source attempts carry `team_source_id` only; the reads that return error
text are super-admin.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "rfr1a2b3c4d5e"
down_revision: Union[str, None] = "cd1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "index_refresh_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("attempted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("no_data", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('running', 'ok', 'partial', 'failed')",
                           name="ck_irr_status"),
    )
    op.create_index("ix_irr_started", "index_refresh_runs", ["started_at"])

    op.create_table(
        "index_refresh_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("index_refresh_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("commodity_id", sa.Integer(),
                  sa.ForeignKey("commodity_indexes.id", ondelete="CASCADE"), nullable=True),
        sa.Column("team_source_id", sa.Integer(),
                  sa.ForeignKey("team_index_sources.id", ondelete="SET NULL"), nullable=True),
        sa.Column("label", sa.String(160), nullable=False),
        sa.Column("route", sa.String(24), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("rows_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_period", sa.String(16), nullable=True),
        sa.Column("tries", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("outcome IN ('ok', 'no_data', 'rejected', 'failed')",
                           name="ck_ira_outcome"),
    )
    op.create_index("ix_ira_run", "index_refresh_attempts", ["run_id"])
    op.create_index("ix_ira_commodity", "index_refresh_attempts", ["commodity_id", "finished_at"])

    op.create_table(
        "index_refresh_state",
        sa.Column("commodity_id", sa.Integer(),
                  sa.ForeignKey("commodity_indexes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("index_refresh_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_outcome", sa.String(16), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_period", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("index_refresh_state")
    op.drop_index("ix_ira_commodity", table_name="index_refresh_attempts")
    op.drop_index("ix_ira_run", table_name="index_refresh_attempts")
    op.drop_table("index_refresh_attempts")
    op.drop_index("ix_irr_started", table_name="index_refresh_runs")
    op.drop_table("index_refresh_runs")
