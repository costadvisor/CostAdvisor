"""Response shapes for the series health API (Jira 208).

Every row carries the evidence behind its state — which store holds the data,
the newest actual month, its age and the point counts — plus a plain-English
reason, so a reader can act on a state without re-deriving it.
"""
from datetime import date

from pydantic import BaseModel


class SeriesHealthOut(BaseModel):
    commodity_id: int
    name: str
    commodity_key: str | None = None
    state: str                      # current | stale | forecast_only | empty | composite
    reason: str
    store: str | None = None        # monthly | quarterly | both
    last_actual: str | None = None  # "YYYY-MM"
    months_old: int | None = None
    monthly_actual_points: int = 0
    forecast_points: int = 0
    quarterly_points: int = 0


class SeriesHealthListOut(BaseModel):
    as_of: date
    stale_after_months: int
    counts: dict[str, int]          # every state, zero included, before the state filter
    series: list[SeriesHealthOut]
