"""Response shapes for the live-number picker (Jira 210 S7)."""
from datetime import date

from pydantic import BaseModel, Field


class LiveSeriesOut(BaseModel):
    key: str                        # what goes in {{index:KEY|...}}
    name: str
    frequency: str | None = None
    state: str                      # series health: current | stale | empty | ...
    last_actual: str | None = None  # "YYYY-MM"


class LiveRecipeLineOut(BaseModel):
    region: str | None = None
    variant: str = ""
    name: str
    weight_pct: float


class LivePreviewIn(BaseModel):
    token: str = Field(..., max_length=500)
    doc_as_of: date | None = None   # the text's date, for a fallback's "as of"


class LivePreviewOut(BaseModel):
    token: str
    valid: bool
    error: str | None = None
    state: str                      # current | late | forecast | missing
    display: str
    derived_from: dict | None = None
