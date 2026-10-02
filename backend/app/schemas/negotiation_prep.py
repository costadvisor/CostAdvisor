"""Response contracts for the negotiation prep flow (Scrum 29)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ClaimCreate(BaseModel):
    # What the supplier actually said, verbatim. Not a driver name: this is the
    # sentence a buyer repeats back in the room.
    said: str = Field(min_length=1, max_length=2000)
    year: int = Field(ge=1990, le=2100)
    quarter: int = Field(ge=1, le=4)
    # Null is a real answer, not an omission: "freight pressure" on a DDP quote
    # maps to no cost line, and that is itself the rebuttal.
    driver_label: str | None = Field(None, max_length=128)
    claimed_change_pct: float | None = None


class ClaimUpdate(BaseModel):
    driver_label: str | None = None
    claimed_change_pct: float | None = None
    include_in_script: bool | None = None


class ClaimOut(BaseModel):
    id: uuid.UUID
    cost_model_id: uuid.UUID
    year: int
    quarter: int
    said: str
    driver_label: str | None = None
    claimed_change_pct: float | None = None
    include_in_script: bool
    created_at: datetime

    class Config:
        from_attributes = True


class CheckedClaimOut(BaseModel):
    claim_id: uuid.UUID
    said: str
    driver_label: str | None = None
    claimed_change_pct: float | None = None
    # What the index really did over the brief's own window. Null when there is
    # no driver to check against.
    actual_change_pct: float | None = None
    weight_pct: float | None = None
    verdict: str
    verdict_label: str
    note: str
    include_in_script: bool


class PositionLadder(BaseModel):
    """Floor / target / their ask, in the brief's own currency and unit.

    `floor` is `BriefResult.current_floor` — the indexed cost before margin,
    already shipped and already distinct from should-cost.
    """
    currency: str
    unit: str
    floor: float | None = None
    should_cost: float
    current_price: float | None = None
    gap: float | None = None
    gap_pct: float | None = None
    # Everything above should-cost. Named for what it is: the should-cost has
    # already consumed every verified index movement, so nothing the supplier
    # cites can re-explain this.
    unexplained: float | None = None


class AvailableDriver(BaseModel):
    """A cost line a claim can be attached to — the picker's options, so a
    buyer chooses from the real recipe instead of typing a name that matches
    nothing."""
    label: str
    index_name: str | None = None
    change_pct: float
    weight_pct: float | None = None
    direction: str


class PrepOut(BaseModel):
    cost_model_id: uuid.UUID
    product_name: str
    supplier_name: str | None = None
    period_label: str
    ladder: PositionLadder
    drivers: list[AvailableDriver]
    claims: list[CheckedClaimOut]
    script: list[str]
    # Surfaced rather than buried: a line riding flat because its index is
    # missing is a line you must not quote in the room.
    data_gaps: list[str] = []
