"""Cost-structure estimator drafts (Scrum 33).

A proposal is a fully separate staging area — it never touches
FormulaTemplateComponent/FormulaRegionCoverage until a human explicitly
approves it (approve_proposal in services/formula_estimator.py). One
proposal per (template_id, region); re-running the estimator upserts the
same row rather than creating a duplicate.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EstimatorProposal(Base):
    __tablename__ = "estimator_proposals"
    __table_args__ = (
        UniqueConstraint("template_id", "region", name="uq_ep_template_region"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("formula_templates.id", ondelete="CASCADE"), nullable=False
    )
    region: Mapped[str] = mapped_column(String(20), ForeignKey("regions.code"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ai_draft", server_default="ai_draft")
    # {"method": "sibling_region"|"correlation", "source_region": "Europe"|None,
    #  "priced_history_quarters": int|None} — the evidence basis for this proposal.
    evidence_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    template = relationship("FormulaTemplate")
    lines = relationship(
        "EstimatorProposalLine", back_populates="proposal", cascade="all, delete-orphan",
        order_by="EstimatorProposalLine.sort_order",
    )


class EstimatorProposalLine(Base):
    __tablename__ = "estimator_proposal_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estimator_proposals.id", ondelete="CASCADE"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    component_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "index" | "fixed"
    # No ondelete, matching FormulaTemplateComponent.commodity_id's own
    # convention — deleting a referenced index must fail loudly.
    commodity_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("commodity_indexes.id"), nullable=True)
    weight_pct: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    is_proxy: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # False when the candidate has no usable series in the TARGET region —
    # surfaced, never silently dropped (Scrum 33 AC3).
    series_available: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    candidate_reason: Mapped[str] = mapped_column(Text, nullable=False)

    proposal = relationship("EstimatorProposal", back_populates="lines")
    commodity = relationship("CommodityIndex")
