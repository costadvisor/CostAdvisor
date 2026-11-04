"""Refresh monitor API (Jira 209).

    GET /api/refresh/routes   how every index gets new values (any signed-in user)
    GET /api/refresh/status   each index's route plus its last refresh (super admin)
    GET /api/refresh/runs     recent refresh runs with every attempt (super admin)
    GET /api/refresh/overview route + last refresh + freshness per index, and the
                              alerts staff must act on (super admin; S5)

Platform grain, like `/api/series-health`. The two reads that return error
text are super-admin: an error can carry a source URL, and team-source
attempts belong to a team.
"""
from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.index_refresh import IndexRefreshAttempt, IndexRefreshRun, IndexRefreshState
from app.models.user import User
from app.routers.admin import require_super_admin
from app.routers.auth import get_current_user
from app.schemas.refresh_monitor import (
    RefreshAlertOut, RefreshAttemptOut, RefreshCheckOut, RefreshOverviewOut,
    RefreshOverviewRowOut, RefreshRouteOut, RefreshRoutesOut, RefreshRunOut,
    RefreshStatusListOut, RefreshStatusOut,
)
from app.services.refresh_overview import build_alerts, overview_counts, refresh_overview
from app.services.refresh_route import refresh_routes, route_counts

router = APIRouter()


@router.get("/routes", response_model=RefreshRoutesOut)
def get_refresh_routes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = refresh_routes(db)
    return RefreshRoutesOut(counts=route_counts(rows),
                            indexes=[RefreshRouteOut(**asdict(r)) for r in rows])


@router.get("/status", response_model=RefreshStatusListOut)
def get_refresh_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """Failing indexes first, then the ones never attempted, then the rest."""
    states = {s.commodity_id: s for s in db.query(IndexRefreshState)}
    out = []
    for r in refresh_routes(db):
        s = states.get(r.commodity_id)
        out.append(RefreshStatusOut(
            **asdict(r),
            last_attempt_at=s.last_attempt_at if s else None,
            last_outcome=s.last_outcome if s else None,
            last_success_at=s.last_success_at if s else None,
            last_error=s.last_error if s else None,
            consecutive_failures=s.consecutive_failures if s else 0,
            latest_period=s.latest_period if s else None,
        ))
    failing = [x for x in out if x.last_outcome not in (None, "ok")]
    # Only scheduled routes are ever attempted by a job; the content drop,
    # uploads and composites are refreshed another way.
    never = [x for x in out if x.last_outcome is None and x.route == "scraper"]
    rest = [x for x in out if x not in failing and x not in never]
    failing.sort(key=lambda x: -x.consecutive_failures)
    return RefreshStatusListOut(failing=len(failing), never_attempted=len(never),
                                indexes=failing + never + rest)


@router.get("/runs", response_model=list[RefreshRunOut])
def get_refresh_runs(
    limit: int = Query(10, ge=1, le=100),
    task: str | None = Query(None, description="Only runs of this job"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """Newest first; within a run, problems first."""
    q = db.query(IndexRefreshRun)
    if task:
        q = q.filter(IndexRefreshRun.task == task)
    runs = q.order_by(IndexRefreshRun.started_at.desc()).limit(limit).all()
    order = {"failed": 0, "rejected": 1, "no_data": 2, "ok": 3}
    out = []
    for run in runs:
        attempts = (db.query(IndexRefreshAttempt)
                    .filter(IndexRefreshAttempt.run_id == run.id).all())
        attempts.sort(key=lambda a: (order.get(a.outcome, 9), a.label))
        out.append(RefreshRunOut(
            id=run.id, task=run.task, status=run.status, started_at=run.started_at,
            finished_at=run.finished_at, attempted=run.attempted, succeeded=run.succeeded,
            no_data=run.no_data, rejected=run.rejected, failed=run.failed, note=run.note,
            attempts=[RefreshAttemptOut(
                commodity_id=a.commodity_id, team_source_id=a.team_source_id, label=a.label,
                route=a.route, outcome=a.outcome, rows_written=a.rows_written,
                latest_period=a.latest_period, tries=a.tries, error=a.error,
                finished_at=a.finished_at,
            ) for a in attempts],
        ))
    return out


@router.get("/overview", response_model=RefreshOverviewOut)
def get_refresh_overview(
    as_of: date | None = Query(None, description="Judge lateness against this date (default today)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """Every index with its route, last refresh and freshness; problems first.
    `alerts` is what the daily check reports — computed live here."""
    as_of = as_of or date.today()
    rows = refresh_overview(db, as_of=as_of)
    alerts = build_alerts(rows)
    check = (db.query(IndexRefreshRun)
             .filter(IndexRefreshRun.task == "refresh_check")
             .order_by(IndexRefreshRun.started_at.desc()).first())
    return RefreshOverviewOut(
        as_of=as_of,
        counts=overview_counts(rows, alerts),
        alerts=[RefreshAlertOut(**asdict(a)) for a in alerts],
        last_check=RefreshCheckOut(at=check.started_at, summary=check.note) if check else None,
        indexes=[RefreshOverviewRowOut(**asdict(r)) for r in rows],
    )
