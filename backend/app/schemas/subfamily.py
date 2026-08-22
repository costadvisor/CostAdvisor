import uuid

from pydantic import BaseModel


class SubfamilyCreate(BaseModel):
    family_id: int
    name: str
    code: str | None = None
    # None => create a platform subfamily (super-admin only). Set => create a team row.
    team_id: uuid.UUID | None = None


class SubfamilyForkRequest(BaseModel):
    # Team the platform subfamily should be forked into.
    team_id: uuid.UUID


class SubfamilyUpdate(BaseModel):
    name: str | None = None
    code: str | None = None


class SubfamilyOut(BaseModel):
    id: int
    family_id: int
    name: str
    code: str | None = None
    team_id: uuid.UUID | None = None
    origin_id: int | None = None

    model_config = {"from_attributes": True}
