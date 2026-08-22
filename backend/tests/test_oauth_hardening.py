"""Scrum 9 — OAuth hardening: state validation (short-circuits before any Google
network call, so testable without mocking authlib) + refresh-token rotation."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.routers.auth import _hash_token
from app.models.refresh_token import RefreshToken


def test_callback_missing_state_400(client):
    r = client.get("/auth/callback", params={"code": "fake-code"})
    assert r.status_code == 400
    assert "state" in r.json()["detail"].lower()


def test_callback_state_mismatch_400(client):
    client.cookies.set("oauth_state", "real-state:some-verifier")
    r = client.get("/auth/callback", params={"code": "fake-code", "state": "wrong-state"})
    assert r.status_code == 400
    assert "state" in r.json()["detail"].lower()


@pytest.fixture
def refresh_row(db, tenant_a):
    """A live (unrevoked, unexpired) refresh token for tenant_a's user, plus the
    raw value that would have been sent as the ca_refresh cookie."""
    raw = "test-raw-refresh-token-" + uuid.uuid4().hex
    row = RefreshToken(
        user_id=tenant_a["user_id"], token_hash=_hash_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(row)
    db.commit()
    yield raw, row.id
    db.query(RefreshToken).filter(RefreshToken.user_id == tenant_a["user_id"]).delete()
    db.commit()


def test_refresh_rotates(client, refresh_row, db):
    raw, row_id = refresh_row
    client.cookies.set("ca_refresh", raw)
    r = client.post("/auth/refresh")
    assert r.status_code == 200, r.text

    db.expire_all()
    old = db.query(RefreshToken).filter(RefreshToken.id == row_id).first()
    assert old.revoked_at is not None
    assert old.replaced_by_id is not None

    new_cookie = r.cookies.get("ca_refresh")
    assert new_cookie and new_cookie != raw
    # the new token is live
    new_row = db.query(RefreshToken).filter(RefreshToken.id == old.replaced_by_id).first()
    assert new_row.revoked_at is None


def test_refresh_rejects_revoked_token(client, refresh_row, db):
    raw, row_id = refresh_row
    db.query(RefreshToken).filter(RefreshToken.id == row_id).update({"revoked_at": datetime.now(timezone.utc)})
    db.commit()
    client.cookies.set("ca_refresh", raw)
    r = client.post("/auth/refresh")
    assert r.status_code == 401


def test_refresh_rejects_expired_token(client, refresh_row, db):
    raw, row_id = refresh_row
    db.query(RefreshToken).filter(RefreshToken.id == row_id).update(
        {"expires_at": datetime.now(timezone.utc) - timedelta(days=1)}
    )
    db.commit()
    client.cookies.set("ca_refresh", raw)
    r = client.post("/auth/refresh")
    assert r.status_code == 401


def test_refresh_without_cookie_401(client):
    r = client.post("/auth/refresh")
    assert r.status_code == 401


def test_logout_revokes_refresh_token(client, refresh_row, db):
    raw, row_id = refresh_row
    client.cookies.set("ca_refresh", raw)
    r = client.post("/auth/logout")
    assert r.status_code == 200
    db.expire_all()
    row = db.query(RefreshToken).filter(RefreshToken.id == row_id).first()
    assert row.revoked_at is not None
