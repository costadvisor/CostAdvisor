from datetime import datetime, timezone
from sqlalchemy import (
    String, Integer, SmallInteger, Numeric, DateTime, Text,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class IndexProjectionRun(Base):
    """One vintaged forecast fit for a (commodity, region) series (Scrum 70 Part 1).

    Platform-level, like CommodityIndex/IndexValue — no team_id, no RLS. A
    re-run always inserts a new row rather than updating an existing one, so a
    forecast can later be scored against what actually happened and history
    is never silently overwritten.
    """

    __tablename__ = "index_projection_runs"
    __table_args__ = (
        Index("idx_projection_runs_lookup", "commodity_id", "region", "vintage_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id"), nullable=False
    )
    region: Mapped[str] = mapped_column(String(20), ForeignKey("regions.code"), nullable=False)
    vintage_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    # "fitted" | "hold" | "no_history"
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # "ols_linear_trend" | "hold_flat_variance" | "hold_insufficient_points" | "no_history"
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    history_from_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    history_from_quarter: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    history_to_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    history_to_quarter: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    history_points_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    horizon_quarters: Mapped[int] = mapped_column(Integer, nullable=False)
    residual_std: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    points = relationship(
        "IndexProjectionPoint",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="(IndexProjectionPoint.year, IndexProjectionPoint.quarter)",
    )


class IndexProjectionPoint(Base):
    """A single future (year, quarter) point of an IndexProjectionRun.

    ci_lo/ci_hi are nullable — a "hold" or "no_history" run has no residual
    variance to build an interval from, so it stays explicitly absent rather
    than a fabricated zero-width band.
    """

    __tablename__ = "index_projection_points"
    __table_args__ = (
        UniqueConstraint("run_id", "year", "quarter"),
        Index("idx_projection_points_lookup", "run_id", "year", "quarter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("index_projection_runs.id", ondelete="CASCADE"), nullable=False
    )
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    ci_lo: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    ci_hi: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)

    run = relationship("IndexProjectionRun", back_populates="points")
