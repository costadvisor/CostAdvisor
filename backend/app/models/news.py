"""Monthly news: runs, items and feeds (Wave 4, SCRUM-214 / NEWS-2).

* ``NewsRun``  — one sweep: what it ran, what it found, what it cost, what
  went wrong. The run log the ticket asks for, one row per run.
* ``NewsItem`` — one piece of news found for a family. We keep only the
  headline, the link, the date, the source and our own short summary — never
  the article (copyright, and we do not need it). Unique per family by
  normalised URL, so re-running a month adds nothing twice.
* ``NewsFeed`` — a free source read on every run: an RSS/Atom feed (trade
  press, a company's press page) or an SEC EDGAR company feed (8-Ks of a
  listed producer).

Platform-only, no ``team_id``, no RLS — the ``search_profiles`` precedent: the
sweep is a system task and the news feeds platform editorial content.

``NewsItem.status`` starts ``unsorted``; sorting and tagging (SCRUM-215) and
the editor queue (SCRUM-216) move it on.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_STOPPED_AT_CAP = "stopped_at_cap"
RUN_FAILED = "failed"
RUN_STATUSES = (RUN_RUNNING, RUN_COMPLETED, RUN_STOPPED_AT_CAP, RUN_FAILED)

ITEM_UNSORTED = "unsorted"
ITEM_STATUSES = ("unsorted", "to_review", "below_threshold", "accepted", "rejected")

ORIGINS = ("web_search", "feed", "sec")
FEED_KINDS = ("rss", "sec")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


class NewsRun(Base):
    __tablename__ = "news_runs"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(RUN_STATUSES)})", name="ck_news_runs_status"),
        CheckConstraint("trigger IN ('scheduled', 'manual')", name="ck_news_runs_trigger"),
        Index("ix_news_runs_started_at", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    period: Mapped[str] = mapped_column(String(7), nullable=False)          # "2026-11"
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    # A manual run can be for one family; a scheduled run is for all of them.
    family_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chemical_families.id", ondelete="SET NULL"), nullable=True)
    started_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RUN_RUNNING)
    spend_cap_usd: Mapped[float] = mapped_column(Float, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    profiles_planned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    profiles_run: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    searches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feed_entries_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_dropped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # [{"label": ..., "message": ...}] — anything that went wrong, in order.
    errors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # One entry per profile run: searches, found, new, duplicate, dropped-by-reason, cost.
    details: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    family = relationship("ChemicalFamily")
    starter = relationship("User")


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = (
        UniqueConstraint("family_id", "url_norm", name="uq_news_items_family_url"),
        CheckConstraint(f"status IN ({_in(ITEM_STATUSES)})", name="ck_news_items_status"),
        CheckConstraint(f"origin IN ({_in(ORIGINS)})", name="ck_news_items_origin"),
        Index("ix_news_items_family_found", "family_id", "found_at"),
        Index("ix_news_items_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    family_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chemical_families.id", ondelete="CASCADE"), nullable=False)
    # Set when the item came from a product line's override profile.
    product_line_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("product_lines.id", ondelete="SET NULL"), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("news_runs.id", ondelete="SET NULL"), nullable=True)

    url: Mapped[str] = mapped_column(Text, nullable=False)
    url_norm: Mapped[str] = mapped_column(String(1000), nullable=False)
    headline: Mapped[str] = mapped_column(String(400), nullable=False)
    headline_key: Mapped[str] = mapped_column(String(400), nullable=False)   # for near-duplicates
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    published_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Our own words (from the search). Never the publisher's text.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Feed items only: the feed's own teaser, at most 280 characters, kept so
    # SCRUM-215 has something to sort on. Never shown or published as ours.
    source_excerpt: Mapped[str | None] = mapped_column(String(280), nullable=True)
    # What the search itself thought; SCRUM-215 makes the real call.
    search_relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    search_event_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ITEM_UNSORTED)
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    family = relationship("ChemicalFamily")
    product_line = relationship("ProductLine")


class NewsFeed(Base):
    __tablename__ = "news_feeds"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(FEED_KINDS)})", name="ck_news_feeds_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False, default="rss")
    # NULL = read for every family (entries still have to match a profile).
    family_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chemical_families.id", ondelete="CASCADE"), nullable=True)
    # SEC feeds: the producer whose filings these are. Its filings are news for
    # every family whose profile watches that producer, no keyword match needed.
    producer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("producers.id", ondelete="CASCADE"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    family = relationship("ChemicalFamily")
    producer = relationship("Producer")
