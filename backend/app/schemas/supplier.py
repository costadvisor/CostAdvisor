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
    # How THIS score was reached: "producer" when it pooled every supplier row
    # in the team naming the same canonical company, "raw_supplier_name" when it
    # did not (unmapped name, a name asserting several companies, or a company
    # the team spells only one way). Read from the model property, so it states
    # what actually happened per row rather than a blanket caveat.
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
    # Top-level default only. Scoring now resolves through the canonical
    # `Producer` master where it can, but that succeeds per supplier, not per
    # response — so the truthful flag is the per-row one on
    # SupplierTrustScoreOut above. This stays as the conservative wrapper-level
    # answer for a caller that reads no further.
    resolution: str = "raw_supplier_name"
    suppliers: list[SupplierTrustSummaryOut]
