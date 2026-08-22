from datetime import datetime, timezone
from sqlalchemy import Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FreightLane(Base):
    """Default landed-cost adjustments for a broad-region pair.

    Looked up by (origin_region, destination_region, mode). Values fill the
    buckets a price-level adjustment leaves blank.
    """

    __tablename__ = "freight_lanes"
    __table_args__ = (
        UniqueConstraint("origin_region", "destination_region", "mode",
                         name="uq_freight_lanes_route"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origin_region: Mapped[str] = mapped_column(String(20), ForeignKey("regions.code"), nullable=False)
    destination_region: Mapped[str] = mapped_column(String(20), ForeignKey("regions.code"), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="sea")
    adjustments: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
