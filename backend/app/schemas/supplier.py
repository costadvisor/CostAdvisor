import uuid
from datetime import datetime
from pydantic import BaseModel


class SupplierCreate(BaseModel):
    name: str
    country: str | None = None


class SupplierOut(BaseModel):
    id: int
    team_id: uuid.UUID
    name: str
    country: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Trust & margin grading (Scrum 32) ───────────────────────────────────────

class SupplierTrustScoreOut(BaseModel):
    id: int
    supplier_id: int
    grain: str  # "product" | "subfamily"
    product_id: uuid.UUID | None = None
    subfamily_id: int | None = None
    insufficient_data: bool
    score: float | None
    grade: str | None
    inputs: dict
    computed_at: datetime
    # Same disclosure as SupplierTrustScoresResponse.resolution below, repeated
    # here so the single-supplier endpoints (which return a bare list of these,
    # not the wrapped multi-supplier response) carry it too — not just the
    # all-suppliers listing.
    resolution: str = "raw_supplier_name"

    model_config = {"from_attributes": True}


class SupplierTrustSummaryOut(BaseModel):
    supplier_id: int
    supplier_name: str
    overall_score: float | None
    overall_grade: str | None
    insufficient_data: bool
    scores: list[SupplierTrustScoreOut] = []


class SupplierTrustScoresResponse(BaseModel):
    # Deliberately not resolved through a canonical producer entity — see
    # services/supplier_trust.py's module docstring. Stated here rather than
    # left implicit so a supplier under two spellings reads as a known
    # limitation, not a bug, wherever this response is consumed.
    resolution: str = "raw_supplier_name"
    suppliers: list[SupplierTrustSummaryOut]
