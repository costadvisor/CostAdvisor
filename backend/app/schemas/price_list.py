"""Response contracts for the price-list import (Scrum 30)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class PriceListRowOut(BaseModel):
    id: uuid.UUID
    line_index: int
    # {field: {"value", "confidence", "locator"}}. A field absent from this
    # dict was not found in the document — never the same as found-and-empty.
    fields: dict
    matched_cost_model_id: uuid.UUID | None = None
    # exact | fuzzy | ambiguous | unmatched. Kept as four states because the
    # remedy differs: accept, check, choose, or find.
    match_confidence: str
    match_candidates: list | None = None
    period_year: int | None = None
    period_quarter: int | None = None
    status: str
    actual_price_id: int | None = None
    reviewed_at: datetime | None = None

    class Config:
        from_attributes = True


class PriceListRunOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    supplier_id: int | None = None
    filename: str
    status: str
    created_at: datetime
    rows: list[PriceListRowOut] = []

    class Config:
        from_attributes = True


class PriceListRunSummary(BaseModel):
    """List view — deliberately without `rows`, which can run to dozens per
    run and are never needed to pick one from a list."""
    id: uuid.UUID
    supplier_id: int | None = None
    filename: str
    status: str
    created_at: datetime
    row_count: int
    committed_count: int

    class Config:
        from_attributes = True


class RowCommit(BaseModel):
    """One row to commit. Every override is optional; omitted means "use what
    was extracted", which is the common case."""
    row_id: uuid.UUID
    cost_model_id: uuid.UUID | None = None
    year: int | None = Field(None, ge=1990, le=2100)
    quarter: int | None = Field(None, ge=1, le=4)
    price: float | None = Field(None, gt=0)


class CommitRequest(BaseModel):
    rows: list[RowCommit]


class RowCommitResult(BaseModel):
    row_id: uuid.UUID
    committed: bool
    # Present only on failure, and phrased for the reviewer rather than the log.
    error: str | None = None
    actual_price_id: int | None = None


class CommitResponse(BaseModel):
    committed: int
    failed: int
    results: list[RowCommitResult]
