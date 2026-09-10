import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class QuoteExtractionLineOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    line_index: int
    fields: dict
    status: str  # pending | confirmed | rejected
    quote_record_line_id: uuid.UUID | None = None
    reviewed_by: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class QuoteExtractionRunOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    uploaded_by: uuid.UUID
    filename: str
    extracted_text: str | None = None
    status: str
    created_at: datetime
    lines: list[QuoteExtractionLineOut] = []

    model_config = {"from_attributes": True}


class QuoteLineConfirm(BaseModel):
    """Optional per-field overrides — a human can correct any extracted
    value before it lands in the permanent quote record."""
    product_reference: str | None = None
    resolved_product_id: uuid.UUID | None = None
    price: float | None = None
    currency: str | None = None
    unit: str | None = None
    volume_tier: str | None = None
    incoterm: str | None = None
    named_place: str | None = None
    quote_date: date | None = None
    valid_from: date | None = None
    valid_until: date | None = None


class QuoteRecordLineOut(BaseModel):
    id: uuid.UUID
    quote_record_id: uuid.UUID
    product_reference: str | None = None
    resolved_product_id: uuid.UUID | None = None
    price: float | None = None
    currency: str | None = None
    unit: str | None = None
    volume_tier: str | None = None
    incoterm: str | None = None
    named_place: str | None = None
    quote_date: date | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    field_confidence: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class QuoteRecordOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    created_by: uuid.UUID
    source_run_id: uuid.UUID | None = None
    filename: str
    created_at: datetime
    lines: list[QuoteRecordLineOut] = []

    model_config = {"from_attributes": True}
