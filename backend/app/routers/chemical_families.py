import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.chemical_family import ChemicalFamily
from app.models.team import TeamMembership
from app.routers.auth import get_current_user
from sqlalchemy.exc import IntegrityError

from app.schemas.chemical_family import (
    ChemicalFamilyCreate,
    ChemicalFamilyForkRequest,
    ChemicalFamilyUpdate,
    ChemicalFamilyOut,
)
from app.services.audit import log_event
from app.services.permissions import require_permission

router = APIRouter()


def require_super_admin(user: User):
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin required")


def _first_team_id(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    m = db.query(TeamMembership).filter(TeamMembership.user_id == user_id).first()
    return m.team_id if m else None


@router.get("/", response_model=list[ChemicalFamilyOut])
def list_families(
    team_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Platform families (team_id IS NULL) plus the caller's team forks.

    RLS already filters to platform + member teams; the optional team_id narrows
    to a single team's forks (still alongside the shared platform rows).
    """
    q = db.query(ChemicalFamily)
    if team_id is not None:
        q = q.filter(
            or_(ChemicalFamily.team_id == None, ChemicalFamily.team_id == team_id)  # noqa: E711
        )
    return q.order_by(ChemicalFamily.team_id.nullsfirst(), ChemicalFamily.name).all()


@router.post("/", response_model=ChemicalFamilyOut, status_code=201)
def create_family(
    data: ChemicalFamilyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Platform rows are super-admin only; a team row needs products.edit on that team.
    if data.team_id is None:
        require_super_admin(current_user)
    else:
        require_permission(db, current_user, data.team_id, "products.edit")

    family = ChemicalFamily(
        name=data.name,
        code=data.code,
        team_id=data.team_id,
        custom_attribute_schema=data.custom_attribute_schema,
    )
    db.add(family)
    db.flush()

    audit_team_id = data.team_id or _first_team_id(db, current_user.id)
    if audit_team_id:
        log_event(db, audit_team_id, current_user.id, "create", "chemical_family",
                  str(family.id), new_value={"name": data.name, "platform": data.team_id is None})

    db.expunge(family)
    db.commit()
    return family


@router.post("/{family_id}/fork", response_model=ChemicalFamilyOut, status_code=201)
def fork_family(
    family_id: int,
    data: ChemicalFamilyForkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Copy a platform family into a team as an editable, private fork.

    The fork keeps origin_id pointing at the platform original, so platform
    formula/index resolution still works even after the team renames its copy.
    """
    require_permission(db, current_user, data.team_id, "products.edit")

    source = db.query(ChemicalFamily).filter(ChemicalFamily.id == family_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Chemical family not found")
    if source.team_id is not None:
        # Only the shared platform copy is forkable — a team row is already private.
        raise HTTPException(status_code=400, detail="Only platform families can be forked")

    existing = (
        db.query(ChemicalFamily)
        .filter(ChemicalFamily.team_id == data.team_id, ChemicalFamily.origin_id == family_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="This family is already forked for the team")

    fork = ChemicalFamily(
        name=source.name,
        code=source.code,
        team_id=data.team_id,
        origin_id=source.id,
        custom_attribute_schema=source.custom_attribute_schema,
    )
    db.add(fork)
    db.flush()
    log_event(db, data.team_id, current_user.id, "fork", "chemical_family",
              str(fork.id), new_value={"name": fork.name, "origin_id": source.id})
    db.expunge(fork)
    db.commit()
    return fork


@router.put("/{family_id}", response_model=ChemicalFamilyOut)
def update_family(
    family_id: int,
    data: ChemicalFamilyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rename/edit a family — a team's own fork, or (super-admin) the platform row.

    This is the point of forking: origin_id keeps resolving to the platform
    original for formula/index purposes even after the team renames or
    re-codes their private copy.
    """
    family = db.query(ChemicalFamily).filter(ChemicalFamily.id == family_id).first()
    if not family:
        raise HTTPException(status_code=404, detail="Chemical family not found")

    if family.team_id is None:
        require_super_admin(current_user)
    else:
        require_permission(db, current_user, family.team_id, "products.edit")

    previous = {"name": family.name, "code": family.code}
    if data.name is not None:
        family.name = data.name
    if "code" in data.model_fields_set:
        family.code = data.code
    if "custom_attribute_schema" in data.model_fields_set:
        family.custom_attribute_schema = data.custom_attribute_schema

    audit_team_id = family.team_id or _first_team_id(db, current_user.id)
    if audit_team_id:
        log_event(db, audit_team_id, current_user.id, "update", "chemical_family",
                  str(family.id), previous_value=previous, new_value={"name": family.name, "code": family.code})

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A family with that name or code already exists in this scope")
    db.expunge(family)
    db.commit()
    return family


@router.delete("/{family_id}")
def delete_family(
    family_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    family = db.query(ChemicalFamily).filter(ChemicalFamily.id == family_id).first()
    if not family:
        raise HTTPException(status_code=404, detail="Chemical family not found")

    # Platform rows are super-admin only; a team fork needs products.delete on that team.
    if family.team_id is None:
        require_super_admin(current_user)
    else:
        require_permission(db, current_user, family.team_id, "products.delete")
        log_event(db, family.team_id, current_user.id, "delete", "chemical_family",
                  str(family_id), previous_value={"name": family.name})

    db.delete(family)
    db.commit()
    return {"status": "deleted"}
