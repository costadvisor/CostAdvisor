"""Web-push subscriptions (PWA extras).

**Keyed on the user, not the team**, and with no RLS — a browser subscription
belongs to a person's device, and a person can sit on several teams. That is
the `refresh_tokens` shape (per-user, no `team_id`, scoped in the app layer),
which is the existing precedent for exactly this; `AlertSubscription` looks
similar but is genuinely team-scoped because an alert is about a team's data.

Started hardened deliberately: every read and write is filtered by
`current_user.id` in the router, never by an id the caller supplies. The
reference implementation this pattern comes from shipped an open policy first
and fixed it later; there is no reason to repeat that.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The push service's URL for this device. Unique: re-subscribing the same
    # browser returns the same endpoint, and without this a device would
    # accumulate a row per visit and be notified once per row.
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    p256dh_key: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_key: Mapped[str] = mapped_column(String(255), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # Last time the push service accepted a delivery — how a stale device is
    # recognised in the UI without guessing from created_at.
    last_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
