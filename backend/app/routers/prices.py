import csv
import io
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.cost_model import CostModel
from app.models.price_data import ActualPrice
from app.routers.auth import get_current_user
from app.schemas.price_data import ActualPriceOut, ActualPriceCreate
from app.services.file_parser import parse_price_upload
from app.services.audit import log_event
from app.services.permissions import require_permission

router = APIRouter()


@router.get("/{cost_model_id}", response_model=list[ActualPriceOut])
def get_actual_prices(
    cost_model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "prices.view")
    prices = (
        db.query(ActualPrice)
        .filter(ActualPrice.cost_model_id == cost_model_id)
        .order_by(ActualPrice.year, ActualPrice.quarter)
        .all()
    )
    return [ActualPriceOut.model_validate(p) for p in prices]


@router.get("/{cost_model_id}/template")
def download_price_template(
    cost_model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a CSV template pre-populated with existing price rows (or last 4 quarters blank).

    Mirrors the volumes template endpoint. The frontend previously served this one
    from an inline data: URI, so the template never reflected the model's own
    incoterm or the periods already on record.
    """
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "prices.view")

    existing = (
        db.query(ActualPrice)
        .filter(ActualPrice.cost_model_id == cost_model_id)
        .order_by(ActualPrice.year, ActualPrice.quarter)
        .all()
    )

    default_incoterm = cm.incoterm or ""

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["period", "price", "incoterm"])
    if existing:
        for p in existing:
            writer.writerow([f"Q{p.quarter}-{p.year}", p.price, p.incoterm or default_incoterm])
    else:
        from datetime import date

        today = date.today()
        y, q = today.year, (today.month - 1) // 3 + 1
        quarters = []
        for _ in range(4):
            quarters.append((y, q))
            q -= 1
            if q == 0:
                q = 4
                y -= 1
        for yr, qt in reversed(quarters):
            writer.writerow([f"Q{qt}-{yr}", "", default_incoterm])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=prices_template.csv"},
    )


@router.post("/{cost_model_id}/upload")
async def upload_prices(
    cost_model_id: uuid.UUID,
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "prices.import")

    content = await file.read()
    filename = file.filename or "upload"

    try:
        result = parse_price_upload(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    rows = result["rows"]
    parse_errors = result["errors"]

    if dry_run:
        return {"rows_processed": len(rows), "errors": parse_errors, "dry_run": True, "filename": filename}

    count = 0
    for row in rows:
        existing = db.query(ActualPrice).filter(
            ActualPrice.cost_model_id == cost_model_id,
            ActualPrice.year == row["year"],
            ActualPrice.quarter == row["quarter"],
        ).first()

        # If the upload doesn't carry an incoterm, fall back to the cost model's default.
        row_incoterm = row.get("incoterm") or cm.incoterm
        row_named_place = row.get("named_place")

        if existing:
            existing.price = row["price"]
            existing.uploaded_by = current_user.id
            existing.source_file = filename
            existing.incoterm = row_incoterm
            if row_named_place is not None:
                existing.named_place = row_named_place
        else:
            ap = ActualPrice(
                cost_model_id=cost_model_id,
                uploaded_by=current_user.id,
                year=row["year"],
                quarter=row["quarter"],
                price=row["price"],
                incoterm=row_incoterm,
                named_place=row_named_place,
                source_file=filename,
            )
            db.add(ap)
        count += 1

    db.commit()
    log_event(db, cm.team_id, current_user.id, "create", "price_data", str(cost_model_id),
              new_value={"rows_processed": count, "filename": filename})
    db.commit()
    return {
        "status": "uploaded",
        "rows_processed": count,
        "filename": filename,
        "errors": parse_errors,
    }


@router.put("/{cost_model_id}/{year}/{quarter}", response_model=ActualPriceOut)
def update_price(
    cost_model_id: uuid.UUID,
    year: int,
    quarter: int,
    data: ActualPriceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "prices.edit")

    existing = db.query(ActualPrice).filter(
        ActualPrice.cost_model_id == cost_model_id,
        ActualPrice.year == year,
        ActualPrice.quarter == quarter,
    ).first()

    previous = float(existing.price) if existing else None
    incoterm_value = data.incoterm or cm.incoterm
    if existing:
        existing.price = data.price
        existing.uploaded_by = current_user.id
        if data.incoterm is not None:
            existing.incoterm = data.incoterm
        if data.named_place is not None:
            existing.named_place = data.named_place
        if data.landed_cost_adjustments is not None:
            existing.landed_cost_adjustments = data.landed_cost_adjustments
    else:
        existing = ActualPrice(
            cost_model_id=cost_model_id,
            uploaded_by=current_user.id,
            year=data.year,
            quarter=data.quarter,
            price=data.price,
            incoterm=incoterm_value,
            named_place=data.named_place,
            landed_cost_adjustments=data.landed_cost_adjustments,
        )
        db.add(existing)

    log_event(db, cm.team_id, current_user.id, "update", "price_data", str(cost_model_id),
              previous_value={"year": year, "quarter": quarter, "price": previous} if previous is not None else None,
              new_value={"year": year, "quarter": quarter, "price": float(data.price)})
    db.flush()
    result = ActualPriceOut.model_validate(existing)
    db.commit()
    return result


@router.delete("/{cost_model_id}/{year}/{quarter}")
def delete_price(
    cost_model_id: uuid.UUID,
    year: int,
    quarter: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "prices.delete")

    price = db.query(ActualPrice).filter(
        ActualPrice.cost_model_id == cost_model_id,
        ActualPrice.year == year,
        ActualPrice.quarter == quarter,
    ).first()

    if not price:
        raise HTTPException(status_code=404, detail="Price not found")

    log_event(db, cm.team_id, current_user.id, "delete", "price_data", str(cost_model_id),
              previous_value={"year": year, "quarter": quarter, "price": float(price.price)})
    db.delete(price)
    db.commit()
    return {"status": "deleted"}
