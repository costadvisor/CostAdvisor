"""Supplier quote extraction (Scrum 31b).

Two pairs, mirroring the Scrum 27b sheet-round-trip philosophy: the draft
extraction is persisted immediately (so it's inspectable and reviewable),
but nothing lands in the permanent "quote record" until an explicit confirm
per line — a quote document can be multi-product/multi-tier, so review is
line-grained, not document-grained.
"""
import uuid
from datetime import date, datetime, timezone
from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class QuoteExtractionRun(Base):
    """One uploaded document. Created immediately on upload — this is the
    inspectable draft, never the quote record itself."""

    __tablename__ = "quote_extraction_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # The original file is never persisted (no blob storage in this repo) —
    # this is what keeps a line's locator snippet independently verifiable.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "extracted" until every line has been confirmed or rejected; "reviewed"
    # once none remain pending. Informational only — confirm/reject act on
    # individual lines regardless of this flag.
    status: Mapped[str] = mapped_column(String(16), default="extracted", server_default="extracted")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    lines = relationship(
        "QuoteExtractionLine", back_populates="run", cascade="all, delete-orphan",
        order_by="QuoteExtractionLine.line_index",
    )


class QuoteExtractionLine(Base):
    """One extracted candidate line (one product/tier). `fields` is
    {field_name: {"value", "confidence", "locator"}} — a field absent from
    the dict means it was not found, never a null placeholder (AC3)."""

    __tablename__ = "quote_extraction_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quote_extraction_runs.id", ondelete="CASCADE"), nullable=False
    )
    line_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    # Set once confirmed — the permanent row this draft line became.
    quote_record_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quote_record_lines.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    run = relationship("QuoteExtractionRun", back_populates="lines")


class QuoteRecord(Base):
    """The permanent destination — created lazily on a run's first
    confirmed line. Never written to by the extraction step itself."""

    __tablename__ = "quote_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quote_extraction_runs.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    lines = relationship("QuoteRecordLine", back_populates="quote_record", cascade="all, delete-orphan")


class QuoteRecordLine(Base):
    """One confirmed quote line — real typed columns (not the draft's JSONB
    blob) so the negotiation-position engine and anything else can read a
    line directly. `field_confidence` carries the extraction's confidence/
    locator forward onto the permanent record too, not just the draft."""

    __tablename__ = "quote_record_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quote_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quote_records.id", ondelete="CASCADE"), nullable=False
    )
    product_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(10), nullable=True)
    volume_tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    incoterm: Mapped[str | None] = mapped_column(String(8), nullable=True)
    named_place: Mapped[str | None] = mapped_column(String(128), nullable=True)
    quote_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    field_confidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    quote_record = relationship("QuoteRecord", back_populates="lines")
    resolved_product = relationship("Product")
