import io
import csv
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.cost_model import CostModel
from app.models.actual_volume import ActualVolume
from app.routers.auth import get_current_user
from app.schemas.actual_volume import ActualVolumeOut, ActualVolumeCreate
from app.services.file_parser import parse_volume_upload
from app.services.unit_converter import convert_unit
from app.services.audit import log_event
from app.services.permissions import require_permission

router = APIRouter()


def _product_unit(cm: CostModel) -> str:
    return (cm.product.unit if cm.product else None) or "kg"


def _in_product_unit(cm: CostModel, volume: float, unit: str | None) -> tuple[float, str]:
    """The quantity in the unit the cost model is priced in.

    The engine (Squeeze, Brief) and the portfolio exposure read the volume number
    without its unit, so every row is stored in the product's unit: 600,000 kg on
    a EUR/t product is stored as 600 t, not read as 600,000 t. No unit means the
    product's unit. Raises ValueError for a unit that cannot be converted.
    """
    target = _product_unit(cm)
    if not unit or unit.strip().lower() == target.lower():
        return volume, target
    try:
        return convert_unit(volume, unit.strip(), target), target
    except ValueError:
        raise ValueError(f"Can't convert {unit} to {target}, the unit this product is priced in.")


@router.get("/{cost_model_id}", response_model=list[ActualVolumeOut])
def get_volumes(
    cost_model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "volumes.view")
    return (
        db.query(ActualVolume)
        .filter(ActualVolume.cost_model_id == cost_model_id)
        .order_by(ActualVolume.year, ActualVolume.quarter)
        .all()
    )


@router.get("/{cost_model_id}/template")
def download_volume_template(
    cost_model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a CSV template pre-populated with existing volume rows (or last 4 quarters blank)."""
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "volumes.view")

    existing = (
        db.query(ActualVolume)
        .filter(ActualVolume.cost_model_id == cost_model_id)
        .order_by(ActualVolume.year, ActualVolume.quarter)
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["period", "volume", "unit"])

    unit = _product_unit(cm)
    if existing:
        for v in existing:
            writer.writerow([f"Q{v.quarter}-{v.year}", v.volume, v.unit or unit])
    else:
        now = datetime.now(timezone.utc)
        y, q = now.year, (now.month - 1) // 3 + 1
        quarters = []
        for _ in range(4):
            quarters.append((y, q))
            q -= 1
            if q == 0:
                q = 4
                y -= 1
        for yr, qt in reversed(quarters):
            writer.writerow([f"Q{qt}-{yr}", "", unit])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=volumes_template.csv"},
    )


@router.post("/{cost_model_id}/upload")
async def upload_volumes(
    cost_model_id: uuid.UUID,
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "volumes.import")

    content = await file.read()
    filename = file.filename or "upload"

    try:
        result = parse_volume_upload(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    parse_errors = result["errors"]
    rows = []
    for row in result["rows"]:
        try:
            volume, unit = _in_product_unit(cm, row["volume"], row.get("unit"))
        except ValueError as exc:
            parse_errors.append({"message": f"Q{row['quarter']}-{row['year']}: {exc}"})
            continue
        rows.append({**row, "volume": volume, "unit": unit})

    if dry_run:
        return {"rows_processed": len(rows), "errors": parse_errors, "dry_run": True, "filename": filename}

    count = 0
    for row in rows:
        existing = db.query(ActualVolume).filter(
            ActualVolume.cost_model_id == cost_model_id,
            ActualVolume.year == row["year"],
            ActualVolume.quarter == row["quarter"],
        ).first()

        if existing:
            existing.volume = row["volume"]
            existing.unit = row["unit"]
            existing.uploaded_by = current_user.id
            existing.source_file = filename
        else:
            av = ActualVolume(
                cost_model_id=cost_model_id,
                uploaded_by=current_user.id,
                year=row["year"],
                quarter=row["quarter"],
                volume=row["volume"],
                unit=row["unit"],
                source_file=filename,
            )
            db.add(av)
        count += 1

    db.commit()
    log_event(db, cm.team_id, current_user.id, "create", "actual_volume", str(cost_model_id),
              new_value={"rows_processed": count, "filename": filename})
    db.commit()
    return {"status": "uploaded", "rows_processed": count, "errors": parse_errors, "filename": filename}


@router.put("/{cost_model_id}/{year}/{quarter}", response_model=ActualVolumeOut)
def update_volume(
    cost_model_id: uuid.UUID,
    year: int,
    quarter: int,
    data: ActualVolumeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "volumes.edit")

    existing = db.query(ActualVolume).filter(
        ActualVolume.cost_model_id == cost_model_id,
        ActualVolume.year == year,
        ActualVolume.quarter == quarter,
    ).first()

    try:
        volume, unit = _in_product_unit(cm, data.volume, data.unit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    previous = {"volume": float(existing.volume), "unit": existing.unit} if existing else None
    if existing:
        existing.volume = volume
        existing.unit = unit
        existing.uploaded_by = current_user.id
    else:
        existing = ActualVolume(
            cost_model_id=cost_model_id,
            uploaded_by=current_user.id,
            year=data.year,
            quarter=data.quarter,
            volume=volume,
            unit=unit,
        )
        db.add(existing)

    log_event(db, cm.team_id, current_user.id, "update", "actual_volume", str(cost_model_id),
              previous_value={"year": year, "quarter": quarter, **previous} if previous else None,
              new_value={"year": year, "quarter": quarter, "volume": float(volume), "unit": unit})
    db.flush()
    db.expunge(existing)
    db.commit()
    return existing


@router.delete("/{cost_model_id}/{year}/{quarter}")
def delete_volume(
    cost_model_id: uuid.UUID,
    year: int,
    quarter: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "volumes.delete")

    vol = db.query(ActualVolume).filter(
        ActualVolume.cost_model_id == cost_model_id,
        ActualVolume.year == year,
        ActualVolume.quarter == quarter,
    ).first()

    if not vol:
        raise HTTPException(status_code=404, detail="Volume not found")

    log_event(db, cm.team_id, current_user.id, "delete", "actual_volume", str(cost_model_id),
              previous_value={"year": year, "quarter": quarter, "volume": float(vol.volume), "unit": vol.unit})
    db.delete(vol)
    db.commit()
    return {"status": "deleted"}
