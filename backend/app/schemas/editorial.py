"""Editorial block API contract (Wave 3, SCRUM-76 / INT-2).

Every response carries the provenance state **and its badge** — the state
machine and the customer-facing claim are the same thing, so the mapping ships
with the API rather than being re-derived by whoever builds the surface.
"""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class BlockCreate(BaseModel):
    subject_type: str
    subject_code: str = Field(min_length=1, max_length=160)
    block_type: str
    # None = the "*" wildcard the dated outlooks use.
    region: str | None = None
    body_text: str | None = None
    body_json: dict | list | None = None
    body_format: str = "text"
    provenance: str = "imported"
    internal_note: str | None = None
    source_note: str | None = None
    expires_at: date | None = None
    # True authors a platform block (gated on the platform permission);
    # otherwise the block belongs to the calling team.
    platform: bool = False


class BlockEdit(BaseModel):
    body_text: str | None = None
    body_json: dict | list | None = None
    body_format: str = "text"
    # An edit is not an approval — the default state for an authored change.
    provenance: str = "human_edited"
    change_note: str | None = None
    internal_note: str | None = None
    source_note: str | None = None
    expires_at: date | None = None


class VersionOut(BaseModel):
    id: uuid.UUID
    version_no: int
    body_text: str | None = None
    body_json: dict | list | None = None
    body_format: str
    provenance: str
    change_note: str | None = None
    authored_by: uuid.UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProvenanceBadge(BaseModel):
    label: str
    # None only for `human_approved` — approval is what clears the caveat.
    caveat: str | None = None
    reviewed: bool


class BlockOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID | None = None
    origin_id: uuid.UUID | None = None
    subject_type: str
    subject_code: str
    block_type: str
    region: str | None = None
    # Convenience joins. Null wherever the subject has no row in our taxonomy —
    # which is a normal state, not a load failure.
    template_id: uuid.UUID | None = None
    commodity_id: int | None = None
    family_id: int | None = None
    subfamily_id: int | None = None
    body_format: str
    provenance: str
    badge: ProvenanceBadge
    current_version_no: int | None = None
    body_text: str | None = None
    body_json: dict | list | None = None
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    expires_at: date | None = None
    is_stale: bool = False
    internal_note: str | None = None
    source_note: str | None = None
    created_at: datetime
    updated_at: datetime


class CardOut(BaseModel):
    subject_type: str
    subject_code: str
    region: str | None = None
    # Keyed by block_type so a consumer reads one card without scanning a list.
    blocks: dict[str, BlockOut] = {}
    # "team:region" / "platform:wildcard" etc — which side and which grain each
    # block was resolved from.
    resolved_from: dict[str, str] = {}
    # The derived payload (series, components, cycle, seasonality, volatility,
    # tier) is SCRUM-75's endpoint at formula x region combo grain. Named here
    # so a consumer knows the card is two calls by design, not one that forgot
    # half its content.
    derived_payload_endpoint: str | None = None
