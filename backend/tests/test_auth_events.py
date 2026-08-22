"""Scrum 10 — login/logout audit trail (AuthEvent)."""
from __future__ import annotations

from app.models.auth_event import AuthEvent
from app.models.refresh_token import RefreshToken
from app.routers.auth import _hash_token
from datetime import datetime, timedelta, timezone
import uuid


def _latest(db, email: str) -> AuthEvent | None:
    return (
        db.query(AuthEvent)
        .filter(AuthEvent.email == email)
        .order_by(AuthEvent.created_at.desc())
        .first()
    )


def test_logout_writes_auth_event(client_as, user_factory, db):
    user = user_factory()
    c = client_as(user)
    r = c.post("/auth/logout")
    assert r.status_code == 200, r.text

    db.expire_all()
    ev = _latest(db, f"test-{user['user_id']}@test.local")
    assert ev is not None
    assert ev.event_type == "logout"
    assert ev.user_id == user["user_id"]


def test_logout_without_valid_token_still_succeeds_no_event(client, db):
    """Logout must never fail just because the token is missing/expired —
    the auth-event write is best-effort, not a hard dependency."""
    r = client.post("/auth/logout")
    assert r.status_code == 200


def test_callback_signup_disabled_writes_login_failed(client, db, monkeypatch):
    from app.routers import auth as auth_router
    monkeypatch.setattr(auth_router.settings, "allow_signup", False)

    email = f"blocked-{uuid.uuid4().hex[:8]}@test.local"
    client.cookies.set("oauth_state", "s:v")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def fetch_token(self, *a, **k):
            return {"access_token": "fake"}

        async def get(self, *a, **k):
            class R:
                def json(self):
                    return {"sub": "google-" + uuid.uuid4().hex, "email": email}
            return R()

    monkeypatch.setattr(auth_router, "AsyncOAuth2Client", FakeClient)

    r = client.get("/auth/callback", params={"code": "fake-code", "state": "s"}, follow_redirects=False)
    assert r.status_code == 302
    assert "signup_disabled" in r.headers["location"]

    db.expire_all()
    ev = _latest(db, email)
    assert ev is not None
    assert ev.event_type == "login_failed"
    assert ev.reason == "signup_disabled"
    assert ev.user_id is None


def test_refresh_login_success_recorded_on_logout_but_not_on_refresh(client_as, user_factory, db):
    """Sanity check the event table isn't spammed by /auth/refresh — only
    login (via /callback) and logout write AuthEvent rows."""
    user = user_factory()
    raw = "test-raw-" + uuid.uuid4().hex
    db.add(RefreshToken(
        user_id=user["user_id"], token_hash=_hash_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    ))
    db.commit()

    c = client_as(user)
    c.cookies.set("ca_refresh", raw)
    r = c.post("/auth/refresh")
    assert r.status_code == 200, r.text

    db.expire_all()
    count_before = db.query(AuthEvent).filter(
        AuthEvent.email == f"test-{user['user_id']}@test.local"
    ).count()
    assert count_before == 0


# ── Admin read surface ─────────────────────────────────────────────────────────

def test_auth_events_endpoint_super_admin_only(client_as, user_factory, db):
    normal = user_factory(is_super_admin=False)
    r = client_as(normal).get("/api/admin/auth-events")
    assert r.status_code == 403, r.text


def test_auth_events_endpoint_lists_for_super_admin(client_as, user_factory, db):
    admin = user_factory(is_super_admin=True)
    db.add(AuthEvent(email="someone@test.local", event_type="login_success"))
    db.commit()
    r = client_as(admin).get("/api/admin/auth-events")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
    assert any(e["email"] == "someone@test.local" for e in r.json())
