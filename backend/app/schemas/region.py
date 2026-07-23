from pydantic import BaseModel


class RegionCreate(BaseModel):
    code: str
    name: str
    # Set to make this a subregion (child) of another region. None = top-level.
    parent_id: int | None = None


class RegionUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    parent_id: int | None = None


class RegionOut(BaseModel):
    id: int
    code: str
    name: str
    parent_id: int | None = None

    model_config = {"from_attributes": True}
