import uuid
from datetime import datetime

from pydantic import BaseModel


class TeamProviderCredentialCreate(BaseModel):
    team_id: uuid.UUID
    provider: str
    credential: dict  # provider-specific fields (e.g. {"api_key": "..."}); never echoed back


class TeamProviderCredentialOut(BaseModel):
    id: int
    team_id: uuid.UUID
    provider: str
    status: str  # unverified | ok | expired | rejected | error
    last_verified_at: datetime | None = None
    last_error: str | None = None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # Deliberately no `credential`/`credential_encrypted` field — nothing to leak.

    model_config = {"from_attributes": True}


class ProviderInfoOut(BaseModel):
    key: str
    label: str
    adapter_available: bool
