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
    # The product line of a custom product (one with no linked template). A
    # catalogue-linked product takes its line, sub-family and family from its
    # template instead (services/effective_lines.py), so this stays NULL there.
    product_line_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("product_lines.id", ondelete="SET NULL"),
        nullable=True, index=True,
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
    product_line = relationship("ProductLine")
    cost_models = relationship(
        "CostModel", back_populates="product", cascade="all, delete-orphan"
    )
