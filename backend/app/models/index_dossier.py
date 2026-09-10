"""Index dossier storage + platform volatility calibration (Wave 3, DB-7).

The structured half of an index dossier: upstream drivers with their
correlation, lag and signal; the price-transmission chain; roles and
methodology flags; the index-specific role a producer or price-setter plays;
supply/demand splits; negotiation pointers.

**Three boundaries held, each with a measured reason.**

1. **Computed snapshots are not dossier content.** `index_feeds.csv` ships
   `current_value`, `change_pct`, `volatility_pct`, `cycle_pct`, `card_status`
   and `has_intel_block`; all are moments in time, recomputable from the
   series, and none is stored here. `volatility_pct` in particular is editorial
   and **self-contradictory**: three series carry two different values across
   their own cards (`elec-cn` 12 and 55, `elec-eu` 55 and 65, `corn` 45 and
   48 — same series, same numbers underneath). Importing it would enshrine a
   number the data itself disagrees with. Cycle position is the same: the
   *value* is derived (SCRUM-75 computes it), so nothing cycle-shaped is stored.
   `INDEX_SEASONALITY` / `INDEX_SEASON_NOTES` are retired as import candidates
   for the same reason — they reproduce from the series, and SCRUM-69 generates
   `index_seasonal_factor` rather than importing it.

2. **Prose is not here.** Narratives (`dyn3m`/`dyn24m`), signal lists, chain
   notes, season notes, producer notes and proxy notes key to the index slug and
   are `subject_type='index'` editorial blocks (unit 7). This module stores
   structured fields only. The one judgement call: **negotiation pointers are
   stored**, because the ticket names them as dossier content and they are
   per-index structured rows with a title, not a free narrative — the body
   rides along with its pointer rather than standing alone.

3. **Company records are not here.** Unit 8 owns the `Producer` entity and its
   alias layer, so `IndexProducerRole` carries the index-specific role and
   **FKs to that producer** — one master, not two.

Grain is per series, with an optional region: 16 of the 54 source entries carry
`_regional` overrides, and 20 fields differ by region inside them. `region IS
NULL` is the series-wide row and a region-specific row overrides it, the same
semantic as `FormulaTemplateComponent.region`.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, SmallInteger,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# What a driver's `signal` says. The source vocabulary is **not** bounded — 20
# distinct values across 66 driver rows, including "dominant structural",
# "dominant 2026", "medium geopolitical" — so the raw string is preserved and a
# normalised strength is derived from its leading token. A CHECK-constrained
# enum here would reject the real data.
SIGNAL_STRENGTHS = ("dominant", "strong", "medium", "moderate", "weak", "macro", "other")

CHAIN_NODE_TYPES = ("node", "transform")
SPLIT_TYPES = ("supply", "demand")
FLAG_KINDS = ("role", "sustainability")
FLAG_SEVERITIES = ("ok", "warn", "info")
PRODUCER_ROLES = ("producer", "price_setter")


def normalize_signal(raw: str | None) -> str:
    """The comparable strength behind a free-text signal label."""
    text = (raw or "").strip().lower()
    for level in ("dominant", "strong", "medium", "moderate", "weak", "macro"):
        if text.startswith(level):
            return level
    return "other"


class IndexDossier(Base):
    """The dossier header for one series, optionally for one region."""
    __tablename__ = "index_dossiers"
    __table_args__ = (
        UniqueConstraint("commodity_id", "region", name="uq_index_dossier_series_region"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # NULL = the series-wide dossier; a set region overrides it for that card.
    region: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # The methodology half of `roleExtra` — how the number is quoted and what
    # part it plays in a formula. Not recomputable, so it lives here.
    quote_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    formula_role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    access_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The dossier's own headline correlation ("r=0.82 vs. Benzene NWE (6w lag)"):
    # the parsed coefficient plus the string it came from, because the prose
    # names the counterpart and the lag and the number alone loses both.
    anchor_correlation: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    anchor_correlation_raw: Mapped[str | None] = mapped_column(String(200), nullable=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False, default="loader")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    drivers: Mapped[list["IndexDriver"]] = relationship(
        back_populates="dossier", cascade="all, delete-orphan",
        order_by="IndexDriver.sort_order")
    chain: Mapped[list["IndexChainNode"]] = relationship(
        back_populates="dossier", cascade="all, delete-orphan",
        order_by="IndexChainNode.position")
    flags: Mapped[list["IndexRoleFlag"]] = relationship(
        back_populates="dossier", cascade="all, delete-orphan",
        order_by="IndexRoleFlag.sort_order")
    splits: Mapped[list["IndexSplit"]] = relationship(
        back_populates="dossier", cascade="all, delete-orphan",
        order_by="IndexSplit.sort_order")
    producer_roles: Mapped[list["IndexProducerRole"]] = relationship(
        back_populates="dossier", cascade="all, delete-orphan",
        order_by="IndexProducerRole.sort_order")
    pointers: Mapped[list["IndexNegotiationPointer"]] = relationship(
        back_populates="dossier", cascade="all, delete-orphan",
        order_by="IndexNegotiationPointer.sort_order")


class IndexDriver(Base):
    """One upstream driver — **correlation, lag and signal on one row**, which
    is the point: a correlation without its lag cannot be acted on, and a lag
    without a direction cannot be read."""
    __tablename__ = "index_drivers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dossier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("index_dossiers.id", ondelete="CASCADE"),
        nullable=False, index=True)

    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    correlation: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)

    # 38 distinct free-text lag strings ("4–6 weeks", "1–2 quarters",
    # "Immediate (co-product)"). The raw string is authoritative; the parsed
    # bounds exist so a caller can sort and threshold on lag, and stay NULL
    # whenever the string does not parse rather than being guessed at.
    lag_raw: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lag_days_min: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    lag_days_max: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # 120 not 80: the unbounded vocabulary runs to 65 characters today
    # ("dominant geopolitical", "strong event-driven") and is prose-shaped.
    signal_raw: Mapped[str | None] = mapped_column(String(120), nullable=True)
    signal_strength: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The driver's own recent move, as the source stated it. Kept as text
    # because it is a claim the dossier makes, not a value we recompute — and
    # sized for prose rather than a percentage: the source puts sentences here
    # ("War-driven gas cost spike feeding into ammonia -> urea", 71 chars at the
    # longest), which is what a String(40) rejected on the first real load.
    move_raw: Mapped[str | None] = mapped_column(String(200), nullable=True)
    move_up: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    dossier: Mapped[IndexDossier] = relationship(back_populates="drivers")


class IndexChainNode(Base):
    """One step of the price-transmission chain, in order.

    The source interleaves nodes (`l`/`s`) with transform arrows (`a`), so
    `node_type` keeps the sequence readable without the presentation classes.
    """
    __tablename__ = "index_chain_nodes"
    __table_args__ = (
        CheckConstraint("node_type IN ('node','transform')", name="ck_chain_node_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dossier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("index_dossiers.id", ondelete="CASCADE"),
        nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    node_type: Mapped[str] = mapped_column(String(16), nullable=False, default="node")
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(200), nullable=True)

    dossier: Mapped[IndexDossier] = relationship(back_populates="chain")


class IndexRoleFlag(Base):
    """A role the index plays, or a sustainability / methodology flag on it."""
    __tablename__ = "index_role_flags"
    __table_args__ = (
        CheckConstraint("flag_kind IN ('role','sustainability')", name="ck_flag_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dossier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("index_dossiers.id", ondelete="CASCADE"),
        nullable=False, index=True)
    flag_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # ok / warn / info on a sustainability flag; NULL on a role.
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    dossier: Mapped[IndexDossier] = relationship(back_populates="flags")


class IndexSplit(Base):
    """A supply or demand split slice. The source's colour is presentation and
    is not stored."""
    __tablename__ = "index_splits"
    __table_args__ = (
        CheckConstraint("split_type IN ('supply','demand')", name="ck_split_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dossier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("index_dossiers.id", ondelete="CASCADE"),
        nullable=False, index=True)
    split_type: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    pct: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    dossier: Mapped[IndexDossier] = relationship(back_populates="splits")


class IndexProducerRole(Base):
    """The index-specific role a company plays — **by FK to unit 8's producer
    master**, so there is one company record rather than two.

    `share = 0` is again *not disclosed*, and the ratio is inverted from the
    supplier data: 147 of 189 rows on index dossiers state a real share, 42 do
    not. The flag is stored either way.
    """
    __tablename__ = "index_producer_roles"
    __table_args__ = (
        UniqueConstraint("dossier_id", "producer_id", "role",
                         name="uq_index_producer_role"),
        CheckConstraint("role IN ('producer','price_setter')", name="ck_index_producer_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dossier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("index_dossiers.id", ondelete="CASCADE"),
        nullable=False, index=True)
    producer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("producers.id", ondelete="CASCADE"),
        nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="producer")

    share_pct: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    share_disclosed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    location: Mapped[str | None] = mapped_column(String(160), nullable=True)
    regions_raw: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # What the dossier called them, before alias resolution.
    raw_name: Mapped[str | None] = mapped_column(String(400), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    dossier: Mapped[IndexDossier] = relationship(back_populates="producer_roles")
    producer = relationship("Producer", lazy="joined")


class IndexNegotiationPointer(Base):
    """A pointer a buyer can act on. Stored because the ticket names it as
    dossier content and it is a titled structured row, not a narrative."""
    __tablename__ = "index_negotiation_pointers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dossier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("index_dossiers.id", ondelete="CASCADE"),
        nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    dossier: Mapped[IndexDossier] = relationship(back_populates="pointers")


# ── The platform volatility calibration ──────────────────────────────────────

class VolatilityCalibration(Base):
    """A dated ladder placing a series' dispersion on a 0–100 percentile.

    **Regenerated, never imported.** The shipped
    `VOLATILITY_PERCENTILE_BREAKPOINTS.json` does not fit this data: measured
    against the real 91-series dispersion distribution its rungs deviate by up
    to **13.7**, and its top rung (21.57) sits *below* the real maximum
    (35.28), so the most volatile series in the library would be pinned at 100
    by a ladder that never saw it.

    Stored as a **vintage** rather than overwritten — the same reasoning as
    `IndexProjectionRun`: a percentile that moved needs the old ladder to
    explain why. `is_active` marks the one readers use.

    SCRUM-75 reads this ladder and reports which calibration it read; it does
    not recompute.
    """
    __tablename__ = "volatility_calibrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # How dispersion was measured, so two calibrations are comparable.
    method: Mapped[str] = mapped_column(String(40), nullable=False, default="mom_pct_stdev")
    n_rungs: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # How many series the ladder was fitted over — the number that makes a
    # recompute meaningful or not.
    n_series: Mapped[int] = mapped_column(Integer, nullable=False)
    min_points: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    breakpoints: Mapped[list["VolatilityBreakpoint"]] = relationship(
        back_populates="calibration", cascade="all, delete-orphan",
        order_by="VolatilityBreakpoint.rung")

    @property
    def step(self) -> float:
        """Percentile points per rung.

        **Derived from the ladder's own length**, never hardcoded: the shipped
        ladder has 21 rungs so `100/(21-1)` is exactly 5, which is why the
        mockup's hardcoded x5 is accidentally correct today and would break
        silently the moment the ladder is recalibrated to a different size.
        """
        return 100 / (self.n_rungs - 1) if self.n_rungs > 1 else 100.0


class VolatilityBreakpoint(Base):
    __tablename__ = "volatility_breakpoints"
    __table_args__ = (
        UniqueConstraint("calibration_id", "rung", name="uq_volatility_breakpoint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    calibration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("volatility_calibrations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    rung: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    dispersion: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)

    calibration: Mapped[VolatilityCalibration] = relationship(back_populates="breakpoints")
