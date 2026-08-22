import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Boolean, Integer, ForeignKey, Date, Time, Text
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DemoHost(Base):
    __tablename__ = "demo_hosts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    slot_duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    # ISO weekday numbers: 0=Mon, 1=Tue, ..., 6=Sun
    working_days: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=lambda: [0, 1, 2, 3, 4])
    working_start: Mapped[str] = mapped_column(String(5), default="09:00")  # "HH:MM"
    working_end: Mapped[str] = mapped_column(String(5), default="18:00")
    google_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User", foreign_keys=[user_id])
    blocked_slots = relationship("DemoBlockedSlot", back_populates="host", cascade="all, delete-orphan")
    demo_requests = relationship("DemoRequest", back_populates="assigned_host")


class DemoBlockedSlot(Base):
    __tablename__ = "demo_blocked_slots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    host_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("demo_hosts.id", ondelete="CASCADE"), nullable=False
    )
    blocked_date: Mapped[str] = mapped_column(String(10), nullable=False)  # "YYYY-MM-DD"
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)     # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    host = relationship("DemoHost", back_populates="blocked_slots")


class DemoRequest(Base):
    __tablename__ = "demo_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    company: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_date: Mapped[str] = mapped_column(String(10), nullable=False)   # "YYYY-MM-DD"
    requested_start: Mapped[str] = mapped_column(String(5), nullable=False)   # "HH:MM"
    requested_end: Mapped[str] = mapped_column(String(5), nullable=False)
    visitor_timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    assigned_host_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("demo_hosts.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | accepted | rejected
    meet_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    assigned_host = relationship("DemoHost", back_populates="demo_requests")
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id])
