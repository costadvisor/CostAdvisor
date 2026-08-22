"""Scrum 24 — Alerts API (subscriptions, slack webhook, evaluate)."""
import uuid

from app.models.team import TeamMembership


def test_subscription_crud(client_as, tenant_a):
    c = client_as(tenant_a)
    tid = str(tenant_a["team_id"])
    r = c.post("/api/alerts/subscriptions", params={"team_id": tid},
               json={"trigger_type": "gap", "threshold_pct": 5})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["scope_label"] == "All products"
    assert len(c.get("/api/alerts/subscriptions", params={"team_id": tid}).json()) == 1
    u = c.put(f"/api/alerts/subscriptions/{sid}", json={"active": False, "threshold_pct": 8})
    assert u.status_code == 200 and u.json()["active"] is False and u.json()["threshold_pct"] == 8
    assert c.delete(f"/api/alerts/subscriptions/{sid}").status_code == 200


def test_invalid_scope_rejected(client_as, tenant_a):
    c = client_as(tenant_a)
    tid = str(tenant_a["team_id"])
    # index_move must not scope a product
    r = c.post("/api/alerts/subscriptions", params={"team_id": tid},
               json={"trigger_type": "index_move", "cost_model_id": str(uuid.uuid4())})
    assert r.status_code == 422
    # unknown trigger
    assert c.post("/api/alerts/subscriptions", params={"team_id": tid},
                  json={"trigger_type": "nope"}).status_code == 422


def test_slack_webhook_admin_only(client_as, tenant_a, user_factory, db):
    c = client_as(tenant_a)
    tid = str(tenant_a["team_id"])
    r = c.put("/api/alerts/slack-webhook", params={"team_id": tid},
              json={"slack_webhook_url": "https://hooks.slack.com/services/T/B/x"})
    assert r.status_code == 200 and r.json()["configured"] is True

    member = user_factory()
    db.add(TeamMembership(user_id=member["user_id"], team_id=tenant_a["team_id"], role="member"))
    db.commit()
    m = client_as(member)
    # member cannot set the webhook
    assert m.put("/api/alerts/slack-webhook", params={"team_id": tid},
                 json={"slack_webhook_url": "https://x/y"}).status_code == 403
    # member sees it's configured but not the URL
    g = m.get("/api/alerts/slack-webhook", params={"team_id": tid}).json()
    assert g["configured"] is True and g["slack_webhook_url"] is None
    # non-https rejected
    assert c.put("/api/alerts/slack-webhook", params={"team_id": tid},
                 json={"slack_webhook_url": "http://insecure"}).status_code == 422


def test_evaluate_owner_ok(client_as, tenant_a):
    c = client_as(tenant_a)
    r = c.post("/api/alerts/evaluate", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 200 and "alerts_created" in r.json()


def test_non_member_forbidden(client_as, tenant_a, tenant_b):
    c = client_as(tenant_b)
    tid = str(tenant_a["team_id"])
    assert c.get("/api/alerts/subscriptions", params={"team_id": tid}).status_code == 403
    assert c.post("/api/alerts/evaluate", params={"team_id": tid}).status_code == 403


def test_history_shape(client_as, tenant_a):
    c = client_as(tenant_a)
    r = c.get("/api/alerts/history", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 200 and isinstance(r.json(), list)
