"""Admin-only CRUD for global permissions, plans, and platform roles.
Also exposes a read-only /permissions endpoint accessible to any authenticated user
(needed by Team.jsx role editor to build permission checkboxes).
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.rbac import Permission, Plan, PlanPermission, Role, RolePermission
from app.models.user import User
from app.routers.auth import get_current_user
from app.routers.admin import require_super_admin
from app.schemas.rbac import (
    PermissionOut, PermissionCreate,
    PlanOut, PlanDetailOut, PlanCreate, PlanUpdate,
    RoleOut, RoleDetailOut, RoleCreate, RoleUpdate,
)

router = APIRouter()


# ── Permissions ───────────────────────────────────────────────────────────────

@router.get("/permissions", response_model=list[PermissionOut])
def list_permissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Any authenticated user can read the permissions list (needed for role editor)."""
    return db.query(Permission).order_by(Permission.category, Permission.action).all()


@router.post("/permissions", response_model=PermissionOut, status_code=201)
def create_permission(
    data: PermissionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    if db.query(Permission).filter(Permission.key == data.key).first():
        raise HTTPException(400, detail="Permission key already exists")
    perm = Permission(key=data.key, label=data.label, category=data.category, action=data.action)
    db.add(perm)
    db.flush()
    db.expunge(perm)
    db.commit()
    return perm


# ── Plans ─────────────────────────────────────────────────────────────────────

@router.get("/plans", response_model=list[PlanOut])
def list_plans(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Any authenticated user can read available plans."""
    plans = db.query(Plan).order_by(Plan.name).all()
    result = []
    for p in plans:
        result.append(PlanOut(
            id=p.id, name=p.name, description=p.description,
            is_default=p.is_default, permission_count=len(p.permissions),
        ))
    return result


@router.post("/plans", response_model=PlanDetailOut, status_code=201)
def create_plan(
    data: PlanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    if db.query(Plan).filter(Plan.name == data.name).first():
        raise HTTPException(400, detail="Plan name already exists")
    if data.is_default:
        db.query(Plan).filter(Plan.is_default == True).update({"is_default": False})  # noqa: E712
    plan = Plan(name=data.name, description=data.description, is_default=data.is_default)
    db.add(plan)
    db.flush()
    for perm_id in data.permission_ids:
        perm = db.query(Permission).filter(Permission.id == perm_id).first()
        if perm:
            db.add(PlanPermission(plan_id=plan.id, permission_id=perm_id))
    db.commit()
    db.refresh(plan)
    return PlanDetailOut(
        id=plan.id, name=plan.name, description=plan.description,
        is_default=plan.is_default, permissions=[
            PermissionOut(id=p.id, key=p.key, label=p.label,
                          category=p.category, action=p.action)
            for p in plan.permissions
        ],
    )


@router.get("/plans/{plan_id}", response_model=PlanDetailOut)
def get_plan(
    plan_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(404, detail="Plan not found")
    return PlanDetailOut(
        id=plan.id, name=plan.name, description=plan.description,
        is_default=plan.is_default, permissions=[
            PermissionOut(id=p.id, key=p.key, label=p.label,
                          category=p.category, action=p.action)
            for p in plan.permissions
        ],
    )


@router.put("/plans/{plan_id}", response_model=PlanDetailOut)
def update_plan(
    plan_id: uuid.UUID,
    data: PlanUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(404, detail="Plan not found")

    if data.name is not None and data.name != plan.name:
        if db.query(Plan).filter(Plan.name == data.name).first():
            raise HTTPException(400, detail="Plan name already exists")
        plan.name = data.name
    if data.description is not None:
        plan.description = data.description
    if data.is_default is not None and data.is_default and not plan.is_default:
        db.query(Plan).filter(Plan.is_default == True).update({"is_default": False})  # noqa: E712
        plan.is_default = True

    # Replace permissions
    db.query(PlanPermission).filter(PlanPermission.plan_id == plan_id).delete()
    for perm_id in data.permission_ids:
        if db.query(Permission).filter(Permission.id == perm_id).first():
            db.add(PlanPermission(plan_id=plan_id, permission_id=perm_id))

    db.commit()
    db.refresh(plan)
    return PlanDetailOut(
        id=plan.id, name=plan.name, description=plan.description,
        is_default=plan.is_default, permissions=[
            PermissionOut(id=p.id, key=p.key, label=p.label,
                          category=p.category, action=p.action)
            for p in plan.permissions
        ],
    )


@router.delete("/plans/{plan_id}")
def delete_plan(
    plan_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(404, detail="Plan not found")
    if plan.is_default:
        raise HTTPException(400, detail="Cannot delete the default plan")
    db.delete(plan)
    db.commit()
    return {"status": "deleted"}


# ── Platform Roles (team_id IS NULL) ──────────────────────────────────────────

PROTECTED_PLATFORM_ROLES = {"User", "SuperAdmin", "Chemist", "FX Manager",
                            "Content Editor"}


def _role_out(role: Role) -> RoleOut:
    return RoleOut(
        id=role.id, team_id=role.team_id, name=role.name,
        description=role.description, permission_count=len(role.permissions),
    )


def _role_detail_out(role: Role) -> RoleDetailOut:
    return RoleDetailOut(
        id=role.id, team_id=role.team_id, name=role.name,
        description=role.description,
        permissions=[
            PermissionOut(id=p.id, key=p.key, label=p.label,
                          category=p.category, action=p.action)
            for p in role.permissions
        ],
    )


@router.get("/roles", response_model=list[RoleOut])
def list_platform_roles(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    roles = db.query(Role).filter(Role.team_id == None).order_by(Role.name).all()  # noqa: E711
    return [_role_out(r) for r in roles]


@router.post("/roles", response_model=RoleDetailOut, status_code=201)
def create_platform_role(
    data: RoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    if db.query(Role).filter(Role.team_id == None, Role.name == data.name).first():  # noqa: E711
        raise HTTPException(400, detail="Platform role name already exists")
    role = Role(team_id=None, name=data.name, description=data.description)
    db.add(role)
    db.flush()
    for perm_id in data.permission_ids:
        if db.query(Permission).filter(Permission.id == perm_id).first():
            db.add(RolePermission(role_id=role.id, permission_id=perm_id))
    db.commit()
    db.refresh(role)
    return _role_detail_out(role)


@router.get("/roles/{role_id}", response_model=RoleDetailOut)
def get_platform_role(
    role_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    role = db.query(Role).filter(Role.id == role_id, Role.team_id == None).first()  # noqa: E711
    if not role:
        raise HTTPException(404, detail="Platform role not found")
    return _role_detail_out(role)


@router.put("/roles/{role_id}", response_model=RoleDetailOut)
def update_platform_role(
    role_id: uuid.UUID,
    data: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    role = db.query(Role).filter(Role.id == role_id, Role.team_id == None).first()  # noqa: E711
    if not role:
        raise HTTPException(404, detail="Platform role not found")
    if data.name is not None and data.name != role.name:
        if role.name in PROTECTED_PLATFORM_ROLES:
            raise HTTPException(400, detail=f"Cannot rename the '{role.name}' platform role")
        if db.query(Role).filter(Role.team_id == None, Role.name == data.name).first():  # noqa: E711
            raise HTTPException(400, detail="Platform role name already exists")
        role.name = data.name
    if data.description is not None:
        role.description = data.description
    db.query(RolePermission).filter(RolePermission.role_id == role_id).delete()
    for perm_id in data.permission_ids:
        if db.query(Permission).filter(Permission.id == perm_id).first():
            db.add(RolePermission(role_id=role_id, permission_id=perm_id))
    db.commit()
    db.refresh(role)
    return _role_detail_out(role)


@router.delete("/roles/{role_id}")
def delete_platform_role(
    role_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    role = db.query(Role).filter(Role.id == role_id, Role.team_id == None).first()  # noqa: E711
    if not role:
        raise HTTPException(404, detail="Platform role not found")
    if role.name in PROTECTED_PLATFORM_ROLES:
        raise HTTPException(400, detail=f"Cannot delete the default '{role.name}' role")
    db.delete(role)
    db.commit()
    return {"status": "deleted"}
