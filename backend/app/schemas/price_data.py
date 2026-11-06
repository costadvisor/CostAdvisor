import uuid
from datetime import datetime
from pydantic import BaseModel, Field, field_validator

from app.constants.incoterms import (
    is_valid as incoterm_is_valid,
    normalize as incoterm_normalize,
    COST_BUCKETS,
)
from app.schemas.cost_model import LandedCostAdjustment


def _validate_incoterm(v: str | None) -> str | None:
    v = incoterm_normalize(v)
    if v is None:
        return None
    if not incoterm_is_valid(v, allow_deprecated=True):
        raise ValueError(f"Unknown Incoterm code: {v}")
    return v


def _validate_adjustments(v: dict | None) -> dict | None:
    if v is None:
        return None
    out = {}
    for bucket, payload in v.items():
        if bucket not in COST_BUCKETS:
            raise ValueError(f"Unknown cost bucket: {bucket}")
        if payload is None:
            continue
        out[bucket] = LandedCostAdjustment.model_validate(payload).model_dump()
    return out or None


class ActualPriceOut(BaseModel):
    id: int
    cost_model_id: uuid.UUID
    year: int
    quarter: int
    price: float
    incoterm: str | None = None
    named_place: str | None = None
    landed_cost_adjustments: dict | None = None
    source_file: str | None
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class ActualPriceCreate(BaseModel):
    year: int
    quarter: int
    # A typo such as -5 fed gap, impact, benchmark and alerts as a real price.
    price: float = Field(gt=0)
    incoterm: str | None = None
    named_place: str | None = None
    landed_cost_adjustments: dict | None = None

    @field_validator("incoterm")
    @classmethod
    def valid_incoterm(cls, v):
        return _validate_incoterm(v)

    @field_validator("landed_cost_adjustments")
    @classmethod
    def valid_adjustments(cls, v):
        return _validate_adjustments(v)


class UploadPreview(BaseModel):
    rows: list[ActualPriceCreate]
    filename: str
    row_count: int
