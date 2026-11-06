import uuid
from datetime import datetime
from pydantic import BaseModel


class ActualVolumeCreate(BaseModel):
    year: int
    quarter: int
    volume: float
    # None = the product's unit. A fixed "kg" default labelled every quantity
    # typed on a tonne-priced model as kilograms.
    unit: str | None = None


class ActualVolumeOut(BaseModel):
    id: int
    cost_model_id: uuid.UUID
    year: int
    quarter: int
    volume: float
    unit: str
    source_file: str | None
    uploaded_at: datetime

    model_config = {"from_attributes": True}
