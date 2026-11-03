"""Series health API (Jira 208).

    GET /api/series-health                  every series, its state and why
    GET /api/series-health/{commodity_id}   one series

One shared answer to "is this index usable" — see `services/series_health.py`
for why four screens needed one.

Platform grain, like `/api/resolution`: no `team_id`, no team gate, every
response is platform reference data. Authentication is still required.

`as_of` exists so a test, or a reviewer reproducing a report, gets the same
ages on any day; it defaults to today.
"""
from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.series_health import SeriesHealthListOut, SeriesHealthOut
from app.services.series_health import (
    STALE_AFTER_MONTHS, STATES, series_health, series_health_bulk, state_counts,
)

router = APIRouter()


@router.get("", response_model=SeriesHealthListOut)
def list_series_health(
    state: str | None = Query(None, description="Only series in this state"),
    as_of: date | None = Query(None, description="Age the data against this date (default today)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every series with its state, the evidence and a reason.

    `counts` covers the whole library before the `state` filter, so the totals
    stay readable while the list is narrowed.
    """
    if state is not None and state not in STATES:
        raise HTTPException(status_code=422, detail=f"Unknown state {state!r}; one of {list(STATES)}")
    as_of = as_of or date.today()
    rows = series_health_bulk(db, as_of=as_of)
    counts = state_counts(rows)
    if state is not None:
        rows = [r for r in rows if r.state == state]
    return SeriesHealthListOut(
        as_of=as_of,
        stale_after_months=STALE_AFTER_MONTHS,
        counts=counts,
        series=[SeriesHealthOut(**asdict(r)) for r in rows],
    )


@router.get("/{commodity_id}", response_model=SeriesHealthOut)
def get_series_health(
    commodity_id: int,
    as_of: date | None = Query(None, description="Age the data against this date (default today)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    health = series_health(db, commodity_id, as_of=as_of)
    if health is None:
        raise HTTPException(status_code=404, detail=f"Unknown series: {commodity_id}")
    return SeriesHealthOut(**asdict(health))
