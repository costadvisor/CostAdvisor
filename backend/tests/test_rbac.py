"""Scrum 8b — RBAC + plans.

Verifies the has_permission() decision order (super-admin → plan ceiling →
custom roles → membership.role fallback), the plan ceiling overriding even an
owner, custom roles replacing the fallback, the API-level plan-ceiling
validation on role creation, and platform-permission resolution.
"""
from __future__ import annotations

import pathlib
import uuid

import pytest

from app.models.user import User
from app.models.team import Team, TeamMembership
from app.models.rbac import Permission, Role, RolePermission, Plan, TeamMemberRole, UserPlatformRole
from app.services.permissions import (
    MEMBER_READABLE_CATEGORIES, has_permission, has_platform_permission,
)


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


def test_the_member_fallback_does_not_hand_over_contract_terms(db, user_factory, tenant_a):
    """The fallback split the key and allowed every `*.view` action regardless of
    category, so `contracts.*` — added precisely because contract prices and
    notice deadlines are more sensitive than a should-cost curve, and granted to
    Owner/Admin and deliberately not to Member — was handed to a plain member on
    every team that never configured roles. Which is a new team's default state.
    """
    member = _user(db, user_factory()["user_id"])
    db.add(TeamMembership(user_id=member.id, team_id=tenant_a["team_id"], role="member"))
    db.commit()
    _perm(db, "contracts.view")   # skip if this DB predates the category
    assert has_permission(db, member, tenant_a["team_id"], "contracts.view") is False
    assert has_permission(db, member, tenant_a["team_id"], "contracts.edit") is False
    # The separation is only worth anything if ordinary reads still work.
    assert has_permission(db, member, tenant_a["team_id"], "costing.view") is True
    assert has_permission(db, member, tenant_a["team_id"], "products.view") is True


def test_a_member_with_a_custom_role_never_reaches_the_fallback(db, user_factory, tenant_a):
    """The narrowing must not become a second ceiling: a team that granted
    contracts through a real role still gets it. `has_permission` returns on the
    custom-role branch, so the category set is not consulted at all."""
    member = _user(db, user_factory()["user_id"])
    db.add(TeamMembership(user_id=member.id, team_id=tenant_a["team_id"], role="member"))
    role = Role(team_id=tenant_a["team_id"], name=f"Buyer-{uuid.uuid4().hex[:6]}")
    db.add(role); db.flush()
    db.add(RolePermission(role_id=role.id, permission_id=_perm(db, "contracts.view").id))
    db.add(TeamMemberRole(user_id=member.id, team_id=tenant_a["team_id"], role_id=role.id))
    db.commit()
    assert has_permission(db, member, tenant_a["team_id"], "contracts.view") is True


def test_every_readable_category_is_a_real_permission_category(db):
    """A typo in the set is silent — it would deny a whole category of reads to
    every bare member with nothing to show for it. Pinned against the seeded
    permissions rather than a second hand-written list."""
    seeded = {
        key.rpartition(".")[0]
        for (key,) in db.query(Permission.key).all()
    }
    if not seeded:
        pytest.skip("no permissions seeded in this DB")
    unknown = MEMBER_READABLE_CATEGORIES - seeded
    assert not unknown, f"not real permission categories: {sorted(unknown)}"


def test_a_new_category_is_denied_to_bare_members_until_it_is_named(db, user_factory, tenant_a):
    """The whole point of the allow-set: opt-in, not opt-out. A category that
    exists in the permissions table but is not in the set must not be readable,
    or the next sensitive key repeats `contracts`' history."""
    member = _user(db, user_factory()["user_id"])
    db.add(TeamMembership(user_id=member.id, team_id=tenant_a["team_id"], role="member"))
    db.commit()
    seeded = {key.rpartition(".")[0] for (key,) in db.query(Permission.key).all()}
    unlisted = seeded - MEMBER_READABLE_CATEGORIES
    if not unlisted:
        pytest.skip("every seeded category is member-readable")
    for category in sorted(unlisted):
        assert has_permission(
            db, member, tenant_a["team_id"], f"{category}.view") is False, category


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


def test_every_gated_permission_key_actually_exists(db):
    """H7. A permission key that is not a row can never be granted.

    `has_permission` applies the plan ceiling BEFORE roles and denies any key
    absent from the plan, and no role can grant a permission that does not exist
    — so such a key resolves only through the membership fallback, which fires
    only for a member with no custom roles at all. It therefore looks fine on a
    plan-less team with plain owners and denies everyone else.

    That is exactly what `costing.edit` did: gated on by four call sites
    (radar dismiss, note deletion, the negotiation flag, the Slack webhook
    reveal) while the `costing` category holds only `view`. On the dev data 24 of
    25 teams have no plan, so owners passed and nobody noticed.

    This scans the routers rather than listing the four, so the next invented key
    fails here instead of in production.
    """
    import re

    routers = pathlib.Path(__file__).resolve().parents[1] / "app" / "routers"
    known = {k for (k,) in db.query(Permission.key).all()}

    pattern = re.compile(
        r'(?:require_permission|has_permission)\([^)]*?"([a-z_]+\.[a-z_]+)"',
        re.S)
    referenced = {}
    for path in sorted(routers.glob("*.py")):
        for key in pattern.findall(path.read_text(encoding="utf-8")):
            referenced.setdefault(key, set()).add(path.name)

    assert referenced, "the scan found no permission checks at all — regex is wrong"
    missing = {k: sorted(v) for k, v in referenced.items() if k not in known}
    assert not missing, (
        "these permission keys are gated on but do not exist as rows, so they "
        f"can only ever pass via the membership fallback: {missing}"
    )
