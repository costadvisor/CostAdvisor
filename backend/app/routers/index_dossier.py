"""Index dossier + volatility calibration API (Wave 3, DB-7).

    GET  /api/dossiers/series/{commodity_id}?region=
    GET  /api/dossiers/series/{commodity_id}/volatility
    GET  /api/dossiers/volatility-calibration
    POST /api/dossiers/volatility-calibration/recompute

Mounted on its own prefix rather than inside `indexes.py`: that router already
owns `/{commodity_id}/...` paths, and a literal segment sitting beside an int
path param is the kind of route collision that only shows up as a 422 later.

Platform reference data, so reads need authentication but no team gate — the
same treatment as the resolution endpoints. The recompute is super-admin: it
changes the percentile scale every consumer reads.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.index_data import CommodityIndex
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.index_dossier import (
    BreakpointOut, CalibrationOut, DossierOut, RecomputeRequest, VolatilityOut,
)
from app.services.audit import log_event
from app.services.index_dossier import (
    active_calibration, dossier_for, recompute_volatility_calibration,
    volatility_percentile,
)

router = APIRouter()

# Platform-level actions have no team, and `audit_logs.team_id` is NOT NULL with
# an FK to teams (the Scrum-10 platform-audit gap) — the same nil-UUID +
# best-effort pattern `indexes.py` already uses for index metadata.
_NO_TEAM = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _calibration_out(calibration) -> CalibrationOut:
    return CalibrationOut(
        id=calibration.id, method=calibration.method, n_rungs=calibration.n_rungs,
        n_series=calibration.n_series, min_points=calibration.min_points,
        is_active=calibration.is_active, step=calibration.step,
        note=calibration.note, computed_at=calibration.computed_at,
        breakpoints=[
            BreakpointOut(rung=b.rung, dispersion=float(b.dispersion))
            for b in sorted(calibration.breakpoints, key=lambda b: b.rung)
        ],
    )


@router.get("/volatility-calibration", response_model=CalibrationOut)
def get_calibration(db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """The active ladder. SCRUM-75 reads this and reports which one it read."""
    calibration = active_calibration(db)
    if calibration is None:
        raise HTTPException(404, "No volatility calibration has been computed yet")
    return _calibration_out(calibration)


@router.post("/volatility-calibration/recompute", response_model=CalibrationOut)
def recompute(data: RecomputeRequest, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    """Fit a fresh ladder over the whole library.

    Super-admin because it changes the percentile scale every consumer reads.
    The previous calibration is deactivated, not deleted — a percentile that
    moved needs the old ladder to explain why.
    """
    if not current_user.is_super_admin:
        raise HTTPException(
            403, "Recomputing the volatility calibration is super-admin only")
    try:
        calibration = recompute_volatility_calibration(
            db, n_rungs=data.n_rungs, min_points=data.min_points, note=data.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    out = _calibration_out(calibration)
    n_rungs, n_series, cid = calibration.n_rungs, calibration.n_series, calibration.id
    db.commit()

    try:
        log_event(db, _NO_TEAM, current_user.id, "recompute",
                  "volatility_calibration", str(cid),
                  new_value={"n_rungs": n_rungs, "n_series": n_series})
        db.commit()
    except Exception:
        db.rollback()
    return out


@router.get("/series/{commodity_id}", response_model=DossierOut)
def get_dossier(commodity_id: int, region: str | None = Query(None),
                db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """The structured dossier for a series, preferring a region-specific row."""
    if not db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first():
        raise HTTPException(404, "Series not found")
    resolved = dossier_for(db, commodity_id, region=region)
    if resolved is None:
        raise HTTPException(404, "No dossier stored for this series")
    return DossierOut(
        commodity_id=resolved.commodity_id,
        commodity_key=resolved.commodity_key,
        region=resolved.region,
        resolved_from=resolved.resolved_from,
        header=resolved.header,
        drivers=resolved.drivers,
        chain=resolved.chain,
        flags=resolved.flags,
        splits=resolved.splits,
        producer_roles=resolved.producer_roles,
        pointers=resolved.pointers,
    )


@router.get("/series/{commodity_id}/volatility", response_model=VolatilityOut)
def get_volatility(commodity_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """A series' dispersion and its percentile on the active ladder.

    Never a bare null: an unmeasurable series says so, because "not measurable"
    and "calm" are different answers and a threshold check cannot tell them
    apart on its own.
    """
    if not db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first():
        raise HTTPException(404, "Series not found")
    return VolatilityOut(**vars(volatility_percentile(db, commodity_id)))
