"""Sheet round-trip mechanism (Scrum 27b) — export a filtered slice, reimport
it as a reviewable diff, apply the diff as a separate explicit action.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.sheet_import_run import SheetImportRun, SheetImportRowDiff
from app.routers.auth import get_current_user
from app.schemas.sheet_roundtrip import SheetImportRunOut, SheetApplyResult
from app.services.audit import log_platform_event
from app.services.permissions import require_platform_permission
from app.services.sheet_roundtrip import get_spec
from app.services.sheet_roundtrip.excel_io import build_export_workbook, read_import_rows
from app.services.sheet_roundtrip.diff import compute_diff

router = APIRouter()


def _resolve_spec(payload_key: str):
    try:
        return get_spec(payload_key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown sheet payload '{payload_key}'")


def _bind_filter(spec, request: Request):
    """Build the payload's OWN filter object from the query string.

    This used to be a hardcoded `subfamily_id`/`needs_review` pair — the fields
    of the first registered payload — passed straight into
    `spec.filter_schema(...)`. The comment here even predicted the failure ("a
    second payload with different filter fields would extend this list"), and
    when `dimension_decision` arrived it did not: Pydantic drops unknown kwargs
    silently, so `DimensionDecisionFilter.kind` was always None and every
    dimension export covered the entire unresolved register regardless of the
    facet the user picked. Silently wrong data, no error.

    Reading the spec's own `model_fields` means a third payload needs no change
    here at all. The cost is that the filter params no longer appear per-route in
    OpenAPI — they vary by `payload_key`, which a single static signature cannot
    express — so the accepted fields are named in each route's docstring instead.
    Unknown query params are ignored (a stray `?foo=1` is not an error); a
    malformed value for a real field is a 422 rather than a silent default.
    """
    fields = spec.filter_schema.model_fields
    supplied = {k: v for k, v in request.query_params.items() if k in fields}
    try:
        return spec.filter_schema(**supplied)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())


@router.get("/{payload_key}/export")
def export_sheet(
    payload_key: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export a filtered slice as a styled workbook.

    Filter params vary by payload and are read from the query string:
    `formula_coverage_price` accepts `subfamily_id`/`needs_review`;
    `dimension_decision` accepts `kind`/`min_occurrences`.
    """
    spec = _resolve_spec(payload_key)
    require_platform_permission(db, current_user, spec.permission_key)
    filter_spec = _bind_filter(spec, request)

    rows = spec.query_rows(db, filter_spec)
    buf = build_export_workbook(spec, rows)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{spec.key}.xlsx"'},
    )


@router.post("/{payload_key}/import", response_model=SheetImportRunOut)
async def import_sheet(
    payload_key: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Computes and persists a diff. Never mutates the underlying payload
    table — applying is a separate call (POST .../import-runs/{id}/apply).

    Filter params vary by payload and are read from the query string; see
    `export_sheet`. The diff is always computed against the same slice the
    filter names, so a reimport is compared with live rows, not a snapshot.
    """
    spec = _resolve_spec(payload_key)
    require_platform_permission(db, current_user, spec.permission_key)
    filter_spec = _bind_filter(spec, request)

    content = await file.read()
    try:
        uploaded_rows = read_import_rows(content, file.filename or "upload", spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    entries = compute_diff(db, spec, filter_spec, uploaded_rows)

    run = SheetImportRun(
        payload_key=payload_key,
        filter_spec=filter_spec.model_dump(),
        status="diffed" if entries else "empty",
        row_count=len(uploaded_rows),
        imported_by=current_user.id,
    )
    db.add(run)
    db.flush()
    for entry in entries:
        db.add(SheetImportRowDiff(run_id=run.id, **entry))
    db.commit()
    db.refresh(run)
    return run


@router.post("/import-runs/{run_id}/apply", response_model=SheetApplyResult)
def apply_sheet_import(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(SheetImportRun).filter(SheetImportRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Import run not found")
    spec = _resolve_spec(run.payload_key)
    require_platform_permission(db, current_user, spec.permission_key)

    applied, skipped_stale = [], []
    for diff in run.diffs:
        if diff.kind != "change" or diff.applied:
            continue
        column_spec = spec.column(diff.column)
        current_str = column_spec.to_string(spec.get_current_value(db, diff.row_key, diff.column))
        if current_str != diff.old_value:
            # Someone else's edit landed on this exact (row, column) since
            # this diff was computed — skip and report it rather than
            # blindly overwriting a value the diff no longer describes.
            skipped_stale.append(diff)
            continue
        parsed = column_spec.parse(diff.new_value) if diff.new_value is not None else None
        spec.apply_change(db, diff.row_key, diff.column, parsed)
        diff.applied = True
        applied.append(diff)

    run.status = "applied"
    run.applied_by = current_user.id
    run.applied_at = datetime.now(timezone.utc)

    # Applying a sheet is gated on a PLATFORM permission (`spec.permission_key`
    # via require_platform_permission), so it is a platform action with no tenant
    # to attribute it to. It used to borrow the actor's first team — misfiling it,
    # and losing the record entirely when that team was deleted or when the actor
    # had no team at all, which is the super-admin case this most needs to cover.
    log_platform_event(
        db, current_user.id, "apply", "sheet_import_run", str(run.id),
        new_value={"payload_key": run.payload_key, "applied": len(applied),
                   "skipped_stale": len(skipped_stale)},
    )
    db.commit()
    db.refresh(run)
    return SheetApplyResult(run=run, applied=applied, skipped_stale=skipped_stale)


@router.get("/import-runs/{run_id}", response_model=SheetImportRunOut)
def get_import_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(SheetImportRun).filter(SheetImportRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Import run not found")
    spec = _resolve_spec(run.payload_key)
    require_platform_permission(db, current_user, spec.permission_key)
    return run


@router.get("/import-runs", response_model=list[SheetImportRunOut])
def list_import_runs(
    payload_key: str = Query(...),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    spec = _resolve_spec(payload_key)
    require_platform_permission(db, current_user, spec.permission_key)
    return (
        db.query(SheetImportRun)
        .filter(SheetImportRun.payload_key == payload_key)
        .order_by(SheetImportRun.created_at.desc())
        .limit(limit)
        .all()
    )
