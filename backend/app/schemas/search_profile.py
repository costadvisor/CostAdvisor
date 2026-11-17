"""News search profile schemas (Wave 4, SCRUM-213 / NEWS-1).

ProfileFields  - the editable settings, shared by a family profile and a per-line override.
EffectiveProfile - what every later stage (test run, sweep, classifier) reads:
                   the family profile merged with a product line's override.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Literal, get_args

from pydantic import BaseModel, Field, field_validator

EventType = Literal[
    "new_plant",
    "capacity_change",
    "new_supplier",
    "technology",
    "closure",
    "regulation",
    "m_and_a",
]
EVENT_TYPES: tuple[str, ...] = get_args(EventType)

_CAS_RE = re.compile(r"^(\d{2,7})-(\d{2})-(\d)$")


def clean_text_list(values: list[str]) -> list[str]:
    """Collapse whitespace, drop blanks, dedupe case-insensitively, keep order."""
    out, seen = [], set()
    for v in values:
        v = " ".join(str(v).split())
        if v and v.casefold() not in seen:
            seen.add(v.casefold())
            out.append(v)
    return out


def clean_domain(value: str) -> str:
    """'https://www.CompositesWorld.com/news' -> 'compositesworld.com'"""
    v = value.strip().lower()
    v = re.sub(r"^[a-z]+://", "", v)
    v = v.split("/")[0].split("?")[0]
    return v.removeprefix("www.")


def is_valid_cas(value: str) -> bool:
    """CAS numbers carry a check digit, so typos are catchable."""
    m = _CAS_RE.match(value)
    if not m:
        return False
    digits = (m.group(1) + m.group(2))[::-1]
    return sum((i + 1) * int(d) for i, d in enumerate(digits)) % 10 == int(m.group(3))


class ProfileFields(BaseModel):
    """Editable settings. On a family profile these are the base; on an override,
    empty lists / None mean 'nothing extra, inherit from the family'."""

    terms: list[str] = []
    synonyms: list[str] = []
    cas_numbers: list[str] = []
    exclude_terms: list[str] = []
    event_types: list[EventType] = []  # empty on the family profile = keep all types
    preferred_sites: list[str] = []
    blocked_sites: list[str] = []
    producer_ids: list[uuid.UUID] = []
    region_codes: list[str] = []  # regions.code, e.g. "Europe", "NWE"
    language: str | None = None
    min_relevance: float | None = Field(None, ge=0, le=1)
    lookback_days: int | None = Field(None, gt=0, le=366)

    @field_validator("terms", "synonyms", "exclude_terms")
    @classmethod
    def _clean_text(cls, v: list[str]) -> list[str]:
        return clean_text_list(v)

    @field_validator("cas_numbers")
    @classmethod
    def _check_cas(cls, v: list[str]) -> list[str]:
        v = clean_text_list(v)
        bad = [c for c in v if not is_valid_cas(c)]
        if bad:
            raise ValueError(f"invalid CAS number(s): {', '.join(bad)}")
        return v

    @field_validator("preferred_sites", "blocked_sites")
    @classmethod
    def _clean_sites(cls, v: list[str]) -> list[str]:
        return clean_text_list([clean_domain(s) for s in v])

    @field_validator("event_types", "producer_ids", "region_codes")
    @classmethod
    def _dedupe(cls, v: list) -> list:
        return list(dict.fromkeys(v))

    @field_validator("language")
    @classmethod
    def _clean_language(cls, v: str | None) -> str | None:
        return v.strip().lower() or None if v else None

    @classmethod
    def from_row(cls, row) -> "ProfileFields":
        """Build from a SearchProfile ORM row."""
        return cls(
            terms=row.terms or [],
            synonyms=row.synonyms or [],
            cas_numbers=row.cas_numbers or [],
            exclude_terms=row.exclude_terms or [],
            event_types=row.event_types or [],
            preferred_sites=row.preferred_sites or [],
            blocked_sites=row.blocked_sites or [],
            producer_ids=[p.id for p in row.producers],
            region_codes=[r.code for r in row.regions],
            language=row.language,
            min_relevance=row.min_relevance,
            lookback_days=row.lookback_days,
        )


class EffectiveProfile(BaseModel):
    """A fully resolved profile: no Nones, overrides applied, defaults filled in."""

    family_id: int
    product_line_id: int | None
    is_active: bool
    provenance: str
    owner_editor_id: uuid.UUID | None

    terms: list[str]
    synonyms: list[str]
    cas_numbers: list[str]
    exclude_terms: list[str]
    event_types: list[EventType]
    preferred_sites: list[str]
    blocked_sites: list[str]
    producer_ids: list[uuid.UUID]
    region_codes: list[str]
    language: str
    min_relevance: float
    lookback_days: int

    @property
    def search_terms(self) -> list[str]:
        """Everything that identifies the product: terms, synonyms and CAS numbers."""
        return clean_text_list(self.terms + self.synonyms + self.cas_numbers)


# ── API shapes (SCRUM-213 sub-tasks 2-4) ─────────────────────────────────────

class ProducerRef(BaseModel):
    id: uuid.UUID
    name: str


class RegionRef(BaseModel):
    code: str
    name: str


class ProfileWrite(ProfileFields):
    """What the portal editor sends. is_active/owner only apply to a family profile."""

    is_active: bool | None = None
    owner_editor_id: uuid.UUID | None = None
    notes: str | None = Field(None, max_length=4000)


class ProfileOut(ProfileFields):
    id: uuid.UUID
    family_id: int
    product_line_id: int | None
    is_active: bool
    provenance: str
    owner_editor_id: uuid.UUID | None
    owner_editor_name: str | None = None
    notes: str | None
    producers: list[ProducerRef] = []
    regions: list[RegionRef] = []
    updated_at: datetime | None


class ProductLineRef(BaseModel):
    id: int
    name: str
    subfamily_name: str | None = None
    has_override: bool


class EditorRef(BaseModel):
    id: uuid.UUID
    name: str


class FamilyProfileSummary(BaseModel):
    family_id: int
    family_code: str | None
    family_name: str
    has_profile: bool
    provenance: str | None = None
    is_active: bool = False
    owner_editor_name: str | None = None
    override_count: int = 0
    updated_at: datetime | None = None
    # Plain-language problems an editor should fix, e.g. "No producers to watch".
    warnings: list[str] = []


class ProfileDetailOut(BaseModel):
    family_id: int
    family_code: str | None
    family_name: str
    product_lines: list[ProductLineRef]
    profile: ProfileOut | None
    overrides: list[ProfileOut]


class DraftOut(BaseModel):
    fields: ProfileFields
    producers: list[ProducerRef]
    regions: list[RegionRef]
    rationale: str
    dropped: list[str]
    cost_usd: float


class TestRunRequest(BaseModel):
    """Test a profile. With ``fields``, the unsaved form is tested instead of
    the saved profile (as the family profile, or as the override of
    ``product_line_id``). Nothing is saved either way."""

    product_line_id: int | None = None
    fields: ProfileFields | None = None
    max_searches: int | None = Field(None, ge=1, le=10)


class NewsItemOut(BaseModel):
    headline: str
    url: str
    source: str
    summary: str
    event_type: str
    relevance: float
    published_date: date | None
    # Where the date came from: "reported" (Claude read it from the article),
    # "search_result" (the search engine's page age) or None (unknown).
    date_source: str | None = None
    drop_reason: str | None = None


class TestRunOut(BaseModel):
    family_id: int
    product_line_id: int | None
    window_start: date | None
    queries: list[str]
    kept: list[NewsItemOut]
    below_threshold: list[NewsItemOut]
    dropped: list[NewsItemOut]
    searches: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None
    saved: bool = False
    pages_seen: int = 0              # distinct pages the searches returned
    claude_note: str | None = None   # Claude's note on what it found (or why so little)
