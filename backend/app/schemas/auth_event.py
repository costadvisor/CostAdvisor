import uuid
from datetime import datetime
from pydantic import BaseModel


class AuthEventOut(BaseModel):
    id: int
    user_id: uuid.UUID | None
    email: str
    event_type: str
    reason: str | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
