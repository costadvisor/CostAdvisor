"""Web push subscriptions (PWA extras).

Nothing here talks to a real push relay. What is worth testing is the ownership
boundary and the lifecycle rules — a subscription is per-user with no RLS
behind it, so the router's filters are the entire boundary and they have to be
right.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.push import PushSubscription
from app.services import push as push_service


def _sub_body(endpoint=None):
    return {
        "endpoint": endpoint or f"https://push.example.test/{uuid.uuid4().hex}",
        "p256dh": "BPtestkeytestkeytestkey",
        "auth": "authsecret",
        "user_agent": "Chrome/1.0 Windows",
    }


def _cleanup(db, user_ids):
    bypass_rls_var.set(True)
    for uid in user_ids:
        db.execute(text("DELETE FROM push_subscriptions WHERE user_id = :u"), {"u": str(uid)})
    db.commit()


def test_subscribe_lists_and_never_returns_the_endpoint(db, tenant_a, client_as):
    """The endpoint is a capability URL — whoever holds it can push to that
    browser — so it must not come back out of the API."""
    c = client_as(tenant_a)
    try:
        body = _sub_body()
        r = c.post("/api/push/subscriptions", json=body)
        assert r.status_code == 201, r.text
        assert "endpoint" not in r.json()
        assert "p256dh" not in r.json() and "auth" not in r.json()

        r = c.get("/api/push/subscriptions")
        assert r.status_code == 200 and len(r.json()) == 1
        assert r.json()[0]["user_agent"] == "Chrome/1.0 Windows"
        assert "endpoint" not in r.json()[0]
    finally:
        _cleanup(db, [tenant_a["user_id"]])


def test_resubscribing_the_same_browser_updates_rather_than_duplicates(db, tenant_a, client_as):
    """A browser returns the same endpoint every time. Inserting per visit
    would notify that one device once per row."""
    c = client_as(tenant_a)
    body = _sub_body()
    try:
        first = c.post("/api/push/subscriptions", json=body).json()
        body["user_agent"] = "Chrome/2.0 Windows"
        second = c.post("/api/push/subscriptions", json=body).json()
        assert first["id"] == second["id"]
        listed = c.get("/api/push/subscriptions").json()
        assert len(listed) == 1 and listed[0]["user_agent"] == "Chrome/2.0 Windows"
    finally:
        _cleanup(db, [tenant_a["user_id"]])


def test_a_shared_browser_moves_to_whoever_signed_in(db, tenant_a, tenant_b, client_as):
    """The endpoint is unique per browser, not per person. If someone else
    signs in on the same machine the device belongs to them now — otherwise
    the insert would violate the unique constraint and 500."""
    body = _sub_body()
    try:
        client_as(tenant_a).post("/api/push/subscriptions", json=body)
        r = client_as(tenant_b).post("/api/push/subscriptions", json=body)
        assert r.status_code == 201, r.text
        assert client_as(tenant_a).get("/api/push/subscriptions").json() == []
        assert len(client_as(tenant_b).get("/api/push/subscriptions").json()) == 1
    finally:
        _cleanup(db, [tenant_a["user_id"], tenant_b["user_id"]])


def test_one_user_cannot_delete_anothers_device(db, tenant_a, tenant_b, client_as):
    """There is no RLS on this table, so the router's user filter is the whole
    boundary."""
    try:
        sub_id = client_as(tenant_a).post("/api/push/subscriptions", json=_sub_body()).json()["id"]
        r = client_as(tenant_b).delete(f"/api/push/subscriptions/{sub_id}")
        assert r.status_code == 404, "someone else's row is not found, not forbidden-but-visible"
        assert len(client_as(tenant_a).get("/api/push/subscriptions").json()) == 1
    finally:
        _cleanup(db, [tenant_a["user_id"], tenant_b["user_id"]])


def test_unsubscribe_by_endpoint_is_scoped_and_idempotent(db, tenant_a, tenant_b, client_as):
    body = _sub_body()
    try:
        client_as(tenant_a).post("/api/push/subscriptions", json=body)

        # Another user naming the same endpoint removes nothing.
        assert client_as(tenant_b).post(
            "/api/push/unsubscribe", json={"endpoint": body["endpoint"]}).status_code == 204
        assert len(client_as(tenant_a).get("/api/push/subscriptions").json()) == 1

        assert client_as(tenant_a).post(
            "/api/push/unsubscribe", json={"endpoint": body["endpoint"]}).status_code == 204
        assert client_as(tenant_a).get("/api/push/subscriptions").json() == []

        # Again: the caller's goal is "this browser is not registered", and it
        # already is not.
        assert client_as(tenant_a).post(
            "/api/push/unsubscribe", json={"endpoint": body["endpoint"]}).status_code == 204
    finally:
        _cleanup(db, [tenant_a["user_id"], tenant_b["user_id"]])


def test_deleting_a_user_takes_their_devices(db, tenant_a, user_factory, client_as):
    """CASCADE, not an orphan: a subscription outliving its user would keep
    pushing to a device nobody can turn off."""
    victim = user_factory()
    client_as(victim).post("/api/push/subscriptions", json=_sub_body())
    bypass_rls_var.set(True)
    assert db.query(PushSubscription).filter(
        PushSubscription.user_id == victim["user_id"]).count() == 1
    # Teardown in FK order: user_factory also creates a team whose created_by
    # points back at this user, so the user cannot go first.
    db.execute(text("DELETE FROM team_memberships WHERE user_id = :u"), {"u": str(victim["user_id"])})
    db.execute(text("DELETE FROM teams WHERE created_by = :u"), {"u": str(victim["user_id"])})
    db.execute(text("DELETE FROM users WHERE id = :u"), {"u": str(victim["user_id"])})
    db.commit()
    assert db.query(PushSubscription).filter(
        PushSubscription.user_id == victim["user_id"]).count() == 0


def test_config_reports_disabled_without_keys(db, tenant_a, client_as, monkeypatch):
    monkeypatch.setattr(push_service, "push_configured", lambda: False)
    monkeypatch.setattr("app.routers.push.push_configured", lambda: False)
    r = client_as(tenant_a).get("/api/push/config")
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "public_key": None}
    # And the test endpoint refuses rather than silently doing nothing.
    assert client_as(tenant_a).post("/api/push/test").status_code == 400


def test_send_to_user_is_a_no_op_when_unconfigured(db, tenant_a, monkeypatch):
    """Best-effort by design: an unconfigured server must not raise into the
    alert that was trying to announce itself."""
    monkeypatch.setattr(push_service, "push_configured", lambda: False)
    assert push_service.send_to_user(db, tenant_a["user_id"], "t", "b") == 0


def test_a_dead_subscription_is_pruned_not_retried(db, tenant_a, monkeypatch):
    """410 Gone means the browser is finished with it. Retrying forever is how
    a table fills with uninstalled browsers."""
    class _Resp:
        status_code = 410

    class _Exc(Exception):
        response = _Resp()

    sub = PushSubscription(
        user_id=tenant_a["user_id"], endpoint=f"https://push.example.test/{uuid.uuid4().hex}",
        p256dh_key="k", auth_key="a",
    )
    bypass_rls_var.set(True)
    db.add(sub)
    db.flush()
    sub_id = sub.id

    def _boom(**kwargs):
        raise _Exc("gone")

    monkeypatch.setattr(push_service, "push_configured", lambda: True)
    monkeypatch.setattr(push_service, "webpush", _boom)
    monkeypatch.setattr(push_service, "WebPushException", _Exc)

    assert push_service.send_to_subscription(db, sub, {"title": "t"}) is False
    db.flush()
    assert db.query(PushSubscription).filter(PushSubscription.id == sub_id).first() is None


def test_a_transient_failure_keeps_the_subscription(db, tenant_a, monkeypatch):
    class _Resp:
        status_code = 503

    class _Exc(Exception):
        response = _Resp()

    sub = PushSubscription(
        user_id=tenant_a["user_id"], endpoint=f"https://push.example.test/{uuid.uuid4().hex}",
        p256dh_key="k", auth_key="a",
    )
    bypass_rls_var.set(True)
    db.add(sub)
    db.flush()
    try:
        def _boom(**kwargs):
            raise _Exc("service unavailable")

        monkeypatch.setattr(push_service, "push_configured", lambda: True)
        monkeypatch.setattr(push_service, "webpush", _boom)
        monkeypatch.setattr(push_service, "WebPushException", _Exc)

        assert push_service.send_to_subscription(db, sub, {"title": "t"}) is False
        assert db.query(PushSubscription).filter(PushSubscription.id == sub.id).first() is not None
    finally:
        _cleanup(db, [tenant_a["user_id"]])


def test_push_is_an_allowed_alert_channel():
    """A third value on the existing channel, not a parallel system."""
    from app.schemas.alerts import CHANNELS
    assert CHANNELS == {"email", "slack", "push"}


def test_unauthenticated_is_401(client):
    assert client.get("/api/push/subscriptions").status_code == 401
    assert client.get("/api/push/config").status_code == 401
