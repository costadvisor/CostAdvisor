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
    provider: Mapped[str | None] = mapped_column(String(64))      # e.g. ECB, EIA, Eurostat, FRED, World Bank
    frequency: Mapped[str | None] = mapped_column(String(16))     # e.g. Daily, Weekly, Monthly, Quarterly
    source_url: Mapped[str | None] = mapped_column(String(512))
    scrape_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quoted_incoterm: Mapped[str | None] = mapped_column(String(8), nullable=True)
    quoted_named_place: Mapped[str | None] = mapped_column(String(128), nullable=True)

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
    )  # "manual" | "scrape_url" | "upload" | "fixed"
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