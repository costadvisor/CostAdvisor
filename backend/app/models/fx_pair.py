import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import Integer, Boolean, String, Numeric, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FxPair(Base):
    """Configurable FX currency pair — replaces the hardcoded _FX_PAIR_MAP."""
    __tablename__ = "fx_pairs"
    __table_args__ = (
        UniqueConstraint("from_currency", "to_currency", name="uq_fx_pairs_currencies"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    to_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    name: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)

    # "ecb" | "generic" | "manual" — drives scrape dispatch
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    scrape_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    scrape_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Daily live rate — updated by the scrape_fx_live Celery task
    live_rate: Mapped[Decimal | None] = mapped_column(Numeric(16, 6), nullable=True)
    live_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
