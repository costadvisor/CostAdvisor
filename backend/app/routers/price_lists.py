"""Supplier price-list import (Scrum 30).

    POST /api/price-lists/extract            parse a PDF -> a reviewable draft
    GET  /api/price-lists/runs               recent runs for a team
    GET  /api/price-lists/runs/{id}          one run with its rows
    POST /api/price-lists/runs/{id}/commit   named rows -> ActualPrice
    POST /api/price-lists/rows/{id}/skip     take a row out of the queue

Permissions reuse `prices.import` / `prices.edit`: this is pricing data, and a
`price_lists.*` category would need a whole permissions/plan/role migration for
a distinction nothing needs.

Commit is a separate call from extract on purpose. Extract never touches
actual_prices — a parse is a proposal, and the review step between them is the
only thing standing between a misread product name and a silently wrong gap.
"""
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.price_list import PriceListRow, PriceListRun
from app.models.supplier import Supplier
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.price_list import (
    CommitRequest, CommitResponse, PriceListRunOut, PriceListRunSummary,
    RowCommitResult,
)
from app.services.audit import log_event
from app.services.permissions import require_permission
from app.services.price_list import (
    CommitError, build_rows, commit_row, refresh_run_status,
)
from app.services.quote_extraction import extract_quote

router = APIRouter()


def _get_run_or_404(db: Session, run_id: uuid.UUID) -> PriceListRun:
    run = (
        db.query(PriceListRun)
        .options(joinedload(PriceListRun.rows))
        .filter(PriceListRun.id == run_id)
        .first()
    )
    # RLS already hides another team's run, so "not visible" and "not there"
    # are the same 404 — deliberately not distinguishable from outside.
    if not run:
        raise HTTPException(status_code=404, detail="Price list run not found")
    return run


@router.post("/extract", response_model=PriceListRunOut, status_code=201)
async def extract(
    team_id: uuid.UUID = Query(...),
    supplier_id: int | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Parse a supplier price list and propose, per row, which cost model and
    which period it belongs to. Writes a draft only."""
    require_permission(db, current_user, team_id, "prices.import")

    if supplier_id is not None:
        supplier = (
            db.query(Supplier)
            .filter(Supplier.id == supplier_id, Supplier.team_id == team_id)
            .first()
        )
        if supplier is None:
            raise HTTPException(status_code=400, detail="Supplier not found for this team")

    content = await file.read()
    filename = file.filename or "price-list.pdf"
    try:
        result = extract_quote(content, filename)
    except ValueError as exc:
        # Structural failure only (the document could not be opened). The
        # caller is expected to fall back to manual entry, so this is a 400
        # with the reason, not a 500.
        raise HTTPException(status_code=400, detail=str(exc))

    run = PriceListRun(
        team_id=team_id,
        uploaded_by=current_user.id,
        supplier_id=supplier_id,
        filename=filename,
        extracted_text=result["extracted_text"],
    )
    db.add(run)
    db.flush()
    for row in build_rows(db, run, result["lines"]):
        db.add(row)
    db.flush()
    db.refresh(run)

    log_event(
        db, team_id, current_user.id, "price_list_extracted", "price_list_run", str(run.id),
        new_value={"filename": filename, "rows": len(result["lines"]),
                   "matched": sum(1 for r in run.rows if r.matched_cost_model_id)},
    )
    out = PriceListRunOut.model_validate(run)
    db.commit()
    return out


@router.get("/runs", response_model=list[PriceListRunSummary])
def list_runs(
    team_id: uuid.UUID = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "prices.view")
    runs = (
        db.query(PriceListRun)
        .options(joinedload(PriceListRun.rows))
        .filter(PriceListRun.team_id == team_id)
        .order_by(PriceListRun.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        PriceListRunSummary(
            id=r.id, supplier_id=r.supplier_id, filename=r.filename, status=r.status,
            created_at=r.created_at, row_count=len(r.rows),
            committed_count=sum(1 for row in r.rows if row.status == "committed"),
        )
        for r in runs
    ]


@router.get("/runs/{run_id}", response_model=PriceListRunOut)
def get_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = _get_run_or_404(db, run_id)
    require_permission(db, current_user, run.team_id, "prices.view")
    return PriceListRunOut.model_validate(run)


@router.post("/runs/{run_id}/commit", response_model=CommitResponse)
def commit(
    run_id: uuid.UUID,
    payload: CommitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Write the named rows as actual prices.

    Per-row outcomes, never all-or-nothing: one row missing a period must not
    stop the eight beside it that are ready, and the reviewer needs to see
    which one failed and why rather than a single rejected request.
    """
    run = _get_run_or_404(db, run_id)
    require_permission(db, current_user, run.team_id, "prices.edit")

    by_id = {r.id: r for r in run.rows}
    results: list[RowCommitResult] = []
    committed = 0
    for item in payload.rows:
        row = by_id.get(item.row_id)
        if row is None:
            results.append(RowCommitResult(row_id=item.row_id, committed=False,
                                           error="Row does not belong to this run"))
            continue
        try:
            ap = commit_row(
                db, row, run, current_user.id,
                cost_model_id=item.cost_model_id, year=item.year,
                quarter=item.quarter, price=item.price,
            )
        except CommitError as exc:
            results.append(RowCommitResult(row_id=item.row_id, committed=False, error=str(exc)))
            continue
        committed += 1
        results.append(RowCommitResult(row_id=item.row_id, committed=True, actual_price_id=ap.id))

    refresh_run_status(run)
    if committed:
        log_event(
            db, run.team_id, current_user.id, "price_list_committed", "price_list_run", str(run.id),
            new_value={"committed": committed, "failed": len(results) - committed},
        )
    out = CommitResponse(committed=committed, failed=len(results) - committed, results=results)
    db.commit()
    return out


@router.post("/rows/{row_id}/skip", response_model=PriceListRunOut)
def skip_row(
    row_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(PriceListRow).filter(PriceListRow.id == row_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Row not found")
    run = _get_run_or_404(db, row.run_id)
    require_permission(db, current_user, run.team_id, "prices.edit")
    if row.status == "committed":
        raise HTTPException(status_code=400, detail="Row is already committed")
    row.status = "skipped"
    row.reviewed_by = current_user.id
    refresh_run_status(run)
    db.flush()
    db.refresh(run)
    out = PriceListRunOut.model_validate(run)
    db.commit()
    return out
