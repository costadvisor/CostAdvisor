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


def test_benchmark_prices_every_quarter_inside_one_index_memo(client_as, tenant_a, db, monkeypatch):
    """Audit C-31: each priced quarter is its own should-cost call. Run outside a
    shared memo, every call re-read the same index values (~15 s on the demo
    team); inside one, the repeats are answered from it (~3 s)."""
    import uuid

    from app.models.cost_model import CostModel
    from app.models.price_data import ActualPrice
    from app.models.product import Product
    from app.models.supplier import Supplier
    from app.services.data_resolver import index_lookup_cache_active

    s = Supplier(team_id=tenant_a["team_id"], name="Memo Co")
    p = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"], created_by=tenant_a["user_id"], name="Memo P", unit="t")
    db.add_all([s, p])
    db.flush()
    cm = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=p.id, created_by=tenant_a["user_id"],
                   supplier_id=s.id, region="Europe", currency="EUR")
    db.add(cm)
    db.flush()
    db.add_all([ActualPrice(cost_model_id=cm.id, uploaded_by=tenant_a["user_id"], year=2026, quarter=q, price=110.0)
                for q in (1, 2)])
    db.commit()

    seen = []

    def _stub(db_, model, year, quarter):
        seen.append(index_lookup_cache_active())
        return 100.0

    monkeypatch.setattr("app.routers.suppliers._should_cost_for_period", _stub)
    r = client_as(tenant_a).get("/api/suppliers/benchmark", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["suppliers"] if x["supplier_id"] == s.id)
    assert row["avg_gap_pct"] == 10.0
    assert seen == [True, True]
