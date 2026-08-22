"""Scrum 22 — opportunistic buy windows endpoint.

Focus: auth/permission gate + response shape. The signal math (current
should-cost vs trailing-4Q average from calculate_evolution) reuses the costing
engine, already covered by the engine determinism tests.
"""


def test_buy_windows_owner_ok(client_as, tenant_a):
    c = client_as(tenant_a)
    r = c.get("/api/portfolio/buy-windows", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_buy_windows_requires_membership(client_as, tenant_a, tenant_b):
    c = client_as(tenant_b)
    r = c.get("/api/portfolio/buy-windows", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 403


def test_buy_windows_unauthenticated(client, tenant_a):
    r = client.get("/api/portfolio/buy-windows", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 401


def test_buy_window_single_model_missing_404(client_as, tenant_a):
    import uuid
    c = client_as(tenant_a)
    r = c.get(f"/api/portfolio/buy-windows/{uuid.uuid4()}")
    assert r.status_code in (403, 404)  # unknown model → not found (or refused before lookup)
