import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, field_validator, computed_field, model_validator

from app.constants.incoterms import (
    is_valid as incoterm_is_valid,
    normalize as incoterm_normalize,
    COST_BUCKETS,
)


def _validate_incoterm(v: str | None) -> str | None:
    v = incoterm_normalize(v)
    if v is None:
        return None
    if not incoterm_is_valid(v, allow_deprecated=True):
        raise ValueError(f"Unknown Incoterm code: {v}")
    return v


class LandedCostAdjustment(BaseModel):
    """One bucket value: flat (per unit, in price currency) or pct (of price)."""
    type: Literal["flat", "pct"]
    value: float

    @field_validator("value")
    @classmethod
    def non_negative(cls, v):
        if v < 0:
            raise ValueError("Adjustment value must be non-negative")
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
        # Coerce through the typed model so downstream code can rely on shape.
        out[bucket] = LandedCostAdjustment.model_validate(payload).model_dump()
    return out or None


class FormulaComponentItem(BaseModel):
    label: str
    commodity_name: str | None = None  # resolved to commodity_id on backend
    weight: float


class FormulaVersionCreate(BaseModel):
    formula_type: Literal['simple', 'advanced'] = 'simple'
    base_price: float
    base_year: int
    base_quarter: int
    margin_type: str = "pct"  # 'pct', 'fixed', 'unknown'
    margin_value: float | None = None
    incoterm: str | None = None
    named_place: str | None = None
    landed_cost_adjustments: dict | None = None
    components: list[FormulaComponentItem] = []
    # Advanced mode fields
    expression: str | None = None
    variables: dict | None = None
    notes: str | None = None

    @field_validator("base_price")
    @classmethod
    def base_price_positive(cls, v):
        if v <= 0:
            raise ValueError("Base price must be positive")
        return v

    @model_validator(mode='after')
    def validate_formula_fields(self):
        if self.formula_type == 'simple' and not self.components:
            raise ValueError("At least one formula component is required for simple mode")
        if self.formula_type == 'advanced' and not self.expression:
            raise ValueError("An expression is required for advanced formula mode")
        return self

    @field_validator("margin_type")
    @classmethod
    def valid_margin_type(cls, v):
        if v not in ("pct", "fixed", "unknown"):
            raise ValueError("margin_type must be 'pct', 'fixed', or 'unknown'")
        return v

    @field_validator("incoterm")
    @classmethod
    def valid_incoterm(cls, v):
        return _validate_incoterm(v)

    @field_validator("landed_cost_adjustments")
    @classmethod
    def valid_adjustments(cls, v):
        return _validate_adjustments(v)


class CostModelCreate(BaseModel):
    product_id: uuid.UUID
    supplier_id: int | None = None
    destination_country: str | None = None
    destination_region: str | None = None
    region: str = "Europe"
    currency: str = "USD"
    incoterm: str | None = None
    formula: FormulaVersionCreate

    @field_validator("incoterm")
    @classmethod
    def valid_incoterm(cls, v):
        return _validate_incoterm(v)


class CostModelUpdate(BaseModel):
    supplier_id: int | None = None
    destination_country: str | None = None
    destination_region: str | None = None
    region: str | None = None
    currency: str | None = None
    incoterm: str | None = None

    @field_validator("incoterm")
    @classmethod
    def valid_incoterm(cls, v):
        return _validate_incoterm(v)


# --- Output schemas ---

class FormulaComponentOut(BaseModel):
    id: int
    label: str
    commodity_id: int | None
    commodity_name: str | None = None
    weight: float

    model_config = {"from_attributes": True}


class FormulaVersionOut(BaseModel):
    id: int
    formula_type: str = 'simple'
    base_price: float
    base_year: int
    base_quarter: int
    margin_type: str
    margin_value: float | None
    incoterm: str | None = None
    named_place: str | None = None
    landed_cost_adjustments: dict | None = None
    expression: str | None = None
    variables: dict | None = None
    notes: str | None
    created_at: datetime
    updated_at: datetime | None = None
    components: list[FormulaComponentOut] = []

    @computed_field
    @property
    def quarter_label(self) -> str:
        return f"Q{self.base_quarter}-{self.base_year}"

    model_config = {"from_attributes": True}


class CostModelOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    product_id: uuid.UUID
    supplier_id: int | None
    destination_country: str | None
    destination_region: str | None = None
    region: str
    currency: str
    incoterm: str | None = None
    negotiation_state: str = "none"   # Scrum 25 collaboration flag
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    formula_versions: list[FormulaVersionOut] = []

    # Flattened product info for convenience
    product_name: str | None = None
    product_reference: str | None = None
    product_unit: str | None = None
    product_active_content: float | None = None
    supplier_name: str | None = None

    model_config = {"from_attributes": True}
