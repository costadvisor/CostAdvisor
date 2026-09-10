"""Support system API contract — threads, messages, canned responses, FAQ."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ThreadCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    # 'team': every team member can see and reply. 'person' (default): only
    # the creator + support staff can, even though a teammate's own query
    # would pass the team-level RLS check — enforced in the router.
    scope: str = "person"


class MessageCreate(BaseModel):
    body: str = Field(min_length=1)
    canned_response_id: uuid.UUID | None = None


class ThreadStatusUpdate(BaseModel):
    status: str


class MessageOut(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID
    author_name: str | None = None
    is_staff: bool
    body: str
    canned_response_id: uuid.UUID | None
    created_at: datetime

    class Config:
        from_attributes = True


class ThreadOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    team_name: str | None = None
    user_id: uuid.UUID
    user_name: str | None = None
    scope: str
    subject: str
    status: str
    assigned_admin_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0

    class Config:
        from_attributes = True


class ThreadDetailOut(ThreadOut):
    messages: list[MessageOut] = []


class CannedResponseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1)
    category: str | None = None


class CannedResponseOut(BaseModel):
    id: uuid.UUID
    title: str
    body: str
    category: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FAQCreate(BaseModel):
    category: str = Field(min_length=1, max_length=50)
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(min_length=1)
    sort_order: int = 0
    published: bool = True


class FAQOut(BaseModel):
    id: uuid.UUID
    category: str
    question: str
    answer: str
    sort_order: int
    published: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
