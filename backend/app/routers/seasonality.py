"""Seasonal-factor API (Wave 3, SCRUM-69).

    GET  /api/seasonality/series/{commodity_id}?region=
    POST /api/seasonality/series/{commodity_id}/recompute
    POST /api/seasonality/recompute

The response carries the twelve factors **and the note rendered from them**, so
a consumer cannot end up displaying prose that disagrees with the numbers beside
it — which is exactly what importing the drop's `INDEX_SEASON_NOTES.json` would
have allowed.

Platform reference data: reads need authentication, no team gate. Recompute is
super-admin, the same reasoning as the volatility calibration — it changes a
number every consumer reads.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.index_data import CommodityIndex
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.seasonality import (
    RecomputeReportOut, SeasonProfileOut, SeriesRecomputeOut,
)
from app.services.index_seasonality import (
    MIN_MONTHS, profile_for, recompute_all, recompute_series,
)

router = APIRouter()


def _require_series(db: Session, commodity_id: int) -> None:
    if not db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first():
        raise HTTPException(404, "Series not found")


@router.get("/series/{commodity_id}", response_model=SeasonProfileOut)
def get_profile(commodity_id: int, region: str | None = Query(None),
                db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """The twelve factors for a series, with the note rendered from them.

    A series that cannot support a fit gets a 404 with the reason rather than a
    flat 100 for every month — "no seasonality" and "not enough history to tell"
    are different answers, and a flat profile would present the second as the
    first.
    """
    _require_series(db, commodity_id)
    profile = profile_for(db, commodity_id, region=region)
    if profile is None:
        raise HTTPException(
            404,
            f"No seasonal profile for this series — a fit needs at least "
            f"{MIN_MONTHS} monthly actuals with every calendar month represented",
        )
    return SeasonProfileOut(**vars(profile))


@router.post("/series/{commodity_id}/recompute", response_model=SeriesRecomputeOut)
def recompute_one(commodity_id: int, region: str | None = Query(None),
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    if not current_user.is_super_admin:
        raise HTTPException(403, "Recomputing seasonal factors is super-admin only")
    _require_series(db, commodity_id)
    result = recompute_series(db, commodity_id, region=region)
    db.commit()
    return SeriesRecomputeOut(**vars(result))


@router.post("/recompute", response_model=RecomputeReportOut)
def recompute_everything(db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    """Recompute every series that has monthly actuals.

    Also runs weekly after the scrapes (`app.tasks.seasonality`), because the
    ticket's requirement is factors recomputed *when the series updates* — and
    the scrapers are what update it.
    """
    if not current_user.is_super_admin:
        raise HTTPException(403, "Recomputing seasonal factors is super-admin only")
    report = recompute_all(db)
    db.commit()
    return RecomputeReportOut(
        computed=report.computed, unchanged=report.unchanged,
        insufficient=report.insufficient,
        series_considered=len(report.results),
    )
