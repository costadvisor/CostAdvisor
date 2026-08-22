import uuid
from datetime import datetime
from pydantic import BaseModel


class TeamInviteOut(BaseModel):
    id: uuid.UUID
    invited_email: str
    role: str
    invited_by_name: str | None
    invited_by_email: str
    created_at: datetime
    expires_at: datetime
    status: str

    model_config = {"from_attributes": True}


class PendingInviteOut(BaseModel):
    id: uuid.UUID
    token: str
    team_id: uuid.UUID
    team_name: str
    role: str
    invited_by_name: str | None
    invited_by_email: str
    created_at: datetime
    expires_at: datetime
    status: str = "pending"
    accepted_at: datetime | None = None

    model_config = {"from_attributes": True}
