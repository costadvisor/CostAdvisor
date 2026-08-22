"""Scrum 8b — RBAC + plans.

Verifies the has_permission() decision order (super-admin → plan ceiling →
custom roles → membership.role fallback), the plan ceiling overriding even an
owner, custom roles replacing the fallback, the API-level plan-ceiling
validation on role creation, and platform-permission resolution.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.user import User
from app.models.team import Team, TeamMembership
from app.models.rbac import Permission, Role, RolePermission, Plan, TeamMemberRole, UserPlatformRole
from app.services.permissions import has_permission, has_platform_permission


def _user(db, uid):
    return db.query(User).filter(User.id == uid).first()


def _perm(db, key):
    p = db.query(Permission).filter(Permission.key == key).first()
    if p is None:
        pytest.skip(f"permission {key} not seeded in this DB")
    return p


# ── has_permission() decision order ───────────────────────────────────────────

def test_super_admin_bypasses_everything(db, user_factory, tenant_a):
    sa = _user(db, user_factory(is_super_admin=True)["user_id"])
    # No membership in tenant_a's team, yet super-admin is allowed anything
    assert has_permission(db, sa, tenant_a["team_id"], "cost_models.delete") is True


def test_owner_fallback_grants_all(db, tenant_a):
    owner = _user(db, tenant_a["user_id"])  # user_factory makes them team owner
    assert has_permission(db, owner, tenant_a["team_id"], "products.delete") is True


def test_member_fallback_is_view_export_only(db, user_factory, tenant_a):
    member = _user(db, user_factory()["user_id"])
    db.add(TeamMembership(user_id=member.id, team_id=tenant_a["team_id"], role="member"))
    db.commit()
    assert has_permission(db, member, tenant_a["team_id"], "products.view") is True
    assert has_permission(db, member, tenant_a["team_id"], "products.export") is True
    assert has_permission(db, member, tenant_a["team_id"], "products.edit") is False
    assert has_permission(db, member, tenant_a["team_id"], "products.delete") is False


def test_custom_role_replaces_membership_fallback(db, user_factory, tenant_a):
    member = _user(db, user_factory()["user_id"])
    db.add(TeamMembership(user_id=member.id, team_id=tenant_a["team_id"], role="member"))
    role = Role(team_id=tenant_a["team_id"], name=f"Editor-{uuid.uuid4().hex[:6]}")
    db.add(role); db.flush()
    db.add(RolePermission(role_id=role.id, permission_id=_perm(db, "cost_models.edit").id))
    db.add(TeamMemberRole(user_id=member.id, team_id=tenant_a["team_id"], role_id=role.id))
    db.commit()
    # Role grants exactly cost_models.edit...
    assert has_permission(db, member, tenant_a["team_id"], "cost_models.edit") is True
    # ...and the custom role REPLACES the member fallback, so view (which a bare
    # member would have) is now denied because the role doesn't include it.
    assert has_permission(db, member, tenant_a["team_id"], "cost_models.view") is False


def test_plan_ceiling_caps_even_owner(db, tenant_a):
    free = db.query(Plan).filter(Plan.name == "Free").first()
    if free is None:
        pytest.skip("Free plan not seeded")
    team = db.query(Team).filter(Team.id == tenant_a["team_id"]).first()
    team.plan_id = free.id
    db.commit()
    owner = _user(db, tenant_a["user_id"])
    # Free = view/export only → an edit permission is denied even for the owner
    assert has_permission(db, owner, tenant_a["team_id"], "products.view") is True
    assert has_permission(db, owner, tenant_a["team_id"], "cost_models.edit") is False


# ── API: plan-ceiling validation on role creation ─────────────────────────────

def test_role_creation_blocked_by_plan_ceiling(db, client_as, tenant_a):
    free = db.query(Plan).filter(Plan.name == "Free").first()
    if free is None:
        pytest.skip("Free plan not seeded")
    team = db.query(Team).filter(Team.id == tenant_a["team_id"]).first()
    team.plan_id = free.id
    db.commit()
    edit_perm = _perm(db, "cost_models.edit")  # not in the Free plan
    r = client_as(tenant_a).post(
        f"/api/teams/{tenant_a['team_id']}/roles",
        json={"name": f"Role-{uuid.uuid4().hex[:6]}", "description": "x",
              "permission_ids": [str(edit_perm.id)]},
    )
    assert r.status_code == 400, r.text
    assert "plan" in r.json()["detail"].lower()


# ── platform permissions ──────────────────────────────────────────────────────

def test_platform_permission_via_user_platform_role(db, user_factory):
    chemist = db.query(Role).filter(Role.team_id == None, Role.name == "Chemist").first()  # noqa: E711
    if chemist is None:
        pytest.skip("Chemist platform role not seeded")
    u = _user(db, user_factory()["user_id"])
    assert has_platform_permission(db, u, "formulas.edit") is False  # not assigned yet
    db.add(UserPlatformRole(user_id=u.id, role_id=chemist.id))
    db.commit()
    assert has_platform_permission(db, u, "formulas.edit") is True
    assert has_platform_permission(db, u, "products.edit") is False  # Chemist scope is formulas.*
