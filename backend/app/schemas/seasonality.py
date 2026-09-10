"""Seasonal-factor API contract (Wave 3, SCRUM-69).

The note ships **with** the factors it describes, rendered on read. Two separate
endpoints — one for the numbers, one for the prose — would put the drop's
original failure mode back: a text and a number that can disagree with nothing
able to say which is right.
"""
from datetime import datetime

from pydantic import BaseModel


class SeasonProfileOut(BaseModel):
    commodity_id: int
    region: str | None = None
    # Twelve factors, January first, averaging 100 by construction.
    factors: list[float]
    # Recorded on every row so two differently-computed sets are never silently
    # compared.
    method: str
    # How many monthly observations the fit actually used — not a constant. The
    # drop's prose asserts 42 for all 78 of its series, including the 30 that
    # have no monthly actuals at all.
    window_months: int
    computed_at: datetime | None = None
    # Everything below is derived from `factors` on read, never stored.
    peak_month: int
    trough_month: int
    spread: float
    tier: str                # low | modest | meaningful
    note: str


class SeriesRecomputeOut(BaseModel):
    commodity_id: int
    # computed | unchanged | insufficient. `unchanged` is what makes the
    # recompute safe to schedule.
    status: str
    window_months: int = 0
    # Populated on `insufficient`, so a caller is told why rather than getting
    # an empty profile.
    reason: str | None = None


class RecomputeReportOut(BaseModel):
    series_considered: int
    computed: int
    unchanged: int
    # Reported, never filled with a flat 100.
    insufficient: int
