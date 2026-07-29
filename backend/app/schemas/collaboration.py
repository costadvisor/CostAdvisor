import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# Negotiation flag states (Scrum 25)
NEGOTIATION_STATES = {"none", "in_negotiation", "agreed", "under_review"}


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    parent_note_id: uuid.UUID | None = None


class NoteOut(BaseModel):
    id: uuid.UUID
    cost_model_id: uuid.UUID
    author_user_id: uuid.UUID
    author_name: str | None = None
    parent_note_id: uuid.UUID | None = None
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}


class FlagUpdate(BaseModel):
    negotiation_state: str


class FlagOut(BaseModel):
    cost_model_id: uuid.UUID
    negotiation_state: str
