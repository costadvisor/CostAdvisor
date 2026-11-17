"""API shapes for the news sweep (SCRUM-214): runs, items, feeds."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


class RunStart(BaseModel):
    """Start a run now. With ``family_id``, just that family (even if its
    profile is not active yet); without, every active profile."""

    family_id: int | None = None
    spend_cap_usd: float | None = Field(None, gt=0, le=500)


class RunOut(BaseModel):
    id: uuid.UUID
    period: str
    trigger: str
    family_id: int | None
    family_name: str | None = None
    started_by_name: str | None = None
    status: str
    spend_cap_usd: float
    started_at: datetime
    finished_at: datetime | None
    profiles_planned: int
    profiles_run: int
    searches: int
    feed_entries_read: int
    items_found: int
    items_new: int
    items_duplicate: int
    items_dropped: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    errors: list[dict] = []


class RunDetailOut(RunOut):
    details: list[dict] = []


class ItemOut(BaseModel):
    id: uuid.UUID
    family_id: int
    family_name: str | None = None
    product_line_id: int | None
    product_line_name: str | None = None
    run_id: uuid.UUID | None
    url: str
    headline: str
    source: str | None
    origin: str
    published_date: date | None
    date_source: str | None
    summary: str | None
    search_relevance: float | None
    search_event_type: str | None
    status: str
    found_at: datetime


class FeedIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    url: str = Field(..., min_length=8, max_length=1000)
    kind: str = "rss"
    family_id: int | None = None
    producer_id: uuid.UUID | None = None
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _http(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("https://", "http://")):
            raise ValueError("the feed address must start with https:// or http://")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("rss", "sec"):
            raise ValueError("kind is 'rss' or 'sec'")
        return v


class FeedOut(BaseModel):
    id: int
    name: str
    url: str
    kind: str
    family_id: int | None
    family_name: str | None = None
    producer_id: uuid.UUID | None
    producer_name: str | None = None
    enabled: bool
    last_fetched_at: datetime | None
    last_error: str | None
