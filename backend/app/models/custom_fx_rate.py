import uuid
from datetime import datetime, timezone
from sqlalchemy import SmallInteger, Numeric, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CustomFxRate(Base):
    __tablename__ = "custom_fx_rates"
    __table_args__ = (
        UniqueConstraint("team_id", "from_currency", "to_currency", "year", "quarter",
                         name="uq_custom_fx_rates"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    from_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    to_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # "fixed" | "live" | "quarter_ref" — drives resolution in fx_converter
    value_type: Mapped[str] = mapped_column(String(16), nullable=False, default="fixed")
    rate: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    ref_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    ref_quarter: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
