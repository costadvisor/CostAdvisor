"""Negotiation windows + market signals (Wave 3, SCRUM-79 / MON-1).

**A window is not a point event.** "Brent moved 8%" is a fact; "you have until
the 14th to give notice on the Q4 renewal, and the driver is running against
you" is a negotiation window. A window has an open, a close, a close *basis*,
the drivers that justify it, and the products it covers — none of which a point
event can carry, and timing is the entire reason a buyer opens Monitor.

So windows get their own store with their own lifecycle. `alert_events` stays
what it already is: the fired/delivered/dedup ledger. This is deliberately not
a second delivery log alongside it.

`MarketSignal` is the supplier-announcement / disruption feed. It has no live
producer in the repo and no source in the drop, so it is modelled with an
**origin discriminator** and a manual-entry path that works on day one — an
analyst who hears about a force majeure can put it on the radar without a
deploy, and an imported editorial feed can land later without a reshape.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# What opened the window. Each maps to one radar feed.
WINDOW_DRIVERS = (
    "clause_deadline", "index_move", "gap", "buy_window", "market_signal",
)

# Why the window closes when it does. `unknown` is a first-class answer: a
# forward-looking close needs a forecast, and inventing a date to fill the
# column would be worse than admitting we do not know.
CLOSE_BASES = ("clause_deadline", "forecast_turn", "quarter_end", "signal_expiry", "unknown")

WINDOW_STATES = ("open", "closed", "dismissed")

# Tri-valued on purpose. A comparison against a missing index value returns
# falsey, so a two-state flag reports a product whose biggest cost line has
# never had a price as calm forever. "Cannot tell" is not "no move".
COVERAGE_STATES = ("covered", "partial", "unknown")

THRESHOLD_UNITS = ("pct", "currency")

SCOPE_TYPES = ("portfolio", "supplier", "contract", "cost_model", "commodity")

SIGNAL_ORIGINS = ("manual", "imported_editorial", "connector")
SIGNAL_TYPES = (
    "supplier_announcement", "disruption", "policy", "capacity", "other",
)


class NegotiationWindow(Base):
    __tablename__ = "negotiation_windows"
    __table_args__ = (
        # One window per driver+scope+period. This is what collapses a single
        # series move — one series backs roughly a quarter of the library's
        # indexed cost weight — into one window instead of a near-identical
        # event per product. `AlertEvent.dedup_key` dedups per subscription and
        # cannot collapse across products.
        UniqueConstraint("team_id", "driver_key", name="uq_window_team_driver_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)

    driver: Mapped[str] = mapped_column(String(24), nullable=False)
    driver_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_supplier_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=True)
    scope_contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=True)
    scope_cost_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_models.id", ondelete="CASCADE"), nullable=True)
    scope_commodity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id", ondelete="CASCADE"), nullable=True)

    headline: Mapped[str] = mapped_column(Text, nullable=False)

    opens_on: Mapped[date] = mapped_column(Date, nullable=False)
    closes_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    close_basis: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")

    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    coverage: Mapped[str] = mapped_column(String(12), nullable=False, default="covered")

    # The unit travels with the value. An absolute-currency threshold only
    # means anything where a base price and an actual price exist; the platform
    # index layer is index_level (base 100), where nothing is money.
    threshold_value: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    threshold_unit: Mapped[str | None] = mapped_column(String(12), nullable=True)

    # Driver values, the cost line -> type code -> series resolution path, the
    # proxy state read from the type-code side, and any unresolved codes by
    # name. Everything the inspection payload needs without re-running the radar.
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    products: Mapped[list["NegotiationWindowCostModel"]] = relationship(
        back_populates="window", cascade="all, delete-orphan",
    )


class NegotiationWindowCostModel(Base):
    """The products a window covers.

    A window groups by driver, so the products it affects are a list, not a
    column — that grouping is the whole point.
    """
    __tablename__ = "negotiation_window_cost_models"
    __table_args__ = (
        UniqueConstraint("window_id", "cost_model_id", name="uq_window_cost_model"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    window_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("negotiation_windows.id", ondelete="CASCADE"),
        nullable=False, index=True)
    cost_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_models.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # How much of this product's recipe the driver reaches, when the driver is
    # an index move. Null for drivers where the notion does not apply.
    exposure_pct: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    # Read from the type-code side of the resolution layer, never from the cost
    # line's own is_proxy — the two disagree on a meaningful share of lines, so
    # a badge sourced from both means nothing.
    via_proxy: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    window: Mapped[NegotiationWindow] = relationship(back_populates="products")


class MarketSignal(Base):
    """A supplier announcement or disruption signal.

    Platform-authored with team forks (`team_id IS NULL` = platform), the
    `tx1a2b3c4d5e` policy shape — so a platform-curated feed is visible to
    every team while a team's own analyst entries stay private.
    """
    __tablename__ = "market_signals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True)

    origin: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    supplier_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True)
    commodity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id", ondelete="SET NULL"), nullable=True)
    region: Mapped[str | None] = mapped_column(String(20), ForeignKey("regions.code"), nullable=True)

    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The editorial layer has no as_of/expires fields at all — the vantage date
    # lives only in prose. So an imported signal's date is synthesised, and
    # that has to be visible rather than passed off as authored.
    as_of_inferred: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def is_live(self, on: date) -> bool:
        return self.as_of_date <= on and (self.expires_at is None or self.expires_at >= on)
