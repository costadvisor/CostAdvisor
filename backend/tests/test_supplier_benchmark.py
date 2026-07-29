"""Scrum 23 — supplier benchmarking endpoint.

Focus: the owner/admin gate (Scrum 23 says "visible to owner/admin only") and
the response shape. The gap math itself is the same pipeline as the costing
engine / Excel export, already covered by the engine determinism tests.
"""
from app.models.team import TeamMembership


def test_benchmark_owner_ok(client_as, tenant_a):
    c = client_as(tenant_a)
    r = c.get("/api/suppliers/benchmark", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 200
    body = r.json()
    assert "suppliers" in body and isinstance(body["suppliers"], list)


def test_benchmark_member_forbidden(client_as, tenant_a, user_factory, db):
    """A plain member of the team cannot see benchmarking (owner/admin only)."""
    member = user_factory()
    db.add(TeamMembership(user_id=member["user_id"], team_id=tenant_a["team_id"], role="member"))
    db.commit()
    c = client_as(member)
    r = c.get("/api/suppliers/benchmark", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 403


def test_benchmark_admin_ok(client_as, tenant_a, user_factory, db):
    """An admin of the team may see benchmarking."""
    admin = user_factory()
    db.add(TeamMembership(user_id=admin["user_id"], team_id=tenant_a["team_id"], role="admin"))
    db.commit()
    c = client_as(admin)
    r = c.get("/api/suppliers/benchmark", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 200


def test_benchmark_non_member_forbidden(client_as, tenant_a, tenant_b):
    """A user with no membership on the team is refused."""
    c = client_as(tenant_b)
    r = c.get("/api/suppliers/benchmark", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 403
