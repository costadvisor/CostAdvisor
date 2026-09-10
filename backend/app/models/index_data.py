import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String, Integer, SmallInteger, Numeric, Boolean, DateTime, Text,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CommodityIndex(Base):
    __tablename__ = "commodity_indexes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(3))
    category: Mapped[str | None] = mapped_column(String(64))
    # The publishing agency. Widened from String(64) in DB-5: the drop's
    # agency strings run to 72 chars, several being a sentence rather than a
    # name ("ICIS (directional commentary only — subscription required...)").
    provider: Mapped[str | None] = mapped_column(String(255))     # e.g. ECB, EIA, Eurostat, FRED, World Bank
    # Widened from String(16) for the same reason — the drop states compound
    # cadences like "Quarterly (NA/EU) · Annual (CN/IN/MEA/LA/APAC)" (45 ch).
    frequency: Mapped[str | None] = mapped_column(String(64))     # e.g. Daily, Weekly, Monthly, Quarterly
    source_url: Mapped[str | None] = mapped_column(String(512))
    scrape_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quoted_incoterm: Mapped[str | None] = mapped_column(String(8), nullable=True)
    quoted_named_place: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ── Price-series layer (Scrum 74 / DB-5 + DB-6) ────────────────────────────
    # This table IS the price series in the three-layer model; the display
    # grouping moved out to IndexCard and the resolution join to TypeCode.
    # See app/models/index_layer.py.
    #
    # The drop's stable series key (`brent`, `lab-eu`). NULL on rows seeded
    # before the drop — they were never part of that vocabulary.
    commodity_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    # What the numbers ARE. Every series in the drop is `index_level` with
    # base 100 = Jan 2023 — nothing here is money, which is why a should-cost
    # gap can be stated in percent but never in currency from this data alone.
    # Storing it explicitly is what stops an engine or a screen implying money
    # that is not there.
    value_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    base_period: Mapped[str | None] = mapped_column(String(16), nullable=True)  # e.g. "2023-01"
    # The source's own declared region for the series. Informational only —
    # IndexCard.region is authoritative for resolution, because the series key
    # is not a reliable region indicator (`-ppi`/`-wb`/`-mb` are data sources,
    # and 23 of 28 `multi` cards sit on a region-tokened series).
    source_region: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Metadata + proxy mapping (Scrum 57) — all on the region-agnostic index ──
    access_tier: Mapped[str | None] = mapped_column(String(16), nullable=True)        # Free / Partial / Subscription
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)               # feedstock / energy / fixed
    # How we obtain a live number: free / good_proxy / weak_proxy / blocked.
    retrieval_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    free_source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    free_source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Structured spec (base_index + operation + spread + recalibration + note),
    # editable in the admin proxy menu (SCRUM-67), executed by FD-1 (SCRUM-80).
    proxy_logic: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # This index is a proxy standing in FOR another (real) index.
    proxy_for_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id", ondelete="SET NULL"), nullable=True
    )
    # Composite / calculated index: value is computed live from OTHER indexes via an
    # advanced expression (e.g. "0.6*Graphite + 0.3*Wood + FC"). `composite_variables`
    # maps each variable to an index or a fixed value, same shape as FormulaVersion.variables:
    #   { "Graphite": {"type":"index","commodity_id":N}, "FC": {"type":"fixed","value":X} }
    composite_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    composite_variables: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Region this composite is computed for. NULL = region-agnostic: follow the
    # region the caller asked for, defaulting to GLOBAL (the original behaviour).
    # Not a FK — this table is otherwise region-free by design (region lives on
    # index_values, Scrum 57), so the code is validated at the API layer.
    composite_region: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Relationships
    values = relationship("IndexValue", back_populates="commodity", lazy="dynamic")
    proxy_for = relationship("CommodityIndex", remote_side=[id])


class IndexValue(Base):
    __tablename__ = "index_values"
    __table_args__ = (
        UniqueConstraint("commodity_id", "region", "year", "quarter"),
        Index("idx_index_values_lookup", "commodity_id", "region", "year", "quarter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id"), nullable=False
    )
    region: Mapped[str] = mapped_column(String(20), ForeignKey("regions.code"), nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="scraped")
    scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    commodity = relationship("CommodityIndex", back_populates="values")


class IndexOverride(Base):
    __tablename__ = "index_overrides"
    __table_args__ = (
        UniqueConstraint("team_id", "commodity_id", "region", "year", "quarter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE")
    )
    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id")
    )
    region: Mapped[str] = mapped_column(String(20), ForeignKey("regions.code"), nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    value: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    source_file: Mapped[str | None] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class TeamIndexSource(Base):
    """Configuration for how a team obtains override values for a commodity+region."""

    __tablename__ = "team_index_sources"
    __table_args__ = (
        UniqueConstraint("team_id", "commodity_id", "region"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    commodity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id"), nullable=False
    )
    region: Mapped[str] = mapped_column(String(20), ForeignKey("regions.code"), nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "manual" | "scrape_url" | "upload" | "fixed" | "provider_credential"
    # For "provider_credential": scrape_config carries {"provider": "...", "series_id": "..."}.
    # The secret itself lives in TeamProviderCredential, keyed (team_id, provider) —
    # not duplicated per commodity/region row, so one vendor subscription rotates once.
    scrape_url: Mapped[str | None] = mapped_column(String(512))
    scrape_config: Mapped[dict | None] = mapped_column(JSONB)
    source_file: Mapped[str | None] = mapped_column(String(255))
    fixed_value: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    commodity = relationship("CommodityIndex")


class TeamProviderCredential(Base):
    """A team's own entitlement with a paid index provider (Fastmarkets, Argus,
    ICIS, ...), keyed by (team_id, provider) so every TeamIndexSource row that
    points at that provider shares one credential to rotate (Scrum 26).

    The secret is Fernet-encrypted JSON (shape varies per provider — API key vs
    client-id+secret vs basic auth) — see services/provider_credentials.py.
    `status`/`last_error` are updated on every fetch attempt so a stale/rejected
    credential is visible without re-triggering a fetch. "missing" is never
    stored here — it's a transient error raised when no row exists at all.

    NOTE (future re-key risk): TeamIndexSource is currently keyed
    (team_id, commodity_id, region). A referenced future re-keying of that grain
    does not exist anywhere in this repo today — this table's own key
    (team_id, provider) is independent of commodity/region, so it is unaffected
    either way; only TeamIndexSource's scrape_config pointer would need
    revisiting if that migration ever lands.
    """

    __tablename__ = "team_provider_credentials"
    __table_args__ = (
        UniqueConstraint("team_id", "provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)  # normalized lowercase
    credential_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unverified"
    )  # unverified | ok | expired | rejected | error
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )