import uuid
from datetime import datetime
from pydantic import BaseModel


class TeamCreate(BaseModel):
    name: str


class TeamOut(BaseModel):
    id: uuid.UUID
    name: str
    created_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class TeamMembershipOut(BaseModel):
    team_id: uuid.UUID
    role: str
    joined_at: datetime
    team: TeamOut | None = None

    model_config = {"from_attributes": True}


class RoleChip(BaseModel):
    id: uuid.UUID
    name: str


class TeamMemberOut(BaseModel):
    user_id: uuid.UUID
    role: str
    joined_at: datetime
    email: str | None = None
    display_name: str | None = None
    custom_roles: list[RoleChip] = []
    platform_role_names: list[str] = []

    model_config = {"from_attributes": True}


class InviteRequest(BaseModel):
    email: str
    role: str = "member"


class RoleUpdate(BaseModel):
    role: str  # 'owner' (transfer), 'admin', or 'member'