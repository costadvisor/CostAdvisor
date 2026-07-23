"""Region reference-data CRUD (Scrum 56).

Regions are global platform reference data (no team_id), like commodity indexes:
any authenticated user can read them (for dropdowns / resolution); only a
super-admin can mutate. Subregions are created by POSTing with a parent_id — no
migration needed.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.region import Region
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.region import RegionCreate, RegionUpdate, RegionOut

router = APIRouter()


def require_super_admin(user: User):
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin required")


def _get(db: Session, region_id: int) -> Region:
    region = db.query(Region).filter(Region.id == region_id).first()
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    return region


@router.get("/", response_model=list[RegionOut])
def list_regions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Parents first (nullsfirst), then by code — a stable, tree-friendly order.
    return db.query(Region).order_by(Region.parent_id.nullsfirst(), Region.code).all()


@router.post("/", response_model=RegionOut, status_code=201)
def create_region(
    data: RegionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_super_admin(current_user)
    if data.parent_id is not None:
        _get(db, data.parent_id)  # 404 if the parent doesn't exist

    region = Region(code=data.code, name=data.name, parent_id=data.parent_id)
    db.add(region)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Region code '{data.code}' already exists")
    db.expunge(region)
    db.commit()
    return region


@router.put("/{region_id}", response_model=RegionOut)
def update_region(
    region_id: int,
    data: RegionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_super_admin(current_user)
    region = _get(db, region_id)

    # Use model_fields_set so an explicit null (promote to top-level) is
    # distinguishable from an omitted field in a partial update.
    if "parent_id" in data.model_fields_set:
        new_parent = data.parent_id
        if new_parent is not None:
            if new_parent == region_id:
                raise HTTPException(status_code=400, detail="A region cannot be its own parent")
            _get(db, new_parent)  # 404 if the parent doesn't exist
            # Cycle guard: the new parent must not be a descendant of this region.
            cur = db.query(Region).filter(Region.id == new_parent).first()
            seen: set[int] = set()
            while cur is not None and cur.parent_id is not None and cur.id not in seen:
                if cur.parent_id == region_id:
                    raise HTTPException(status_code=400, detail="That change would create a region cycle")
                seen.add(cur.id)
                cur = db.query(Region).filter(Region.id == cur.parent_id).first()
        region.parent_id = new_parent
    if data.code is not None:
        region.code = data.code
    if data.name is not None:
        region.name = data.name

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Region code must be unique")
    db.expunge(region)
    db.commit()
    return region


@router.delete("/{region_id}")
def delete_region(
    region_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_super_admin(current_user)
    region = _get(db, region_id)
    db.delete(region)
    try:
        db.commit()
    except IntegrityError:
        # A data row (cost model / index value / freight lane / …) still references
        # this region's code, so the FK blocks the delete.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Region is in use and cannot be deleted; reassign those records first",
        )
    return {"status": "deleted"}
