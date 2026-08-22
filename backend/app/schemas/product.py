import uuid
from datetime import datetime
from pydantic import BaseModel


class ProductCreate(BaseModel):
    name: str
    formula: str | None = None
    active_content: float | None = None
    unit: str = "kg"
    chemical_family_id: int | None = None
    subfamily_id: int | None = None
    formula_template_id: uuid.UUID | None = None
    custom_attributes: dict | None = None


class ProductUpdate(BaseModel):
    name: str | None = None
    formula: str | None = None
    active_content: float | None = None
    unit: str | None = None
    chemical_family_id: int | None = None
    subfamily_id: int | None = None
    formula_template_id: uuid.UUID | None = None
    custom_attributes: dict | None = None


class ProductOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    created_by: uuid.UUID
    chemical_family_id: int | None
    subfamily_id: int | None
    formula_template_id: uuid.UUID | None = None
    formula_template_code: str | None = None
    formula_template_name: str | None = None
    name: str
    formula: str | None
    active_content: float | None
    unit: str
    custom_attributes: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
