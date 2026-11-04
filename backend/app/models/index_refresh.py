"""Jira 209 — the record of every index refresh.

Platform-level, no RLS, following `index_validation_runs` / `type_codes`: these
are facts about the shared index library. Team-source attempts are recorded by
`team_source_id` only, and the reads that return error text are super-admin.

The shape is explained in migration `rfr1a2b3c4d5e`. In short: a run is one
execution of a refresh job, an attempt is one index (or team source) inside
it, and the state row is the latest word on each index, so "when did this last
refresh, and did it work" is one lookup instead of a scan of every attempt.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# `ok` wrote at least one value. `no_data` reached the source and got nothing.
# `rejected` got values that failed the checks, so the last good value stays.
# `failed` could not reach the source or crashed, after its retries.
OUTCOMES = ("ok", "no_data", "rejected", "failed")
RUN_STATUSES = ("running", "ok", "partial", "failed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IndexRefreshRun(Base):
    __tablename__ = "index_refresh_runs"
    __table_args__ = (
        CheckConstraint(f"status IN {RUN_STATUSES}", name="ck_irr_status"),
        Index("ix_irr_started", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    attempted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    no_data: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class IndexRefreshAttempt(Base):
    __tablename__ = "index_refresh_attempts"
    __table_args__ = (
        CheckConstraint(f"outcome IN {OUTCOMES}", name="ck_ira_outcome"),
        Index("ix_ira_run", "run_id"),
        Index("ix_ira_commodity", "commodity_id", "finished_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("index_refresh_runs.id", ondelete="CASCADE"),
        nullable=False)
    commodity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id", ondelete="CASCADE"), nullable=True)
    team_source_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("team_index_sources.id", ondelete="SET NULL"), nullable=True)
    label: Mapped[str] = mapped_column(String(160), nullable=False)   # what was refreshed
    route: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latest_period: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "2026Q3"
    tries: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=_now)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  default=_now)


class IndexRefreshState(Base):
    """The latest word on one index. Platform indexes only: a team source is a
    team's own number, not the health of the shared index."""
    __tablename__ = "index_refresh_state"

    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id", ondelete="CASCADE"), primary_key=True)
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("index_refresh_runs.id", ondelete="SET NULL"),
        nullable=True)
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latest_period: Mapped[str | None] = mapped_column(String(16), nullable=True)
