"""Scrum 30 — supplier price-list import, landing as ActualPrice.

Deliberately NOT the same thing as Scrum 31b's quote extraction, even though
both start by handing a PDF to `services/quote_extraction.extract_quote`:

  /quotes      a one-off quote  -> QuoteRecordLine -> negotiation position
  /price-lists a recurring list -> ActualPrice     -> the gap in Monitor

The destination is what makes them different stories. An ActualPrice is keyed
on (cost_model, year, quarter), so every row has to resolve to one of *this
team's* cost models and to a period before it can land — and a price attached
to the wrong product corrupts a gap silently, with nothing on screen to say so.
That is why matching is a first-class, reviewable step here and does not exist
on the quote side at all.

Draft-then-commit, same two-table shape as the quote tables: the parse is
persisted immediately so it can be reviewed, left, and come back to; nothing
touches actual_prices until an explicit commit names the rows.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Three states, kept distinct on purpose. "fuzzy" is not a weak "exact": it is
# a match a human still has to agree with, and collapsing the two would let a
# near-miss commit itself.
MATCH_EXACT = "exact"
MATCH_FUZZY = "fuzzy"
MATCH_UNMATCHED = "unmatched"
MATCH_AMBIGUOUS = "ambiguous"


class PriceListRun(Base):
    """One uploaded price list. Created immediately on upload — the reviewable
    draft, never a price."""

    __tablename__ = "price_list_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    # Optional, and it does real work: declaring the supplier narrows matching
    # to that supplier's cost models, which is what turns several same-named
    # products across suppliers from an ambiguity into one answer.
    supplier_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # The PDF itself is never stored (no blob storage in this repo); the text is,
    # so a row's locator snippet stays independently checkable afterwards.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="extracted", server_default="extracted")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    rows = relationship(
        "PriceListRow", back_populates="run", cascade="all, delete-orphan",
        order_by="PriceListRow.line_index",
    )


class PriceListRow(Base):
    """One extracted product line, with the match the server proposed.

    `fields` is {name: {"value", "confidence", "locator"}} exactly as the quote
    extractor produces it — a field ABSENT from the dict was not found, which
    is never the same as found-and-empty.
    """

    __tablename__ = "price_list_rows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_list_runs.id", ondelete="CASCADE"), nullable=False
    )
    line_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # The proposed match. Nullable because "no match" is a real, common answer
    # and has to be storable rather than forcing a guess at parse time.
    matched_cost_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_models.id", ondelete="SET NULL"), nullable=True
    )
    match_confidence: Mapped[str] = mapped_column(
        String(16), default=MATCH_UNMATCHED, server_default=MATCH_UNMATCHED
    )
    # Ranked alternatives, so an unmatched or ambiguous row is a question the
    # reviewer can answer in place rather than a dead end.
    match_candidates: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Period the price applies to, derived from the document's own dates where
    # it states them. Null means the document did not say and a human must.
    period_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    period_quarter: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    actual_price_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("actual_prices.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    run = relationship("PriceListRun", back_populates="rows")
