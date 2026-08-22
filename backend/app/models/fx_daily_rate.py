from datetime import datetime, date, timezone
from sqlalchemy import Integer, Numeric, String, Date, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FxDailyRate(Base):
    """Daily FX time series (platform-level, no RLS — like FxRate/FxPair).

    One row per (pair, date) holding that day's ECB reference rate. Backfilled
    from Frankfurter's date-range endpoint and appended to daily by the
    scrape_fx_live task. Separate from the single overwritten fx_pairs.live_rate
    so the History tab can show a real daily series.
    """

    __tablename__ = "fx_daily_rate"
    __table_args__ = (
        UniqueConstraint("from_currency", "to_currency", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    to_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    rate: Mapped[float] = mapped_column(Numeric(16, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
