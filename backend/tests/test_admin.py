"""Admin console + impersonation security tests (Scrum 8).

Covers the security-relevant guarantees the admin console claims:
super-admin-only access, self-exclusion from the user list, the full
impersonation start/stop flow with its cookies, and audit-log wiring.
"""
from __future__ import annotations

import uuid

from app.models.audit_log import AuditLog


def _event(db, event_type: str, entity_id: uuid.UUID) -> AuditLog | None:
    return (
        db.query(AuditLog)
        .filter(AuditLog.event_type == event_type, AuditLog.entity_id == str(entity_id))
        .order_by(AuditLog.timestamp.desc())
        .first()
    )


# ── Authentication / Authorization ────────────────────────────────────────────

def test_unauthenticated_gets_401(client):
    r = client.get("/api/admin/users")
    assert r.status_code == 401, r.text


def test_non_super_admin_gets_403(client_as, user_factory):
    normal = user_factory(is_super_admin=False)
    r = client_as(normal).get("/api/admin/users")
    assert r.status_code == 403, r.text
    assert "Super admin" in r.json()["detail"]


def test_super_admin_can_list_users(client_as, user_factory):
    admin = user_factory(is_super_admin=True)
    r = client_as(admin).get("/api/admin/users")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_super_admin_excluded_from_own_list(client_as, user_factory):
    admin = user_factory(is_super_admin=True)
    other = user_factory(is_super_admin=False)
    r = client_as(admin).get("/api/admin/users")
    assert r.status_code == 200, r.text
    ids = {str(u["id"]) for u in r.json()}
    assert str(admin["user_id"]) not in ids       # cannot see/act on self
    assert str(other["user_id"]) in ids            # but sees other tenants


# ── Impersonation ──────────────────────────────────────────────────────────────

def test_impersonate_sets_cookies_and_audits(client_as, user_factory, db):
    admin = user_factory(is_super_admin=True)
    target = user_factory(is_super_admin=False)
    c = client_as(admin)

    r = c.post(f"/api/admin/impersonate/{target['user_id']}")
    assert r.status_code == 200, r.text
    assert r.json()["target_email"]
    # Impersonation cookies issued
    assert r.cookies.get("ca_admin_token")           # original admin identity stashed
    assert r.cookies.get("ca_impersonating") == "1"  # frontend flag (readable)

    db.expire_all()
    evt = _event(db, "admin_impersonate_start", target["user_id"])
    assert evt is not None
    assert evt.user_id == admin["user_id"]           # attributed to the acting admin


def test_cannot_impersonate_super_admin(client_as, user_factory):
    admin = user_factory(is_super_admin=True)
    other_admin = user_factory(is_super_admin=True)
    r = client_as(admin).post(f"/api/admin/impersonate/{other_admin['user_id']}")
    assert r.status_code == 400, r.text
    assert "super admin" in r.json()["detail"].lower()


def test_cannot_impersonate_while_already_impersonating(client_as, user_factory):
    admin = user_factory(is_super_admin=True)
    target1 = user_factory(is_super_admin=False)
    target2 = user_factory(is_super_admin=False)
    c = client_as(admin)
    assert c.post(f"/api/admin/impersonate/{target1['user_id']}").status_code == 200
    # Nested impersonation is blocked: while impersonating, ca_token is the
    # target's (a non-admin), so require_super_admin rejects with 403 before the
    # explicit "already impersonating" 400 guard is even reached. Either way the
    # second start cannot succeed.
    r = c.post(f"/api/admin/impersonate/{target2['user_id']}")
    assert r.status_code in (400, 403), r.text


def test_stop_impersonate_restores_and_audits(client_as, user_factory, db):
    admin = user_factory(is_super_admin=True)
    target = user_factory(is_super_admin=False)
    c = client_as(admin)
    assert c.post(f"/api/admin/impersonate/{target['user_id']}").status_code == 200

    r = c.post("/api/admin/stop-impersonate")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "restored"

    db.expire_all()
    evt = _event(db, "admin_impersonate_stop", target["user_id"])
    assert evt is not None
    assert evt.user_id == admin["user_id"]


def test_stop_impersonate_without_session_400(client_as, user_factory):
    admin = user_factory(is_super_admin=True)
    r = client_as(admin).post("/api/admin/stop-impersonate")
    assert r.status_code == 400, r.text
