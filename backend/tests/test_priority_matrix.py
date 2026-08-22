"""Scrum 20 — Procurement Priority Matrix endpoint.

Focus: auth/permission gate + response shape. The volatility (stdev of QoQ
should-cost change) and exposure (should-cost × trailing-4Q volume) math reuses
the costing engine, already covered by the engine determinism tests.
"""


def test_matrix_owner_ok_shape(client_as, tenant_a):
    c = client_as(tenant_a)
    r = c.get("/api/portfolio/priority-matrix", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 200
    body = r.json()
    assert set(["items", "reporting_currency", "volatility_threshold", "exposure_threshold"]).issubset(body)
    assert isinstance(body["items"], list)


def test_matrix_requires_membership(client_as, tenant_a, tenant_b):
    """A user with no membership on the team is refused (costing.view)."""
    c = client_as(tenant_b)
    r = c.get("/api/portfolio/priority-matrix", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 403


def test_matrix_unauthenticated(client, tenant_a):
    r = client.get("/api/portfolio/priority-matrix", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 401
