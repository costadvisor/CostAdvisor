"""SCRUM-34 — index data-quality validation over the resolution layer.

Revision ID: val1a2b3c4d5e
Revises: sup1a2b3c4d5e
Create Date: 2026-09-12

Two platform tables (no `team_id`, no RLS — following `type_codes`,
`index_cards` and `volatility_calibrations`, which are all platform reference
data): a run header and the findings it produced.

**Findings are keyed by fingerprint, not by run.** The ticket asks for a run
that is "inspectable after the fact and re-runnable without duplicating
findings", which pulls in two directions: a findings-per-run table is
inspectable but duplicates every standing finding on every run, while a
snapshot table that is cleared and rebuilt (the `dimension_unresolved` shape)
never duplicates but loses the history. So a finding is a row that *persists*
and carries `first_seen_run_id` / `last_seen_run_id`, and a run records what it
counted. Re-running updates `last_seen_run_id` and inserts only what is new;
anything the latest run did not re-observe is stamped `resolved_at` rather than
deleted, which is what makes "did my fix land?" answerable at all.

`origin` is the ticket's own requirement that carried-through findings stay
distinguishable from derived ones: `declared` rows come from the drop's
`_issues.csv` (already loaded as `drop_issues`) and are never recomputed,
`derived` rows are what this run actually checked.

The two conflicting values are two columns rather than one free-text message,
because "naming the two conflicting values" is the whole point of a structural
contradiction — a message would have to be parsed to be useful.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "val1a2b3c4d5e"
down_revision: Union[str, None] = "sup1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "index_validation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # Which checks ran, so a partial run is never mistaken for a clean one.
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("n_findings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_resolved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
    )

    op.create_table(
        "index_validation_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Stable identity of the finding itself — what makes a re-run an update
        # rather than a duplicate.
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("check_code", sa.String(48), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        # The ticket's "naming the table, key, column".
        sa.Column("subject_table", sa.String(64), nullable=False),
        sa.Column("subject_key", sa.String(255), nullable=False),
        sa.Column("subject_column", sa.String(64), nullable=True),
        # "...and the two conflicting values." Labelled, because which side is
        # which is the actionable part: a registry saying proxy and a line
        # saying direct needs a different fix from the reverse.
        sa.Column("left_label", sa.String(64), nullable=True),
        sa.Column("left_value", sa.Text(), nullable=True),
        sa.Column("right_label", sa.String(64), nullable=True),
        sa.Column("right_value", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("first_seen_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("index_validation_runs.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("last_seen_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("index_validation_runs.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        # Stamped, never deleted: a finding that stopped appearing is evidence
        # a fix landed, which is worth more than a tidy table.
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("fingerprint", name="uq_index_validation_finding"),
        sa.CheckConstraint("origin IN ('derived', 'declared')",
                           name="ck_ivf_origin"),
        sa.CheckConstraint("severity IN ('contradiction', 'gap', 'note')",
                           name="ck_ivf_severity"),
    )
    op.create_index("ix_ivf_check_open", "index_validation_findings",
                    ["check_code", "resolved_at"])
    op.create_index("ix_ivf_subject", "index_validation_findings",
                    ["subject_table", "subject_key"])


def downgrade() -> None:
    op.drop_index("ix_ivf_subject", table_name="index_validation_findings")
    op.drop_index("ix_ivf_check_open", table_name="index_validation_findings")
    op.drop_table("index_validation_findings")
    op.drop_table("index_validation_runs")
