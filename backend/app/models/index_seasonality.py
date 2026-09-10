"""Seasonal factors — generated, never imported (Wave 3, SCRUM-69).

Seasonality is a derived table, not editorial content. The drop's
`INDEX_SEASONALITY.json` reproduces from the stored series and its
`INDEX_SEASON_NOTES.json` is a small set of templates rendering those same
numbers, so importing either freezes them at the values they had on the day of
the drop, lets them disagree with the series within a quarter or two, and leaves
nothing able to say which of the two the app should believe.

**The method was reverse-engineered and confirmed rather than guessed.** The
drop's own notes name it — "(ratio-to-moving-average method)" — and fitting
ratio-to-centred-12-month-moving-average over the stored monthly actuals
reproduces the published factors **exactly (within 0.05) on 46 of the 48 series
that have actual history**.

Which exposes the reason this has to be generated: **30 of the 78 series with
published seasonality have no monthly actuals at all** — six forecast points
each — and every one of their notes still asserts "computed directly from 42
months of real index history". The prose claims a history the series does not
have. A generated table simply has no factors for those series, which is the
honest answer.

The columns are the ticket's own list: `(commodity_id, region, month, factor,
method, window_months, computed_at)`, with the method recorded on the row so two
differently-computed sets can never be silently compared.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

# Recorded on every row. A second method would produce a second, incomparable
# set of numbers, which is exactly what the ticket's "nobody else generates a
# second set" is guarding against.
METHOD_RATIO_TO_CENTRED_MA12 = "ratio_to_centred_ma12"


class IndexSeasonalFactor(Base):
    __tablename__ = "index_seasonal_factors"
    __table_args__ = (
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_seasonal_month"),
        CheckConstraint("factor > 0", name="ck_seasonal_factor_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # NULL = the whole series. The drop's seasonality is per series (region is
    # already baked into the series key), so nothing populates this today — the
    # column exists because SCRUM-75 blends factors per formula x region combo
    # and would otherwise have nowhere to store a region-specific override.
    region: Mapped[str | None] = mapped_column(String(20), nullable=True)

    month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # 100 = an average month. The twelve factors of a series average to 100 by
    # construction.
    factor: Mapped[float] = mapped_column(Numeric(7, 3), nullable=False)

    method: Mapped[str] = mapped_column(String(48), nullable=False)
    # How many monthly observations the fit actually used — the number the
    # drop's prose asserts as 42 for every series, including the 30 that have
    # none.
    window_months: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
