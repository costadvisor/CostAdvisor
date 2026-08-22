import uuid
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.team import Team, TeamMembership
from app.models.rbac import Permission, RolePermission, PlanPermission, TeamMemberRole, UserPlatformRole
from app.models.user import User


def has_permission(db: Session, user: User, team_id: uuid.UUID, key: str) -> bool:
    """
    Returns True if user is allowed to perform the action identified by key in team_id.
    Super admins bypass all checks.
    Falls back to membership.role if no custom roles are assigned.
    """
    if user.is_super_admin:
        return True

    membership = db.query(TeamMembership).filter(
        TeamMembership.user_id == user.id,
        TeamMembership.team_id == team_id,
    ).first()
    if not membership:
        return False

    # Plan ceiling: if team has a plan and it doesn't include this permission → deny
    team = db.query(Team).filter(Team.id == team_id).first()
    if team and team.plan_id:
        plan_ok = (
            db.query(PlanPermission)
            .join(Permission, Permission.id == PlanPermission.permission_id)
            .filter(
                PlanPermission.plan_id == team.plan_id,
                Permission.key == key,
            )
            .first()
        )
        if not plan_ok:
            return False

    # User's custom roles in this team
    member_role_rows = db.query(TeamMemberRole).filter(
        TeamMemberRole.user_id == user.id,
        TeamMemberRole.team_id == team_id,
    ).all()

    if member_role_rows:
        role_ids = [r.role_id for r in member_role_rows]
        return bool(
            db.query(RolePermission)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .filter(
                RolePermission.role_id.in_(role_ids),
                Permission.key == key,
            )
            .first()
        )

    # Fallback: no custom roles assigned — use TeamMembership.role
    action = key.split(".")[-1]
    if membership.role == "owner":
        return True
    elif membership.role == "admin":
        return action != "delete"
    else:  # member
        return action in ("view", "export")


def require_permission(db: Session, user: User, team_id: uuid.UUID, key: str) -> None:
    if not has_permission(db, user, team_id, key):
        raise HTTPException(status_code=403, detail=f"Permission required: {key}")


def has_platform_permission(db: Session, user: User, key: str) -> bool:
    """Check if user has a platform-level permission (not team-scoped). Super admins bypass."""
    if user.is_super_admin:
        return True
    rows = db.query(UserPlatformRole).filter(UserPlatformRole.user_id == user.id).all()
    if not rows:
        return False
    role_ids = [r.role_id for r in rows]
    return bool(
        db.query(RolePermission)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .filter(RolePermission.role_id.in_(role_ids), Permission.key == key)
        .first()
    )


def require_platform_permission(db: Session, user: User, key: str) -> None:
    if not has_platform_permission(db, user, key):
        raise HTTPException(status_code=403, detail=f"Platform permission required: {key}")
