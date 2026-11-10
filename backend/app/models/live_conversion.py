"""Typed numbers in reports to be converted into live tokens (LIVE-4 / SCRUM-211).

One row per typed number: where it is (report, section, an anchor), what was
typed, the token that replaces it, and whether a person approved it.

The reports are reloaded from the content drop, which would undo a
conversion made in place, so conversions are kept here and applied again on
every load (`services/live_conversions.py`). If the text changed so the typed
number is no longer where the row says, the row is skipped and reported,
never applied elsewhere. The same rows are exported for the content team, so
the drop itself can carry the tokens later; then these rows find nothing
left to convert.

Platform table (no team): reports are platform content. The anchor quotes a
short piece of report text, which is why this lives in the database next to
the reports, not in the repository.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

KINDS = ("cost_bar", "date", "pct_move")
STATUSES = ("proposed", "approved", "rejected")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReportLiveConversion(Base):
    __tablename__ = "report_live_conversions"
    __table_args__ = (
        UniqueConstraint("slug", "section_id", "kind", "anchor", "typed", name="uq_rlc_target"),
        CheckConstraint("kind IN ('cost_bar','date','pct_move')", name="ck_rlc_kind"),
        CheckConstraint("status IN ('proposed','approved','rejected')", name="ck_rlc_status"),
        Index("ix_rlc_slug", "slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    section_id: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # cost_bar: the bar's label; date / pct_move: the text it sits in (whitespace folded).
    anchor: Mapped[str] = mapped_column(Text, nullable=False)
    typed: Mapped[str] = mapped_column(String(64), nullable=False)    # as written
    token: Mapped[str] = mapped_column(Text, nullable=False)
    today: Mapped[str | None] = mapped_column(String(64), nullable=True)   # value when proposed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
