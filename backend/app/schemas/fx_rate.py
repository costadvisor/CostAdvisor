import uuid
from datetime import datetime, date
from pydantic import BaseModel


class FxDailyRateOut(BaseModel):
    date: date
    rate: float

    model_config = {"from_attributes": True}


class FxRateOut(BaseModel):
    id: int
    from_currency: str
    to_currency: str
    year: int
    quarter: int
    rate: float
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class CustomFxRateOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    from_currency: str
    to_currency: str
    year: int
    quarter: int
    value_type: str  # "fixed" | "live" | "quarter_ref"
    rate: float | None
    ref_year: int | None
    ref_quarter: int | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class FxRateUpsert(BaseModel):
    from_currency: str
    to_currency: str
    year: int
    quarter: int
    rate: float


class CustomFxRateUpsert(BaseModel):
    team_id: uuid.UUID
    from_currency: str
    to_currency: str
    year: int
    quarter: int
    value_type: str = "fixed"
    rate: float | None = None
    ref_year: int | None = None
    ref_quarter: int | None = None
