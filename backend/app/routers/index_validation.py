"""Index data-quality validation API (Wave 3, SCRUM-34).

    POST /api/validation/runs        run every check, store the findings
    GET  /api/validation/runs        the run history
    GET  /api/validation/runs/{id}   one run, with its per-check counts
    GET  /api/validation/findings    the findings, filterable
    GET  /api/validation/preview     run the checks without storing anything

**Platform-grain, deliberately** — the same reasoning as `/api/resolution`:
these are facts about the shared index library, not about anyone's tenancy, so
there is no `team_id` parameter and no team gate. Authentication is still
required, matching the other platform index reads.

Running is **super-admin**, reading is any authenticated user. A run writes
findings that carry a customer-visible judgement about the library's data, and
it is the same gate `POST /volatility-calibration/recompute` takes for the same
reason. `GET /preview` exists so a non-destructive look does not need that gate.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.index_validation import IndexValidationFinding, IndexValidationRun
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.index_validation import (
    FindingOut, FindingsResponse, RunOut, RunRequest, RunsResponse,
)
from app.services.audit import log_platform_event
from app.services.index_validation import collect, run_validation

router = APIRouter()


@router.post("/runs", response_model=RunOut, status_code=201)
def create_run(
    payload: RunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin required")
    report = run_validation(db, include_declared=payload.include_declared,
                            note=payload.note)
    run = db.query(IndexValidationRun).filter(
        IndexValidationRun.id == report.run_id).first()
    # Built before the commit: the transaction-local RLS GUCs reset on commit,
    # and this router follows the same ordering as the rest of the codebase.
    out = RunOut.model_validate(run)
    log_platform_event(
        db, current_user.id, "index_validation_run", "index_validation",
        str(report.run_id),
        new_value={"n_findings": run.n_findings, "n_new": report.n_new,
                   "n_resolved": report.n_resolved})
    db.commit()
    return out


@router.get("/runs", response_model=RunsResponse)
def list_runs(
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    runs = (db.query(IndexValidationRun)
            .order_by(IndexValidationRun.started_at.desc())
            .limit(limit).all())
    return RunsResponse(runs=[RunOut.model_validate(r) for r in runs])


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(IndexValidationRun).filter(
        IndexValidationRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunOut.model_validate(run)


@router.get("/findings", response_model=FindingsResponse)
def list_findings(
    check_code: str | None = None,
    origin: str | None = Query(None, pattern="^(derived|declared)$"),
    severity: str | None = Query(None, pattern="^(contradiction|gap|note)$"),
    subject_table: str | None = None,
    subject_key: str | None = None,
    # Default to open findings: a resolved one is history, and a queue that
    # shows every finding ever made is a queue nobody reads.
    open_only: bool = True,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(IndexValidationFinding)
    if check_code:
        q = q.filter(IndexValidationFinding.check_code == check_code)
    if origin:
        q = q.filter(IndexValidationFinding.origin == origin)
    if severity:
        q = q.filter(IndexValidationFinding.severity == severity)
    if subject_table:
        q = q.filter(IndexValidationFinding.subject_table == subject_table)
    if subject_key:
        q = q.filter(IndexValidationFinding.subject_key == subject_key)
    if open_only:
        q = q.filter(IndexValidationFinding.resolved_at.is_(None))
    total = q.count()
    rows = (q.order_by(IndexValidationFinding.severity,
                       IndexValidationFinding.check_code,
                       IndexValidationFinding.subject_key)
            .offset(offset).limit(limit).all())
    return FindingsResponse(
        total=total, findings=[FindingOut.model_validate(r) for r in rows])


@router.get("/preview")
def preview(
    include_declared: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """What a run would find, without storing anything.

    Shares `collect` with the real run rather than reimplementing the checks,
    so the preview cannot drift from what `POST /runs` would actually record.
    """
    report = collect(db, include_declared=include_declared)
    return {
        "checks": report.checks,
        "n_findings": len(report.findings),
        "findings": [
            {"origin": f.origin, "check_code": f.check_code,
             "severity": f.severity, "subject_table": f.subject_table,
             "subject_key": f.subject_key, "subject_column": f.subject_column,
             "left_label": f.left_label, "left_value": f.left_value,
             "right_label": f.right_label, "right_value": f.right_value,
             "summary": f.summary, "detail": f.detail}
            for f in report.findings
        ],
    }
