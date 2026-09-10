import uuid
from datetime import datetime

from pydantic import BaseModel


class EstimatorProposalLineOut(BaseModel):
    id: uuid.UUID
    name: str
    component_type: str
    commodity_id: int | None = None
    weight_pct: float
    is_proxy: bool
    series_available: bool
    candidate_reason: str

    model_config = {"from_attributes": True}


class EstimatorProposalOut(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    region: str
    status: str  # "ai_draft" | "human_approved" | "rejected"
    evidence_summary: dict
    created_at: datetime
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    lines: list[EstimatorProposalLineOut] = []

    model_config = {"from_attributes": True}


class BacktestComboOut(BaseModel):
    template_id: str
    region: str
    evaluable: bool
    method: str | None = None
    match_fraction: float | None = None
    mean_weight_error: float | None = None


class BacktestReportOut(BaseModel):
    summary: dict
    combos: list[BacktestComboOut]
