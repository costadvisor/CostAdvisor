import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AuthEvent(Base):
    """Scrum 10 — login/logout audit trail. Deliberately separate from AuditLog:
    `audit_logs.team_id`/`user_id` are NOT NULL, but a login (or a rejected signup
    attempt) is platform-level and may have no team yet, or no matched user at all.
    No team_id here, so no RLS policy is needed (mirrors the `team_memberships`
    RLS-bootstrap exemption — this table isn't tenant data)."""
    __tablename__ = "auth_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)  # login_success|login_failed|logout
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User")
