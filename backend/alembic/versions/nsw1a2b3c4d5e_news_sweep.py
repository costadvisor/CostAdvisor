"""Monthly news sweep: runs, items and feeds (Wave 4, SCRUM-214 / NEWS-2).

news_runs   — the run log: one row per sweep, with counts, tokens, dollars,
              errors and a per-profile breakdown.
news_items  — what was found: headline, link, date, source, our own summary,
              status. Unique per (family, normalised URL), so re-running a
              month adds nothing twice.
news_feeds  — free sources read on every run (RSS/Atom, SEC EDGAR company feeds).

Tenancy: platform-only, no team_id, no RLS (the search_profiles precedent).

Revision ID: nsw1a2b3c4d5e
Revises: nsp1a2b3c4d5e
Create Date: 2026-11-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "nsw1a2b3c4d5e"
down_revision: Union[str, None] = "nsp1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_EMPTY = sa.text("'[]'::jsonb")
_NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "news_runs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("family_id", sa.Integer, sa.ForeignKey("chemical_families.id", ondelete="SET NULL")),
        sa.Column("started_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("spend_cap_usd", sa.Float, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *[sa.Column(n, sa.Integer, nullable=False, server_default="0") for n in (
            "profiles_planned", "profiles_run", "searches", "feed_entries_read", "items_found",
            "items_new", "items_duplicate", "items_dropped", "input_tokens", "output_tokens")],
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("errors", postgresql.JSONB, nullable=False, server_default=_EMPTY),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default=_EMPTY),
        sa.CheckConstraint("status IN ('running', 'completed', 'stopped_at_cap', 'failed')",
                           name="ck_news_runs_status"),
        sa.CheckConstraint("trigger IN ('scheduled', 'manual')", name="ck_news_runs_trigger"),
    )
    op.create_index("ix_news_runs_started_at", "news_runs", ["started_at"])

    op.create_table(
        "news_items",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("family_id", sa.Integer, sa.ForeignKey("chemical_families.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("product_line_id", sa.Integer, sa.ForeignKey("product_lines.id", ondelete="SET NULL")),
        sa.Column("run_id", _UUID, sa.ForeignKey("news_runs.id", ondelete="SET NULL")),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("url_norm", sa.String(1000), nullable=False),
        sa.Column("headline", sa.String(400), nullable=False),
        sa.Column("headline_key", sa.String(400), nullable=False),
        sa.Column("source", sa.String(200)),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("published_date", sa.Date),
        sa.Column("date_source", sa.String(16)),
        sa.Column("summary", sa.Text),
        sa.Column("source_excerpt", sa.String(280)),
        sa.Column("search_relevance", sa.Float),
        sa.Column("search_event_type", sa.String(32)),
        sa.Column("status", sa.String(20), nullable=False, server_default="unsorted"),
        sa.Column("found_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.UniqueConstraint("family_id", "url_norm", name="uq_news_items_family_url"),
        sa.CheckConstraint("status IN ('unsorted', 'to_review', 'below_threshold', 'accepted', 'rejected')",
                           name="ck_news_items_status"),
        sa.CheckConstraint("origin IN ('web_search', 'feed', 'sec')", name="ck_news_items_origin"),
    )
    op.create_index("ix_news_items_family_found", "news_items", ["family_id", "found_at"])
    op.create_index("ix_news_items_status", "news_items", ["status"])

    op.create_table(
        "news_feeds",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False, unique=True),
        sa.Column("kind", sa.String(8), nullable=False, server_default="rss"),
        sa.Column("family_id", sa.Integer, sa.ForeignKey("chemical_families.id", ondelete="CASCADE")),
        sa.Column("producer_id", _UUID, sa.ForeignKey("producers.id", ondelete="CASCADE")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.CheckConstraint("kind IN ('rss', 'sec')", name="ck_news_feeds_kind"),
    )


def downgrade() -> None:
    op.drop_table("news_feeds")
    op.drop_index("ix_news_items_status", table_name="news_items")
    op.drop_index("ix_news_items_family_found", table_name="news_items")
    op.drop_table("news_items")
    op.drop_index("ix_news_runs_started_at", table_name="news_runs")
    op.drop_table("news_runs")
