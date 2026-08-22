import uuid
from pydantic import BaseModel


class PermissionOut(BaseModel):
    id: uuid.UUID
    key: str
    label: str
    category: str
    action: str
    model_config = {"from_attributes": True}


class PermissionCreate(BaseModel):
    key: str
    label: str
    category: str
    action: str


class RoleOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID | None
    name: str
    description: str | None
    permission_count: int = 0
    model_config = {"from_attributes": True}


class RoleDetailOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID | None
    name: str
    description: str | None
    permissions: list[PermissionOut]
    model_config = {"from_attributes": True}


class RoleCreate(BaseModel):
    name: str
    description: str | None = None
    permission_ids: list[uuid.UUID] = []


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    permission_ids: list[uuid.UUID] = []


class PlanOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    is_default: bool
    permission_count: int = 0
    model_config = {"from_attributes": True}


class PlanDetailOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    is_default: bool
    permissions: list[PermissionOut]
    model_config = {"from_attributes": True}


class PlanCreate(BaseModel):
    name: str
    description: str | None = None
    is_default: bool = False
    permission_ids: list[uuid.UUID] = []


class PlanUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_default: bool | None = None
    permission_ids: list[uuid.UUID] = []


class MemberRoleAssign(BaseModel):
    user_id: uuid.UUID
    role_id: uuid.UUID


class MemberRoleOut(BaseModel):
    user_id: uuid.UUID
    display_name: str | None
    email: str | None
    membership_role: str
    assigned_roles: list[RoleOut]
