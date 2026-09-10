import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.product import Product
from app.models.quote import QuoteExtractionLine, QuoteExtractionRun, QuoteRecord, QuoteRecordLine
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.quote import (
    QuoteExtractionRunOut,
    QuoteLineConfirm,
    QuoteRecordLineOut,
    QuoteRecordOut,
)
from app.services.audit import log_event
from app.services.permissions import require_permission
from app.services.quote_extraction import extract_quote

router = APIRouter()


def _get_line_or_404(db: Session, line_id: uuid.UUID) -> QuoteExtractionLine:
    line = db.query(QuoteExtractionLine).filter(QuoteExtractionLine.id == line_id).first()
    if not line:
        raise HTTPException(status_code=404, detail="Quote extraction line not found")
    return line


@router.post("/extract", response_model=QuoteExtractionRunOut, status_code=201)
async def extract(
    team_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Extracts structured, confidence-scored candidate lines from a
    supplier quote/price-list PDF. Persists the draft immediately (so it's
    inspectable) — nothing lands in the permanent quote record until each
    line is separately confirmed."""
    require_permission(db, current_user, team_id, "prices.import")

    content = await file.read()
    filename = file.filename or "upload.pdf"
    try:
        result = extract_quote(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    run = QuoteExtractionRun(
        team_id=team_id, uploaded_by=current_user.id, filename=filename,
        extracted_text=result["extracted_text"],
    )
    db.add(run)
    db.flush()
    for i, fields in enumerate(result["lines"]):
        db.add(QuoteExtractionLine(run_id=run.id, line_index=i, fields=fields))
    db.flush()

    log_event(db, team_id, current_user.id, "quote_extracted", "quote_extraction_run", str(run.id),
              new_value={"filename": filename, "line_count": len(result["lines"])})
    out = QuoteExtractionRunOut.model_validate(run)
    db.commit()
    return out


@router.get("/runs/{run_id}", response_model=QuoteExtractionRunOut)
def get_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(QuoteExtractionRun).filter(QuoteExtractionRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Quote extraction run not found")
    require_permission(db, current_user, run.team_id, "prices.view")
    return run


@router.post("/lines/{line_id}/confirm", response_model=QuoteRecordLineOut, status_code=201)
def confirm_line(
    line_id: uuid.UUID,
    data: QuoteLineConfirm,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Writes into the permanent quote record — the only place that happens.
    Extracted values are used as-is unless a field is explicitly overridden."""
    line = _get_line_or_404(db, line_id)
    run = db.query(QuoteExtractionRun).filter(QuoteExtractionRun.id == line.run_id).first()
    require_permission(db, current_user, run.team_id, "prices.edit")
    if line.status != "pending":
        raise HTTPException(status_code=400, detail=f"Line is already {line.status}")

    if data.resolved_product_id is not None:
        product = db.query(Product).filter(Product.id == data.resolved_product_id).first()
        if not product or product.team_id != run.team_id:
            raise HTTPException(status_code=400, detail="Unknown or inaccessible resolved_product_id")

    def _field(name: str, override):
        if override is not None:
            return override
        entry = line.fields.get(name)
        if not entry:
            return None
        value = entry["value"]
        if name in ("quote_date", "valid_from", "valid_until") and isinstance(value, str):
            return date.fromisoformat(value)
        return value

    record = db.query(QuoteRecord).filter(QuoteRecord.source_run_id == run.id).first()
    if not record:
        record = QuoteRecord(team_id=run.team_id, created_by=current_user.id,
                              source_run_id=run.id, filename=run.filename)
        db.add(record)
        db.flush()

    record_line = QuoteRecordLine(
        quote_record_id=record.id,
        product_reference=_field("product_reference", data.product_reference),
        resolved_product_id=data.resolved_product_id,
        price=_field("price", data.price),
        currency=_field("currency", data.currency),
        unit=_field("unit", data.unit),
        volume_tier=_field("volume_tier", data.volume_tier),
        incoterm=_field("incoterm", data.incoterm),
        named_place=_field("named_place", data.named_place),
        quote_date=_field("quote_date", data.quote_date),
        valid_from=_field("valid_from", data.valid_from),
        valid_until=_field("valid_until", data.valid_until),
        field_confidence=line.fields,
    )
    db.add(record_line)
    db.flush()

    line.status = "confirmed"
    line.quote_record_line_id = record_line.id
    line.reviewed_by = current_user.id
    line.reviewed_at = datetime.now(timezone.utc)

    log_event(db, run.team_id, current_user.id, "quote_line_confirmed", "quote_extraction_line",
              str(line.id), new_value={"quote_record_line_id": str(record_line.id)})
    out = QuoteRecordLineOut.model_validate(record_line)
    db.commit()
    return out


@router.post("/lines/{line_id}/reject")
def reject_line(
    line_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    line = _get_line_or_404(db, line_id)
    run = db.query(QuoteExtractionRun).filter(QuoteExtractionRun.id == line.run_id).first()
    require_permission(db, current_user, run.team_id, "prices.edit")
    if line.status != "pending":
        raise HTTPException(status_code=400, detail=f"Line is already {line.status}")

    line.status = "rejected"
    line.reviewed_by = current_user.id
    line.reviewed_at = datetime.now(timezone.utc)

    log_event(db, run.team_id, current_user.id, "quote_line_rejected", "quote_extraction_line", str(line.id))
    db.commit()
    return {"status": "rejected"}


@router.get("/records", response_model=list[QuoteRecordOut])
def list_records(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "prices.view")
    return (
        db.query(QuoteRecord)
        .filter(QuoteRecord.team_id == team_id)
        .order_by(QuoteRecord.created_at.desc())
        .all()
    )
