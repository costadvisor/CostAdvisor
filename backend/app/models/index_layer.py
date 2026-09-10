"""The three-layer index model (Wave 3, SCRUM-74 / DB-5 + DB-6).

The July drop keys prices differently from the shipped code: the region is
baked into the series key (`lab-eu`, `lab-in`) rather than sitting in a
separate column, and a cost line names a **type code** which resolves to a
series — many codes to one series. 60 codes resolve to Brent alone, carrying
about a quarter of all indexed cost weight, and nothing in the app could see
that because there was no layer that groups codes by what they resolve to.

    TypeCode          what a cost line names        (191 rows)
        │ resolves_to
    CommodityIndex    the price series              (121 rows)
        │
        ├── IndexCard          how it is displayed   (132 rows; several cards
        │                      can share one series)
        └── IndexMonthlyValue  the numbers, monthly

**These are additive.** `DROP_2026-07_ANALYSIS.md` §1 is explicit that the
costing engine stays as it is — so `CommodityIndex` keeps its existing role
and simply gains the series-layer fields, `IndexValue` (quarterly) is
untouched, and `FormulaTemplateComponent.commodity_id` still works exactly
as before. The new `type_code_id` beside it is what makes the resolution
chain queryable; nothing is repointed in this migration.

**Platform-level, no RLS** — `commodity_indexes` has no `team_id` and no
policy, and these layers describe the same shared reference data, so they
follow it. Team-specific values continue to live in `IndexOverride` /
`TeamIndexSource`, which key on `(team_id, commodity_id, region)`.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric,
    SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# `resolution` is three-valued and the third state is the one that gets
# mis-filed: `no_series` still names a real series — it means that series has
# no numbers yet — while `ambiguous` is the only state with nothing to point
# at. Binary handling files ambiguous under no_series and loses the
# distinction between "we know what this is and can't price it" and "we don't
# know what this is".
RESOLUTION_STATES = ("resolved", "no_series", "ambiguous")

# The registry's own reading. `combo_lines` carries a second, disagreeing
# reading of the same fact — see services/drop/authority.py for why both are
# kept rather than adjudicated here.
PROXY_STATUSES = ("direct", "proxy", "unclassified")

# A sourcing backlog rank, NOT an accuracy ladder:
#   A — a better index exists and is named in ideal_index; buying it improves
#       the number overnight.
#   B — an upstream feedstock or energy stand-in; defensible by design.
#   C — permanent by design (electricity tracks electricity). Already correct.
# Blank on 82 of 191 codes, so nullable.
SWAP_PRIORITIES = ("A", "B", "C")

VALUE_KINDS = ("actual", "forecast")


class TypeCode(Base):
    """What a cost line names, and what it resolves to.

    The join that makes concentration visible. Many codes share one series,
    so this is where "this combo's diversified-looking breakdown is really
    one commodity wearing several labels" becomes answerable.
    """

    __tablename__ = "type_codes"
    __table_args__ = (
        CheckConstraint(
            "resolution IN ('resolved', 'no_series', 'ambiguous')",
            name="ck_type_code_resolution",
        ),
        # Only `ambiguous` may lack a target. `no_series` codes all name a
        # real series — verified across the whole drop — so allowing a NULL
        # there would let a genuine load failure pass as a known state.
        CheckConstraint(
            "resolves_to_id IS NOT NULL OR resolution = 'ambiguous'",
            name="ck_type_code_target_required",
        ),
        Index("ix_type_codes_resolves_to", "resolves_to_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))

    resolves_to_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id"), nullable=True
    )
    resolution: Mapped[str] = mapped_column(String(16), nullable=False)
    proxy_status: Mapped[str | None] = mapped_column(String(16))
    swap_priority: Mapped[str | None] = mapped_column(String(1))

    # Free prose naming a series we do not have ("2 EH  EU regional price").
    # None of its values correspond to any commodity_key, so it cannot be an
    # FK today — it becomes one the day that series is sourced, and the set of
    # non-null values IS the sourcing backlog.
    ideal_index: Mapped[str | None] = mapped_column(Text)
    registry_note: Mapped[str | None] = mapped_column(Text)

    # The drop's own usage snapshot, carried for reference. The live answer
    # derives from FormulaTemplateComponent once type_code_id is populated —
    # these are what the source measured, not what our catalog currently says.
    source_n_formulas: Mapped[int | None] = mapped_column(Integer)
    source_n_lines: Mapped[int | None] = mapped_column(Integer)
    source_total_weight: Mapped[float | None] = mapped_column(Numeric(14, 4))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    resolves_to = relationship("CommodityIndex")

    @property
    def is_priceable(self) -> bool:
        """A code can only be costed when it resolves AND its series carries
        numbers. `resolution == 'resolved'` alone is not enough."""
        return self.resolution == "resolved"


class IndexCard(Base):
    """How a series is presented — the display grouping.

    A card is not a series: 132 cards sit over 121 series, and Brent alone
    backs 4. Keying the app by series would silently lose 11 cards.

    **Region lives here, not on the series.** The series key often carries a
    trailing token (`-na`, `-eu`) but it is not reliably a region — `-ppi`,
    `-wb` and `-mb` are data sources, and 23 of the 28 `multi`-region cards
    sit on a series whose key names a specific region. Parsing the key would
    assign the wrong region to those.
    """

    __tablename__ = "index_cards"
    __table_args__ = (
        Index("ix_index_cards_commodity", "commodity_id"),
        Index("ix_index_cards_slug", "feed_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # `slug|region` for most cards, a bare slug for the stub ones — two
    # formats in one identity column, so it is stored verbatim rather than
    # parsed into parts.
    feed_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    feed_slug: Mapped[str] = mapped_column(String(64), nullable=False)

    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id"), nullable=False
    )

    # Deliberately NOT an FK to regions.code. The drop's vocabulary is its
    # own (`EU`, `NA`, `multi`, `Global`, blank) and does not map onto our
    # region table; the mapping is a decision-form dependency, not something
    # to guess at load time. `multi` and `Global` are not regions at all.
    region: Mapped[str | None] = mapped_column(String(20))
    region_label: Mapped[str | None] = mapped_column(String(128))

    name: Mapped[str | None] = mapped_column(String(128))
    unit: Mapped[str | None] = mapped_column(String(32))
    incoterm: Mapped[str | None] = mapped_column(String(8))
    named_place: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[str | None] = mapped_column(String(64))
    access: Mapped[str | None] = mapped_column(String(32))
    frequency: Mapped[str | None] = mapped_column(String(64))

    # Not unique per slug — 18 slugs carry several defaults (one has four), so
    # a partial unique index here would reject the data as shipped.
    is_default_region: Mapped[bool | None] = mapped_column(Boolean)

    agency: Mapped[str | None] = mapped_column(String(255))
    source_freq: Mapped[str | None] = mapped_column(String(64))
    sourcing_note: Mapped[str | None] = mapped_column(Text)
    source_note: Mapped[str | None] = mapped_column(Text)
    used_in_formulas: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    commodity = relationship("CommodityIndex")

    # NOT stored, by design (DB-7): current_value, change_pct, volatility_pct,
    # cycle_pct, card_status and has_intel_block are moments in time that
    # recompute from the series. `volatility_pct` is also internally
    # contradictory in the source — the same series carries 12 on one card and
    # 55 on another — so importing it would enshrine a conflict.
    #
    # `shares_series_with` is likewise not stored: it is a denormalised,
    # self-referential list of slugs that derives exactly from grouping cards
    # by commodity_id.


class IndexMonthlyValue(Base):
    """The numbers, at the grain the source actually publishes.

    The drop's series are monthly; its quarterly files are rollups derived
    from them, verified reproducible to the last decimal. So monthly is
    stored and quarterly derives — storing both would be two sources of one
    truth.

    `IndexValue` (quarterly, region-keyed) is untouched and still backs the
    costing engine. This table is the drop's series data, which the new
    layers read.
    """

    __tablename__ = "index_monthly_values"
    __table_args__ = (
        UniqueConstraint("commodity_id", "year", "month", name="uq_imv_commodity_period"),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_imv_month"),
        CheckConstraint("kind IN ('actual', 'forecast')", name="ck_imv_kind"),
        Index("idx_imv_lookup", "commodity_id", "year", "month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id"), nullable=False
    )
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)

    # actual | forecast, carried in-band exactly as the source states it. The
    # drop's README is explicit that the two must never be averaged together,
    # so this is NOT NULL and every aggregate filters on it.
    #
    # Distinct from IndexProjectionRun/Point (Scrum 21), which holds *our*
    # computed projections with a method and a vintage. These are someone
    # else's forecast, imported as data — different provenance, so they live
    # apart rather than competing for one table.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    commodity = relationship("CommodityIndex")

    @property
    def quarter(self) -> int:
        """Derived, never stored — the source's own quarter column is always
        consistent with its month, so storing it would only create a way for
        the two to disagree."""
        return (self.month - 1) // 3 + 1
