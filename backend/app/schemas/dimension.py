"""Dimension + producer API contract (Wave 3, SCRUM-77 / INT-3).

Every hit carries its own audit trail — the alias that matched, the region the
claim applies to, and whether it is a platform assertion or a team override.
A bare list of product names cannot be checked by the person who has to act on
it, which is the whole point of the facet.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TermOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID | None = None
    origin_id: uuid.UUID | None = None
    kind: str
    code: str
    label: str
    description: str | None = None
    sort_order: int = 0
    is_active: bool = True
    source: str

    model_config = {"from_attributes": True}


class TermCreate(BaseModel):
    kind: str
    code: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = None
    sort_order: int = 0
    # True creates a platform term (gated on the platform permission); otherwise
    # the term belongs to the calling team.
    platform: bool = False


class AliasCreate(BaseModel):
    raw_value: str = Field(min_length=1, max_length=400)
    platform: bool = False


class AliasOut(BaseModel):
    id: uuid.UUID
    term_id: uuid.UUID
    team_id: uuid.UUID | None = None
    kind: str
    raw_value: str
    normalized: str
    source: str

    model_config = {"from_attributes": True}


class AssertionCreate(BaseModel):
    subject_type: str
    subject_code: str = Field(min_length=1, max_length=160)
    # None = applies to every region (the FormulaTemplateComponent.region
    # semantic, reused).
    region: str | None = None
    raw_value: str | None = None
    platform: bool = False


class HitOut(BaseModel):
    subject_type: str
    subject_code: str
    region: str | None = None
    scope: str                       # "platform" | "team"
    term_code: str
    term_label: str
    raw_value: str | None = None
    matched_alias: str | None = None
    source: str
    template_id: uuid.UUID | None = None
    template_name: str | None = None
    # Team grain only.
    product_id: uuid.UUID | None = None
    product_name: str | None = None
    cost_model_id: uuid.UUID | None = None
    cost_model_region: str | None = None
    region_applies: bool | None = None


class FacetOut(BaseModel):
    kind: str
    code: str
    # "platform" backs the Intelligence library (formula tiles); "team" backs
    # Portfolio and the audit use case. The same join, two grains.
    grain: str
    total: int
    hits: list[HitOut] = []


class SubjectDimensionsOut(BaseModel):
    subject_type: str
    subject_code: str
    region: str | None = None
    # Keyed by kind — the dimension half of the ID card that SCRUM-76's
    # composed read (CON-7) folds in.
    dimensions: dict[str, list[dict]] = {}


class UnresolvedOut(BaseModel):
    kind: str
    raw_value: str
    normalized: str
    # How many source assertions this one value blocked — what makes the queue
    # rankable rather than alphabetical.
    occurrences: int
    sample_subjects: list | None = None
    reason: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime

    model_config = {"from_attributes": True}


class ProducerOut(BaseModel):
    id: uuid.UUID
    name: str
    normalized_name: str
    hq_country: str | None = None
    notes: str | None = None
    source: str
    alias_count: int = 0

    model_config = {"from_attributes": True}


class ProducerFormulaOut(BaseModel):
    subject_code: str
    template_id: uuid.UUID | None = None
    region: str | None = None
    # Null whenever the share was not disclosed — 99.0% of source rows.
    share_pct: float | None = None
    # The flag that stops "BASF — 0% market share" reaching a customer.
    share_disclosed: bool
    hq_country: str | None = None
    regions_raw: list | None = None
    tags: list | None = None
    raw_name: str | None = None

    model_config = {"from_attributes": True}


class ProducerDetailOut(ProducerOut):
    aliases: list[str] = []
    portfolio: list[ProducerFormulaOut] = []
