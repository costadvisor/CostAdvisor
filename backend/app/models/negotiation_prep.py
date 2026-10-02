"""Scrum 29 — the guided half of the negotiation aid.

**The app does not predict the supplier's counter.** It holds no supplier-cost
data, and a fabricated counter-proposal playbook was deleted once already for
pretending otherwise (commit 03e0856). So the supplier's position is an INPUT:
the buyer records what was actually said, and the output is the evidence
answering it.

What is stored is therefore only the claim — the words, which driver it is
about, and the magnitude the supplier put on it. **The verdict is not stored.**
It is computed at read time from the live brief, because the driver's real
movement changes as index data lands; a stored verdict would quietly become a
different answer from the one the numbers now support, and a buyer would carry
it into a room.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SupplierClaim(Base):
    """One thing the supplier said, against a cost model and a period."""

    __tablename__ = "supplier_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    cost_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_models.id", ondelete="CASCADE"), nullable=False
    )
    # The period the claim is about — a supplier's argument is about a quarter,
    # and the same words a year later are checked against different movement.
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # Verbatim. Never paraphrased into a driver name: what they actually said is
    # the thing a buyer repeats back in the room.
    said: Mapped[str] = mapped_column(Text, nullable=False)

    # Which resolved cost line this is about. Nullable, and null is a real
    # answer: "freight pressure" on a DDP quote maps to no line at all, which
    # is itself the rebuttal.
    driver_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # The magnitude the supplier put on it, if they put one on it. Null means
    # they asserted a direction without a number — checkable, but differently.
    claimed_change_pct: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)

    # A buyer builds the script by choosing which claims to answer out loud.
    include_in_script: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    cost_model = relationship("CostModel")
