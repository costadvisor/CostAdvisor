import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.subfamily import Subfamily
from app.models.chemical_family import ChemicalFamily
from app.models.team import TeamMembership
from app.routers.auth import get_current_user
from app.schemas.subfamily import SubfamilyCreate, SubfamilyForkRequest, SubfamilyUpdate, SubfamilyOut
from app.services.audit import log_event
from app.services.permissions import require_permission

router = APIRouter()


def require_super_admin(user: User):
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin required")


def _first_team_id(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    m = db.query(TeamMembership).filter(TeamMembership.user_id == user_id).first()
    return m.team_id if m else None


@router.get("/", response_model=list[SubfamilyOut])
def list_subfamilies(
    family_id: int | None = None,
    team_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Platform subfamilies (team_id IS NULL) plus the caller's team forks.

    RLS filters to platform + member teams; family_id/team_id narrow the result.
    """
    q = db.query(Subfamily)
    if family_id is not None:
        q = q.filter(Subfamily.family_id == family_id)
    if team_id is not None:
        q = q.filter(
            or_(Subfamily.team_id == None, Subfamily.team_id == team_id)  # noqa: E711
        )
    return q.order_by(Subfamily.team_id.nullsfirst(), Subfamily.name).all()


@router.post("/", response_model=SubfamilyOut, status_code=201)
def create_subfamily(
    data: SubfamilyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if data.team_id is None:
        require_super_admin(current_user)
    else:
        require_permission(db, current_user, data.team_id, "products.edit")

    # The parent family must be visible to the caller (RLS returns platform + own team).
    family = db.query(ChemicalFamily).filter(ChemicalFamily.id == data.family_id).first()
    if not family:
        raise HTTPException(status_code=404, detail="Parent family not found")

    sub = Subfamily(
        family_id=data.family_id,
        name=data.name,
        code=data.code,
        team_id=data.team_id,
    )
    db.add(sub)
    db.flush()

    audit_team_id = data.team_id or _first_team_id(db, current_user.id)
    if audit_team_id:
        log_event(db, audit_team_id, current_user.id, "create", "subfamily",
                  str(sub.id), new_value={"name": data.name, "platform": data.team_id is None})

    db.expunge(sub)
    db.commit()
    return sub


@router.post("/{subfamily_id}/fork", response_model=SubfamilyOut, status_code=201)
def fork_subfamily(
    subfamily_id: int,
    data: SubfamilyForkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Copy a platform subfamily into a team as an editable, private fork.

    family_id is preserved: the fork stays under the same (platform) family, which
    the team can read via RLS. origin_id back-links to the platform original.
    """
    require_permission(db, current_user, data.team_id, "products.edit")

    source = db.query(Subfamily).filter(Subfamily.id == subfamily_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Subfamily not found")
    if source.team_id is not None:
        raise HTTPException(status_code=400, detail="Only platform subfamilies can be forked")

    existing = (
        db.query(Subfamily)
        .filter(Subfamily.team_id == data.team_id, Subfamily.origin_id == subfamily_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="This subfamily is already forked for the team")

    fork = Subfamily(
        family_id=source.family_id,
        name=source.name,
        code=source.code,
        team_id=data.team_id,
        origin_id=source.id,
    )
    db.add(fork)
    db.flush()
    log_event(db, data.team_id, current_user.id, "fork", "subfamily",
              str(fork.id), new_value={"name": fork.name, "origin_id": source.id})
    db.expunge(fork)
    db.commit()
    return fork


@router.put("/{subfamily_id}", response_model=SubfamilyOut)
def update_subfamily(
    subfamily_id: int,
    data: SubfamilyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rename/edit a subfamily — a team's own fork, or (super-admin) the platform row."""
    sub = db.query(Subfamily).filter(Subfamily.id == subfamily_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subfamily not found")

    if sub.team_id is None:
        require_super_admin(current_user)
    else:
        require_permission(db, current_user, sub.team_id, "products.edit")

    previous = {"name": sub.name, "code": sub.code}
    if data.name is not None:
        sub.name = data.name
    if "code" in data.model_fields_set:
        sub.code = data.code

    audit_team_id = sub.team_id or _first_team_id(db, current_user.id)
    if audit_team_id:
        log_event(db, audit_team_id, current_user.id, "update", "subfamily",
                  str(sub.id), previous_value=previous, new_value={"name": sub.name, "code": sub.code})

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A subfamily with that name or code already exists in this scope")
    db.expunge(sub)
    db.commit()
    return sub


@router.delete("/{subfamily_id}")
def delete_subfamily(
    subfamily_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = db.query(Subfamily).filter(Subfamily.id == subfamily_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subfamily not found")

    if sub.team_id is None:
        require_super_admin(current_user)
    else:
        require_permission(db, current_user, sub.team_id, "products.delete")
        log_event(db, sub.team_id, current_user.id, "delete", "subfamily",
                  str(subfamily_id), previous_value={"name": sub.name})

    db.delete(sub)
    db.commit()
    return {"status": "deleted"}
