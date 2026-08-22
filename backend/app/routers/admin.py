import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel
from jose import jwt, JWTError

from app.database import get_db, bypass_rls_var
from app.models.user import User
from app.models.team import Team, TeamMembership
from app.models.audit_log import AuditLog
from app.models.auth_event import AuthEvent
from app.models.access_request import PlatformAccessRequest
from app.models.rbac import Plan, Role, TeamMemberRole, UserPlatformRole
from app.models.demo import DemoHost, DemoBlockedSlot, DemoRequest
from app.config import get_settings
from app.routers.auth import get_current_user, create_jwt
from app.schemas.user import UserOut
from app.schemas.audit_log import AuditLogOut
from app.schemas.auth_event import AuthEventOut
from app.schemas.access_request import AccessRequestOut
from app.schemas.demo import (
    DemoHostCreate, DemoHostUpdate, DemoHostOut,
    BlockedSlotCreate, BlockedSlotOut,
    DemoRequestOut, DemoRemarkUpdate,
)
from app.services.audit import log_event
from app.services.email import (
    send_access_granted_email, send_welcome_email,
    send_demo_confirmation_email,
)

router = APIRouter()
settings = get_settings()


def require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin required")
    return current_user


def _first_team_id(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    """Return the first team this user belongs to, for audit log attribution."""
    m = db.query(TeamMembership).filter(TeamMembership.user_id == user_id).first()
    return m.team_id if m else None


class UserAdminOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    avatar_url: str | None
    is_super_admin: bool
    created_at: str
    last_login_at: str | None
    deleted_at: str | None
    teams: list[dict]
    platform_role_names: list[str] = []

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    display_name: str | None = None
    is_super_admin: bool | None = None


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserAdminOut])
def list_all_users(
    search: str | None = Query(None),
    include_deleted: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    query = db.query(User).filter(User.id != current_user.id)
    if not include_deleted:
        query = query.filter(User.deleted_at == None)  # noqa: E711
    if search:
        term = f"%{search.lower()}%"
        query = query.filter(
            or_(
                User.email.ilike(term),
                User.display_name.ilike(term),
            )
        )
    users = query.order_by(User.created_at).all()

    # Batch-load platform role assignments to avoid N+1
    user_ids = [u.id for u in users]
    platform_role_rows = (
        db.query(UserPlatformRole).filter(UserPlatformRole.user_id.in_(user_ids)).all()
        if user_ids else []
    )
    platform_role_ids = {r.role_id for r in platform_role_rows}
    roles_by_id = (
        {r.id: r.name for r in db.query(Role).filter(Role.id.in_(platform_role_ids)).all()}
        if platform_role_ids else {}
    )
    platform_roles_by_user: dict = {}
    for row in platform_role_rows:
        platform_roles_by_user.setdefault(row.user_id, []).append(
            roles_by_id.get(row.role_id, "?")
        )

    result = []
    for u in users:
        teams = []
        for m in u.memberships:
            team = db.query(Team).filter(Team.id == m.team_id).first()
            teams.append({
                "team_id": str(m.team_id),
                "team_name": team.name if team else "Unknown",
                "role": m.role,
            })
        result.append(UserAdminOut(
            id=u.id,
            email=u.email,
            display_name=u.display_name,
            avatar_url=u.avatar_url,
            is_super_admin=u.is_super_admin,
            created_at=u.created_at.isoformat() if u.created_at else "",
            last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
            deleted_at=u.deleted_at.isoformat() if u.deleted_at else None,
            teams=teams,
            platform_role_names=platform_roles_by_user.get(u.id, []),
        ))
    return result


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    if user_id == current_user.id and data.is_super_admin is False:
        raise HTTPException(status_code=400, detail="Cannot revoke your own super admin privileges")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    changes: dict = {}
    if data.display_name is not None:
        changes["display_name"] = {"from": user.display_name, "to": data.display_name}
        user.display_name = data.display_name
    if data.is_super_admin is not None:
        changes["is_super_admin"] = {"from": user.is_super_admin, "to": data.is_super_admin}
        user.is_super_admin = data.is_super_admin

    team_id = _first_team_id(db, user.id)
    if team_id and changes:
        log_event(db, team_id, current_user.id, "admin_update_user", "user", str(user_id),
                  new_value={"changes": changes, "by": current_user.email})

    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.deleted_at:
        raise HTTPException(status_code=400, detail="User already deleted")

    user.deleted_at = datetime.now(timezone.utc)

    team_id = _first_team_id(db, user.id)
    if team_id:
        log_event(db, team_id, current_user.id, "admin_delete_user", "user", str(user_id),
                  new_value={"email": user.email, "by": current_user.email})

    db.commit()
    return {"status": "deleted"}


@router.post("/users/{user_id}/restore")
def restore_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.deleted_at = None

    team_id = _first_team_id(db, user.id)
    if team_id:
        log_event(db, team_id, current_user.id, "admin_restore_user", "user", str(user_id),
                  new_value={"email": user.email, "by": current_user.email})

    db.commit()
    return {"status": "restored"}


# ── Impersonation ─────────────────────────────────────────────────────────────

@router.post("/impersonate/{user_id}")
def impersonate(
    user_id: uuid.UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    if request.cookies.get("ca_impersonating"):
        raise HTTPException(status_code=400, detail="Already impersonating a user — stop current session first")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.deleted_at:
        raise HTTPException(status_code=400, detail="Cannot impersonate a deleted user")
    if target.is_super_admin:
        raise HTTPException(status_code=400, detail="Cannot impersonate a super admin")

    admin_token = create_jwt(current_user.id)
    target_token = create_jwt(target.id)

    is_prod = settings.environment != "development"
    samesite = "none" if is_prod else "lax"

    response.set_cookie("ca_admin_token", admin_token, httponly=True, secure=is_prod,
                        samesite=samesite, max_age=3600 * 24)
    response.set_cookie("ca_token", target_token, httponly=True, secure=is_prod,
                        samesite=samesite, max_age=3600 * 24)
    # Not HttpOnly so the frontend can read it to show ImpersonationBar
    response.set_cookie("ca_impersonating", "1", httponly=False, secure=is_prod,
                        samesite=samesite, max_age=3600 * 24)

    team_id = _first_team_id(db, target.id) or _first_team_id(db, current_user.id)
    if team_id:
        log_event(db, team_id, current_user.id, "admin_impersonate_start", "user", str(user_id),
                  new_value={"target_email": target.email, "by": current_user.email})
    db.commit()

    return {"status": "impersonating", "target_email": target.email, "target_name": target.display_name}


@router.post("/stop-impersonate")
def stop_impersonate(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    admin_token = request.cookies.get("ca_admin_token")
    if not admin_token:
        raise HTTPException(status_code=400, detail="Not currently impersonating")

    # Decode both tokens to log who stopped impersonating whom
    admin_user_id: uuid.UUID | None = None
    impersonated_user_id: uuid.UUID | None = None
    try:
        payload = jwt.decode(admin_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        admin_user_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        pass

    current_token = request.cookies.get("ca_token")
    if current_token:
        try:
            payload = jwt.decode(current_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            impersonated_user_id = uuid.UUID(payload["sub"])
        except (JWTError, KeyError, ValueError):
            pass

    # Bypass RLS so the audit log insert can succeed: this endpoint has no
    # get_current_user dependency (it runs off the raw cookies), so no user
    # context is established. Reset in finally so the bypass never outlives
    # this block, matching the pattern used elsewhere.
    bypass_rls_var.set(True)
    try:
        if admin_user_id and impersonated_user_id:
            admin_user = db.query(User).filter(User.id == admin_user_id).first()
            impersonated_user = db.query(User).filter(User.id == impersonated_user_id).first()
            team_id = _first_team_id(db, impersonated_user_id) or _first_team_id(db, admin_user_id)
            if team_id:
                log_event(db, team_id, admin_user_id, "admin_impersonate_stop", "user",
                          str(impersonated_user_id),
                          new_value={
                              "by": admin_user.email if admin_user else None,
                              "target_email": impersonated_user.email if impersonated_user else str(impersonated_user_id),
                          })
            db.commit()
    finally:
        bypass_rls_var.set(False)

    is_prod = settings.environment != "development"
    samesite = "none" if is_prod else "lax"
    response.set_cookie("ca_token", admin_token, httponly=True, secure=is_prod,
                        samesite=samesite, max_age=3600 * 24)
    # Must pass the same samesite/secure params used when setting, otherwise browsers ignore the delete
    response.delete_cookie("ca_admin_token", httponly=True, secure=is_prod, samesite=samesite)
    response.delete_cookie("ca_impersonating", httponly=False, secure=is_prod, samesite=samesite)

    return {"status": "restored"}


# ── Team management ───────────────────────────────────────────────────────────

class SetTeamRequest(BaseModel):
    team_id: uuid.UUID
    role: str = "member"


@router.post("/users/{user_id}/set-team")
def set_user_team(
    user_id: uuid.UUID,
    data: SetTeamRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    team = db.query(Team).filter(Team.id == data.team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    db.query(TeamMembership).filter(TeamMembership.user_id == user_id).delete()
    db.add(TeamMembership(user_id=user_id, team_id=data.team_id, role=data.role))
    log_event(db, data.team_id, current_user.id, "admin_set_team", "user", str(user_id),
              new_value={"team": team.name, "role": data.role, "by": current_user.email})
    db.commit()
    return {"status": "ok", "team_name": team.name}


@router.post("/users/{user_id}/add-team")
def add_user_to_team(
    user_id: uuid.UUID,
    data: SetTeamRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    team = db.query(Team).filter(Team.id == data.team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    existing = db.query(TeamMembership).filter(
        TeamMembership.user_id == user_id,
        TeamMembership.team_id == data.team_id,
    ).first()
    if existing:
        existing.role = data.role
    else:
        db.add(TeamMembership(user_id=user_id, team_id=data.team_id, role=data.role))

    log_event(db, data.team_id, current_user.id, "admin_add_team", "user", str(user_id),
              new_value={"team": team.name, "role": data.role, "by": current_user.email})
    db.commit()
    return {"status": "ok", "team_name": team.name}


@router.delete("/users/{user_id}/teams/{team_id}")
def remove_user_from_team(
    user_id: uuid.UUID,
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    m = db.query(TeamMembership).filter(
        TeamMembership.user_id == user_id,
        TeamMembership.team_id == team_id,
    ).first()
    if not m:
        raise HTTPException(status_code=404, detail="Membership not found")
    log_event(db, team_id, current_user.id, "admin_remove_team", "user", str(user_id),
              previous_value={"team_id": str(team_id), "role": m.role},
              new_value={"by": current_user.email})
    db.delete(m)
    db.commit()
    return {"status": "removed"}


# ── Teams ─────────────────────────────────────────────────────────────────────

class AdminRoleUpdate(BaseModel):
    role: str


def _get_owner(db: Session, team_id: uuid.UUID) -> TeamMembership | None:
    return db.query(TeamMembership).filter(
        TeamMembership.team_id == team_id,
        TeamMembership.role == "owner",
    ).first()


def _build_members_with_roles(db, team_id, memberships):
    """Batch-load custom roles for all team members to avoid N+1 queries."""
    member_role_rows = db.query(TeamMemberRole).filter(
        TeamMemberRole.team_id == team_id
    ).all()
    role_ids = {r.role_id for r in member_role_rows}
    roles_by_id = (
        {r.id: r.name for r in db.query(Role).filter(Role.id.in_(role_ids)).all()}
        if role_ids else {}
    )
    by_user: dict = {}
    for row in member_role_rows:
        by_user.setdefault(row.user_id, []).append(
            {"id": str(row.role_id), "name": roles_by_id.get(row.role_id, "?")}
        )
    return [
        {
            "user_id": str(m.user_id),
            "role": m.role,
            "email": m.user.email if m.user else None,
            "custom_roles": by_user.get(m.user_id, []),
        }
        for m in memberships
    ]


@router.get("/teams", response_model=list[dict])
def list_all_teams(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    teams = db.query(Team).order_by(Team.created_at).all()
    result = []
    for t in teams:
        members = db.query(TeamMembership).filter(TeamMembership.team_id == t.id).all()
        result.append({
            "id": str(t.id),
            "name": t.name,
            "created_at": t.created_at.isoformat() if t.created_at else "",
            "member_count": len(members),
            "plan_id": str(t.plan_id) if t.plan_id else None,
            "members": _build_members_with_roles(db, t.id, members),
        })
    return result


@router.patch("/teams/{team_id}/members/{user_id}")
def admin_update_member_role(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    data: AdminRoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    if data.role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=400, detail="Role must be owner, admin, or member")

    membership = db.query(TeamMembership).filter(
        TeamMembership.user_id == user_id,
        TeamMembership.team_id == team_id,
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")
    if membership.role == data.role:
        return {"status": "no change"}

    if data.role == "owner":
        # Transfer: demote the current owner to admin first
        current_owner = _get_owner(db, team_id)
        if current_owner and current_owner.user_id != user_id:
            current_owner.role = "admin"
            log_event(db, team_id, current_user.id, "admin_update_role", "team_member",
                      str(current_owner.user_id),
                      previous_value={"role": "owner"}, new_value={"role": "admin", "by": current_user.email})
    elif membership.role == "owner":
        raise HTTPException(status_code=400, detail="Transfer ownership to another member before changing the owner's role")

    previous_role = membership.role
    membership.role = data.role
    log_event(db, team_id, current_user.id, "admin_update_role", "team_member", str(user_id),
              previous_value={"role": previous_role}, new_value={"role": data.role, "by": current_user.email})
    db.commit()
    return {"status": "updated"}


@router.delete("/teams/{team_id}/members/{user_id}")
def admin_remove_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    membership = db.query(TeamMembership).filter(
        TeamMembership.user_id == user_id,
        TeamMembership.team_id == team_id,
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")
    if membership.role == "owner":
        raise HTTPException(status_code=400, detail="Transfer ownership before removing the owner")

    log_event(db, team_id, current_user.id, "admin_remove_member", "team_member", str(user_id),
              previous_value={"role": membership.role}, new_value={"by": current_user.email})
    db.delete(membership)
    db.commit()
    return {"status": "removed"}


class PlanAssignRequest(BaseModel):
    plan_id: uuid.UUID | None


@router.put("/teams/{team_id}/plan")
def assign_team_plan(
    team_id: uuid.UUID,
    data: PlanAssignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if data.plan_id is not None:
        plan = db.query(Plan).filter(Plan.id == data.plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
    team.plan_id = data.plan_id
    log_event(db, team_id, current_user.id, "admin_assign_plan", "team", str(team_id),
              new_value={"plan_id": str(data.plan_id) if data.plan_id else None,
                         "by": current_user.email})
    db.commit()
    return {"status": "ok"}


@router.delete("/teams/{team_id}")
def admin_delete_team(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    # Audit log is cascade-deleted with the team, so we just delete
    db.delete(team)
    db.commit()
    return {"status": "deleted"}


# ── Platform access requests ──────────────────────────────────────────────────

@router.get("/access-requests", response_model=list[AccessRequestOut])
def list_access_requests(
    status: str | None = Query(None),
    search: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    query = db.query(PlatformAccessRequest)
    if status:
        query = query.filter(PlatformAccessRequest.status == status)
    if search:
        term = f"%{search.lower()}%"
        query = query.filter(
            or_(
                PlatformAccessRequest.email.ilike(term),
                PlatformAccessRequest.name.ilike(term),
                PlatformAccessRequest.company.ilike(term),
            )
        )
    requests = query.order_by(PlatformAccessRequest.created_at.desc()).all()

    result = []
    for r in requests:
        reviewer_email = None
        if r.reviewed_by_id:
            reviewer = db.query(User).filter(User.id == r.reviewed_by_id).first()
            reviewer_email = reviewer.email if reviewer else None
        result.append(AccessRequestOut(
            id=r.id,
            email=r.email,
            name=r.name,
            company=r.company,
            status=r.status,
            created_at=r.created_at,
            reviewed_at=r.reviewed_at,
            reviewed_by_email=reviewer_email,
        ))
    return result


@router.post("/access-requests/{request_id}/accept")
def accept_access_request(
    request_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    req = db.query(PlatformAccessRequest).filter(PlatformAccessRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Access request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request is already {req.status}")

    req.status = "accepted"
    req.reviewed_at = datetime.now(timezone.utc)
    req.reviewed_by_id = current_user.id

    send_access_granted_email(req.email, settings.app_url)
    send_welcome_email(req.email, req.name or "", settings.app_url)

    team_id = _first_team_id(db, current_user.id)
    if team_id:
        log_event(db, team_id, current_user.id, "admin_accept_access_request", "access_request",
                  str(request_id),
                  new_value={"email": req.email, "by": current_user.email})
    db.commit()
    return {"status": "accepted"}


@router.post("/access-requests/{request_id}/reject")
def reject_access_request(
    request_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    req = db.query(PlatformAccessRequest).filter(PlatformAccessRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Access request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request is already {req.status}")

    req.status = "rejected"
    req.reviewed_at = datetime.now(timezone.utc)
    req.reviewed_by_id = current_user.id

    team_id = _first_team_id(db, current_user.id)
    if team_id:
        log_event(db, team_id, current_user.id, "admin_reject_access_request", "access_request",
                  str(request_id),
                  new_value={"email": req.email, "by": current_user.email})
    db.commit()
    return {"status": "rejected"}


# ── Platform role assignments ─────────────────────────────────────────────────

class PlatformRoleAssignRequest(BaseModel):
    role_id: uuid.UUID


@router.get("/users/{user_id}/platform-roles")
def get_user_platform_roles(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    rows = db.query(UserPlatformRole).filter(UserPlatformRole.user_id == user_id).all()
    role_ids = [r.role_id for r in rows]
    roles = db.query(Role).filter(Role.id.in_(role_ids), Role.team_id == None).all()  # noqa: E711
    return [{"id": str(r.id), "name": r.name} for r in roles]


@router.post("/users/{user_id}/platform-roles")
def assign_platform_role(
    user_id: uuid.UUID,
    data: PlatformRoleAssignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    role = db.query(Role).filter(Role.id == data.role_id, Role.team_id == None).first()  # noqa: E711
    if not role:
        raise HTTPException(status_code=404, detail="Platform role not found")
    if role.name == "SuperAdmin":
        raise HTTPException(status_code=400, detail="Use is_super_admin flag to assign SuperAdmin")

    existing = db.query(UserPlatformRole).filter(
        UserPlatformRole.user_id == user_id,
        UserPlatformRole.role_id == data.role_id,
    ).first()
    if existing:
        return {"status": "already_assigned"}

    db.add(UserPlatformRole(user_id=user_id, role_id=data.role_id))

    team_id = _first_team_id(db, user_id) or _first_team_id(db, current_user.id)
    if team_id:
        log_event(db, team_id, current_user.id, "admin_assign_platform_role", "user",
                  str(user_id),
                  new_value={"role": role.name, "by": current_user.email})
    db.commit()
    return {"status": "assigned"}


@router.delete("/users/{user_id}/platform-roles/{role_id}")
def remove_platform_role(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    row = db.query(UserPlatformRole).filter(
        UserPlatformRole.user_id == user_id,
        UserPlatformRole.role_id == role_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Role assignment not found")

    role = db.query(Role).filter(Role.id == role_id).first()
    team_id = _first_team_id(db, user_id) or _first_team_id(db, current_user.id)
    if team_id:
        log_event(db, team_id, current_user.id, "admin_remove_platform_role", "user",
                  str(user_id),
                  previous_value={"role": role.name if role else str(role_id)},
                  new_value={"by": current_user.email})
    db.delete(row)
    db.commit()
    return {"status": "removed"}


# ── Audit logs (cross-team admin view) ────────────────────────────────────────

@router.get("/audit-logs", response_model=list[AuditLogOut])
def list_admin_audit_logs(
    event_type: str | None = Query(None),
    entity_type: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """Return audit log entries across all teams (super-admin only)."""
    query = db.query(AuditLog)
    if event_type:
        query = query.filter(AuditLog.event_type == event_type)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)

    logs = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit).all()

    return [
        AuditLogOut(
            id=log.id,
            team_id=log.team_id,
            user_id=log.user_id,
            user_email=log.user.email if log.user else None,
            event_type=log.event_type,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            previous_value=log.previous_value,
            new_value=log.new_value,
            timestamp=log.timestamp,
        )
        for log in logs
    ]


# ── Auth events (login/logout audit trail, Scrum 10) ──────────────────────────

@router.get("/auth-events", response_model=list[AuthEventOut])
def list_auth_events(
    event_type: str | None = Query(None),
    email: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """Return login/logout/failed-login events (super-admin only)."""
    query = db.query(AuthEvent)
    if event_type:
        query = query.filter(AuthEvent.event_type == event_type)
    if email:
        query = query.filter(AuthEvent.email == email)
    return query.order_by(AuthEvent.created_at.desc()).offset(offset).limit(limit).all()


# ── Demo Hosts ────────────────────────────────────────────────────────────────

def _host_out(host: DemoHost) -> DemoHostOut:
    return DemoHostOut(
        id=host.id,
        user_id=host.user_id,
        user_name=host.user.display_name if host.user else None,
        user_email=host.user.email if host.user else None,
        is_active=host.is_active,
        timezone=host.timezone,
        slot_duration_minutes=host.slot_duration_minutes,
        working_days=host.working_days or [0, 1, 2, 3, 4],
        working_start=host.working_start,
        working_end=host.working_end,
        calendar_connected=bool(host.google_refresh_token_encrypted),
        google_email=host.google_email,
        created_at=host.created_at,
    )


@router.get("/demo-hosts", response_model=list[DemoHostOut])
def list_demo_hosts(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    hosts = db.query(DemoHost).all()
    return [_host_out(h) for h in hosts]


@router.post("/demo-hosts", response_model=DemoHostOut)
def create_demo_host(
    payload: DemoHostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    existing = db.query(DemoHost).filter(DemoHost.user_id == payload.user_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="User is already a demo host")
    host = DemoHost(
        user_id=payload.user_id,
        timezone=payload.timezone,
        slot_duration_minutes=payload.slot_duration_minutes,
        working_days=payload.working_days,
        working_start=payload.working_start,
        working_end=payload.working_end,
    )
    db.add(host)
    db.commit()
    db.refresh(host)
    return _host_out(host)


@router.put("/demo-hosts/{host_id}", response_model=DemoHostOut)
def update_demo_host(
    host_id: uuid.UUID,
    payload: DemoHostUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    host = db.get(DemoHost, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Demo host not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(host, field, value)
    db.commit()
    db.refresh(host)
    return _host_out(host)


@router.delete("/demo-hosts/{host_id}")
def delete_demo_host(
    host_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    host = db.get(DemoHost, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Demo host not found")
    db.delete(host)
    db.commit()
    return {"status": "deleted"}


@router.delete("/demo-hosts/{host_id}/calendar")
def disconnect_demo_host_calendar(
    host_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """Remove stored Google Calendar credentials from a demo host."""
    host = db.get(DemoHost, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Demo host not found")
    host.google_email = None
    host.google_refresh_token_encrypted = None
    host.google_token_expiry = None
    db.commit()
    return {"status": "disconnected"}


# ── Blocked Slots ─────────────────────────────────────────────────────────────

@router.get("/demo-hosts/{host_id}/blocked-slots", response_model=list[BlockedSlotOut])
def list_blocked_slots(
    host_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    host = db.get(DemoHost, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Demo host not found")
    slots = db.query(DemoBlockedSlot).filter(DemoBlockedSlot.host_id == host_id).all()
    return slots


@router.post("/demo-hosts/{host_id}/blocked-slots", response_model=BlockedSlotOut)
def add_blocked_slot(
    host_id: uuid.UUID,
    payload: BlockedSlotCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    host = db.get(DemoHost, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Demo host not found")
    slot = DemoBlockedSlot(
        host_id=host_id,
        blocked_date=payload.blocked_date,
        start_time=payload.start_time,
        end_time=payload.end_time,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


@router.delete("/demo-hosts/{host_id}/blocked-slots/{slot_id}")
def delete_blocked_slot(
    host_id: uuid.UUID,
    slot_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    slot = db.get(DemoBlockedSlot, slot_id)
    if not slot or slot.host_id != host_id:
        raise HTTPException(status_code=404, detail="Blocked slot not found")
    db.delete(slot)
    db.commit()
    return {"status": "deleted"}


# ── Demo Requests ─────────────────────────────────────────────────────────────

def _req_out(req: DemoRequest) -> DemoRequestOut:
    return DemoRequestOut(
        id=req.id,
        email=req.email,
        name=req.name,
        phone=req.phone,
        company=req.company,
        requested_date=req.requested_date,
        requested_start=req.requested_start,
        requested_end=req.requested_end,
        visitor_timezone=req.visitor_timezone,
        status=req.status,
        meet_link=req.meet_link,
        remarks=req.remarks,
        created_at=req.created_at,
        reviewed_at=req.reviewed_at,
        reviewed_by_name=(
            req.reviewed_by.display_name or req.reviewed_by.email
            if req.reviewed_by else None
        ),
        assigned_host_name=(
            req.assigned_host.user.display_name or req.assigned_host.user.email
            if req.assigned_host and req.assigned_host.user else None
        ),
    )


@router.get("/demo-requests", response_model=list[DemoRequestOut])
def list_demo_requests(
    status: str | None = Query(None),
    search: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    query = db.query(DemoRequest)
    if status:
        query = query.filter(DemoRequest.status == status)
    if search:
        term = f"%{search.lower()}%"
        query = query.filter(
            or_(
                DemoRequest.email.ilike(term),
                DemoRequest.name.ilike(term),
                DemoRequest.company.ilike(term),
            )
        )
    reqs = query.order_by(DemoRequest.created_at.desc()).all()
    return [_req_out(r) for r in reqs]


class DemoAcceptPayload(BaseModel):
    remarks: str | None = None


@router.post("/demo-requests/{request_id}/accept")
def accept_demo_request(
    request_id: uuid.UUID,
    payload: DemoAcceptPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """Accept a demo request. The current user must be a configured demo host
    with a Google Calendar connection — they become the host for this meeting."""
    req = db.get(DemoRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Demo request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request is already {req.status}")

    # Current user must be an active demo host with calendar connected
    host = db.query(DemoHost).filter(
        DemoHost.user_id == current_user.id,
        DemoHost.is_active == True,  # noqa: E712
    ).first()
    if not host:
        raise HTTPException(
            status_code=400,
            detail="You are not configured as a demo host. Ask a super-admin to add you in Admin → Settings → Demo Hosts.",
        )
    if not host.google_refresh_token_encrypted:
        raise HTTPException(
            status_code=400,
            detail="Connect your Google Calendar first in Admin → Settings → Demo Hosts.",
        )

    # Check this host doesn't already have an accepted booking at this slot
    conflict = db.query(DemoRequest).filter(
        DemoRequest.assigned_host_id == host.id,
        DemoRequest.requested_date == req.requested_date,
        DemoRequest.requested_start == req.requested_start,
        DemoRequest.status == "accepted",
    ).first()
    if conflict:
        raise HTTPException(
            status_code=409,
            detail="You already have a confirmed demo at this time slot.",
        )

    # Create Google Calendar event with Meet
    from datetime import datetime as dt
    from app.services.google_calendar import create_google_meet

    start_dt = dt.strptime(
        f"{req.requested_date} {req.requested_start}", "%Y-%m-%d %H:%M"
    )
    end_dt = dt.strptime(
        f"{req.requested_date} {req.requested_end}", "%Y-%m-%d %H:%M"
    )

    try:
        meet_link, event_id = create_google_meet(
            host=host,
            requester_email=req.email,
            requester_name=req.name,
            company=req.company,
            start_dt=start_dt,
            end_dt=end_dt,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to create Google Meet: {exc}",
        )

    req.status = "accepted"
    req.assigned_host_id = host.id
    req.meet_link = meet_link
    req.calendar_event_id = event_id
    req.remarks = payload.remarks
    req.reviewed_at = datetime.now(timezone.utc)
    req.reviewed_by_id = current_user.id
    db.commit()

    host_name = current_user.display_name or current_user.email
    date_fmt = req.requested_date
    time_fmt = f"{req.requested_start}–{req.requested_end}"
    send_demo_confirmation_email(
        req.email, req.name, date_fmt, time_fmt, meet_link, host_name
    )

    log_event(
        db,
        event_type="admin_accept_demo_request",
        user_id=current_user.id,
        new_value={"email": req.email, "date": req.requested_date, "host": host_name},
    )

    return _req_out(req)


class DemoRejectPayload(BaseModel):
    remarks: str | None = None


@router.post("/demo-requests/{request_id}/reject")
def reject_demo_request(
    request_id: uuid.UUID,
    payload: DemoRejectPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    req = db.get(DemoRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Demo request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request is already {req.status}")

    req.status = "rejected"
    req.remarks = payload.remarks
    req.reviewed_at = datetime.now(timezone.utc)
    req.reviewed_by_id = current_user.id
    db.commit()

    log_event(
        db,
        event_type="admin_reject_demo_request",
        user_id=current_user.id,
        new_value={"email": req.email, "date": req.requested_date},
    )

    return _req_out(req)


@router.patch("/demo-requests/{request_id}/remarks")
def update_demo_remarks(
    request_id: uuid.UUID,
    payload: DemoRemarkUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    req = db.get(DemoRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Demo request not found")
    req.remarks = payload.remarks
    db.commit()

    log_event(
        db,
        event_type="admin_edit_demo_remarks",
        user_id=current_user.id,
        new_value={"email": req.email, "remarks": payload.remarks},
    )

    return _req_out(req)
