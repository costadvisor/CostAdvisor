"""Supplier trust & margin grading (Scrum 32).

A team's own read on a supplier — never a shared platform fact, since
`Supplier` itself is team-scoped and there is no cross-team canonical
producer entity in this repo (the alias-canonicalisation work the ticket
names as a hard dependency, SCRUM-77, doesn't exist here — this scores by
raw Supplier.id/name instead, flagged explicitly in the API response as
`resolution: "raw_supplier_name"` rather than hidden).
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SupplierTrustScore(Base):
    __tablename__ = "supplier_trust_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False
    )
    grain: Mapped[str] = mapped_column(String(16), nullable=False)  # "product" | "subfamily"
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    subfamily_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("subfamilies.id", ondelete="SET NULL"), nullable=True
    )
    # str(product_id) or str(subfamily_id) — always non-null, unlike the two
    # columns above. Postgres treats every NULL as distinct in a unique
    # constraint, which would silently defeat upsert-in-place on recompute
    # if the constraint were keyed on the nullable FK columns directly.
    grain_key: Mapped[str] = mapped_column(String(64), nullable=False)
    insufficient_data: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    grade: Mapped[str | None] = mapped_column(String(2), nullable=True)
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    supplier = relationship("Supplier")
    product = relationship("Product")
    subfamily = relationship("Subfamily")
