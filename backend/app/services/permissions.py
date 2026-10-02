import uuid
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.team import Team, TeamMembership
from app.models.rbac import Permission, RolePermission, PlanPermission, TeamMemberRole, UserPlatformRole
from app.models.user import User

# Categories a plain member may read on a team that has configured **no custom
# roles at all**. Only consulted by the membership-role fallback at the bottom of
# `has_permission`; a member holding any custom role never reaches it.
#
# This exists because the fallback used to split the key and allow every
# `*.view` / `*.export` action regardless of category — so a permission category
# added later was granted to members the moment it was created. `contracts.*`
# (contract prices, notice deadlines) was introduced precisely because it is
# more sensitive than a should-cost curve, and its migration deliberately did
# not grant it to Member, yet this path handed it over anyway on every team that
# never opened Role Settings, which is the default state of a new team.
#
# An allow-set makes a new category **opt-in**. The trade-off is deliberate: a
# new ordinary category is denied to bare members until it is named here, so
# adding one means adding a line. Denying an ordinary read by oversight is
# visible and recoverable; disclosing contract terms by oversight is neither.
MEMBER_READABLE_CATEGORIES = frozenset({
    "briefs", "content", "cost_models", "costing", "dimensions", "evolution",
    "formulas", "fx_rates", "indexes", "prices", "products", "scenarios",
    "squeeze", "suppliers", "volumes",
})

# Deliberately absent, so the reasoning is here rather than in a commit message:
#   contracts — Unit 6 (MON-1) grants it to Owner/Admin and the Dream plan and
#               explicitly not to Member. This is the category the fallback was
#               leaking.
#   support   — the staff keys are `support.reply` / `support.manage_content`;
#               neither is a view/export action, so the action check already
#               denies them. Named here so its absence reads as considered.


def has_permission(db: Session, user: User, team_id: uuid.UUID, key: str) -> bool:
    """
    Returns True if user is allowed to perform the action identified by key in team_id.
    Super admins bypass all checks.
    Falls back to membership.role if no custom roles are assigned — and that
    fallback is category-aware for a plain member, see
    MEMBER_READABLE_CATEGORIES.
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
    category, _, action = key.rpartition(".")
    if membership.role == "owner":
        return True
    elif membership.role == "admin":
        return action != "delete"
    else:  # member
        # Category as well as action — see MEMBER_READABLE_CATEGORIES. A key with
        # no category ("" from rpartition) is not in the set, so it is denied.
        return (action in ("view", "export")
                and category in MEMBER_READABLE_CATEGORIES)


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
