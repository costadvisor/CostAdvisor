import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr


class AccessRequestCreate(BaseModel):
    email: EmailStr
    name: str | None = None
    company: str | None = None


class AccessRequestOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    company: str | None
    status: str
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by_email: str | None

    model_config = {"from_attributes": True}
