"""Scrum 32 — AI cost modeler: a suggested cost structure, staged for review.

**Why this is not `EstimatorProposal`**, despite the plan saying to reuse it.
The rules are the same and are copied deliberately: draft-then-approve, upsert
rather than duplicate, and nothing real written until a human approves. The
*table* cannot be shared, because the subject is different. An
`EstimatorProposal` is keyed `(template_id, region)` with `template_id` NOT
NULL and approves into `FormulaTemplateComponent` + coverage — it is about a
catalog combo. This is about a team's own product with no decomposition, and it
approves into a `FormulaVersion` on a `CostModel`. Making `template_id`
nullable and branching the approve path on which subject a row happens to be
would be two features sharing one table and two approve paths; two tables with
one set of rules is the smaller thing.

Nothing here is ever a cost model on its own. A draft is an estimate and says
so: `provenance = ai_draft` follows it onto the FormulaVersion at promotion
(app/constants/trust.py's vocabulary), so the caveat survives the save.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AiCostDraft(Base):
    """One suggestion, for one product, at one rough price."""

    __tablename__ = "ai_cost_drafts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # Optional: the whole point is a product nobody has decomposed, and that
    # product may not exist as a row yet.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rough_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(10), nullable=True)
    region: Mapped[str | None] = mapped_column(String(20), nullable=True)

    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the model actually returned. Kept because a draft whose parse went
    # wrong is only debuggable against the words it came from, and because a
    # reviewer is entitled to see what was said rather than only what was read
    # out of it.
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="ai_draft", server_default="ai_draft")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The formula version this became, once promoted.
    promoted_cost_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_models.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    lines = relationship(
        "AiCostDraftLine", back_populates="draft", cascade="all, delete-orphan",
        order_by="AiCostDraftLine.sort_order",
    )


class AiCostDraftLine(Base):
    """One suggested component. Editable before promotion — that is the point."""

    __tablename__ = "ai_cost_draft_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_cost_drafts.id", ondelete="CASCADE"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    weight_pct: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    component_type: Mapped[str] = mapped_column(String(16), default="index", server_default="index")

    # What the model SAID the index is, kept verbatim even when it resolves —
    # a suggestion that had to be corrected is worth seeing.
    suggested_index: Mapped[str | None] = mapped_column(String(128), nullable=True)
    commodity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id"), nullable=True
    )
    # False when `suggested_index` matched nothing we track. Flagged, never
    # silently dropped: a language model inventing a plausible feed name is the
    # expected failure here, and a line that quietly disappeared would leave a
    # recipe that no longer sums to 100 with no explanation.
    index_resolved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    draft = relationship("AiCostDraft", back_populates="lines")
    commodity = relationship("CommodityIndex")
