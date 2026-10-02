"""SCRUM-34 — the store for index data-quality findings.

Platform-level, no RLS, following `type_codes` / `index_cards` /
`volatility_calibrations`: these are facts about the shared index library, not
about anyone's tenancy.

The shape is explained in migration `val1a2b3c4d5e`. In short: a finding is
identified by its own fingerprint and persists across runs, so re-running
updates it rather than inserting a second copy; a run records what it counted.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Every finding is one of these. `contradiction` means two sources of truth in
# the loaded data disagree and at least one is wrong; `gap` means something the
# library needs is absent; `note` is carried-through provenance that needs no
# action on its own.
SEVERITIES = ("contradiction", "gap", "note")

# `declared` is the drop's own `_issues.csv`, carried through verbatim.
# `derived` is what a run computed. Kept apart because re-deriving a delivered
# defect list would both duplicate it and risk contradicting it.
ORIGINS = ("derived", "declared")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IndexValidationRun(Base):
    __tablename__ = "index_validation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Which checks this run actually executed, with their counts. A run that
    # skipped a check must not read as a run that found nothing wrong.
    checks: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    n_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_resolved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class IndexValidationFinding(Base):
    __tablename__ = "index_validation_findings"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_index_validation_finding"),
        CheckConstraint("origin IN ('derived', 'declared')", name="ck_ivf_origin"),
        CheckConstraint("severity IN ('contradiction', 'gap', 'note')",
                        name="ck_ivf_severity"),
        Index("ix_ivf_check_open", "check_code", "resolved_at"),
        Index("ix_ivf_subject", "subject_table", "subject_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    check_code: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)

    subject_table: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_column: Mapped[str | None] = mapped_column(String(64), nullable=True)

    left_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    left_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    right_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    right_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    first_seen_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("index_validation_runs.id", ondelete="SET NULL"),
        nullable=True)
    last_seen_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("index_validation_runs.id", ondelete="SET NULL"),
        nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
