import uuid

from pydantic import BaseModel


class ChemicalFamilyCreate(BaseModel):
    name: str
    code: str | None = None
    custom_attribute_schema: list[dict] | None = None
    # None => create a platform family (super-admin only). Set => create a team row.
    team_id: uuid.UUID | None = None


class ChemicalFamilyForkRequest(BaseModel):
    # Team the platform family should be forked into.
    team_id: uuid.UUID


class ChemicalFamilyUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    custom_attribute_schema: list[dict] | None = None


class ChemicalFamilyOut(BaseModel):
    id: int
    name: str
    code: str | None = None
    team_id: uuid.UUID | None = None
    origin_id: int | None = None
    custom_attribute_schema: list[dict] | None = None

    model_config = {"from_attributes": True}
