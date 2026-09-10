import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Integer, SmallInteger, String, Numeric, Text, Boolean,
    DateTime, ForeignKey, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CostModel(Base):
    __tablename__ = "cost_models"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE")
    )
    supplier_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("suppliers.id"), nullable=True
    )
    destination_country: Mapped[str | None] = mapped_column(String(64))
    destination_region: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("regions.code"), nullable=True
    )
    region: Mapped[str] = mapped_column(String(20), ForeignKey("regions.code"), default="Europe")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    incoterm: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Scrum 25 — negotiation flag: none / in_negotiation / agreed / under_review
    negotiation_state: Mapped[str] = mapped_column(String(20), default="none", server_default="none")
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    team = relationship("Team", back_populates="cost_models")
    product = relationship("Product", back_populates="cost_models")
    supplier = relationship("Supplier", back_populates="cost_models")
    formula_versions = relationship(
        "FormulaVersion", back_populates="cost_model",
        cascade="all, delete-orphan", lazy="selectin",
        order_by="(FormulaVersion.base_year.desc(), FormulaVersion.base_quarter.desc())",
    )
    actual_prices = relationship(
        "ActualPrice", back_populates="cost_model",
        cascade="all, delete-orphan", lazy="dynamic",
    )
    actual_volumes = relationship(
        "ActualVolume", back_populates="cost_model",
        cascade="all, delete-orphan", lazy="dynamic",
    )

    @property
    def current_formula(self):
        """Return the latest formula version (sorted desc by year/quarter)."""
        return self.formula_versions[0] if self.formula_versions else None

    def formula_for_period(self, year: int, quarter: int):
        """Return the formula version active for a given period.
        Versions are sorted desc by (base_year, base_quarter).
        Return the first version where (base_year, base_quarter) <= (year, quarter).
        Fallback to the earliest version."""
        for fv in self.formula_versions:
            if (fv.base_year, fv.base_quarter) <= (year, quarter):
                return fv
        # Fallback to earliest (last in desc-sorted list)
        return self.formula_versions[-1] if self.formula_versions else None


class FormulaVersion(Base):
    __tablename__ = "formula_versions"
    __table_args__ = (
        UniqueConstraint("cost_model_id", "base_year", "base_quarter",
                         name="uq_formula_versions_model_quarter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cost_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_models.id", ondelete="CASCADE")
    )
    base_price: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    base_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    base_quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    formula_type: Mapped[str] = mapped_column(String(10), default="simple", server_default="simple")
    expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    variables: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    margin_type: Mapped[str] = mapped_column(String(10), default="pct")  # 'pct', 'fixed', 'unknown'
    margin_value: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    incoterm: Mapped[str | None] = mapped_column(String(8), nullable=True)
    named_place: Mapped[str | None] = mapped_column(String(128), nullable=True)
    landed_cost_adjustments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Scrum 28b — which catalog combo this version was priced from (NULL = a
    # hand-built formula, unrelated to the catalog, exactly today's behavior).
    source_coverage_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("formula_region_coverage.id", ondelete="SET NULL"), nullable=True
    )
    # "pinned" (frozen at save — a negotiated contract must not drift) or
    # "tracking" (recomputed live from the catalog recipe on every
    # evaluation) — NULL alongside a NULL source_coverage_id for anything
    # not catalog-linked. See services/formula_resolver.get_effective_lines.
    link_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    cost_model = relationship("CostModel", back_populates="formula_versions")
    components = relationship(
        "FormulaComponent", back_populates="formula_version",
        cascade="all, delete-orphan", lazy="selectin",
    )
    source_coverage = relationship("FormulaRegionCoverage")


class FormulaComponent(Base):
    __tablename__ = "formula_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    formula_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("formula_versions.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    commodity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id"), nullable=True
    )
    weight: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    # Scrum 28b — "index" | "fixed" | NULL (legacy row, behaves exactly as
    # before this column existed). Disambiguates commodity_id IS NULL: a
    # "fixed" line is deliberately flat; an "index" line with no commodity_id
    # is a broken link that should surface as a gap, never ride flat silently.
    component_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Provenance only — never read by the costing engine's calculation path,
    # just carried through for inspection/display (matches GET /formulas/{id}/resolve).
    depth: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    via_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("formula_templates.id", ondelete="SET NULL"), nullable=True
    )
    line_region: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_proxy: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Relationships
    formula_version = relationship("FormulaVersion", back_populates="components")
    commodity = relationship("CommodityIndex")
    via_template = relationship("FormulaTemplate")

    @property
    def commodity_name(self) -> str | None:
        return self.commodity.name if self.commodity else None

    @property
    def via_template_name(self) -> str | None:
        return self.via_template.name if self.via_template else None
