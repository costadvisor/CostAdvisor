import uuid
from datetime import datetime, timezone
from sqlalchemy import Integer, String, Numeric, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    chemical_family_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chemical_families.id"), nullable=True
    )
    # Optional finer-grained taxonomy link: family -> subfamily -> product. Nullable
    # because existing products only carry a family and not every product is subfiled.
    subfamily_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("subfamilies.id", ondelete="SET NULL"), nullable=True
    )
    # The catalog formula this product is priced by (Scrum 58): cost models for
    # a linked product auto-load the template at their region. NULL = not
    # catalog-linked (hand-modelled or pre-catalog products).
    formula_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("formula_templates.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    formula: Mapped[str | None] = mapped_column(String(64))
    active_content: Mapped[float | None] = mapped_column(Numeric(4, 3))
    unit: Mapped[str] = mapped_column(String(10), default="kg")  # kg, t, lb
    custom_attributes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    team = relationship("Team", back_populates="products")
    chemical_family = relationship("ChemicalFamily", back_populates="products")
    subfamily = relationship("Subfamily", back_populates="products")
    cost_models = relationship(
        "CostModel", back_populates="product", cascade="all, delete-orphan"
    )
