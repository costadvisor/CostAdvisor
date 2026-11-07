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


def test_a_legacy_kg_quantity_on_a_tonne_product_counts_in_tonnes(client_as, tenant_a, db, monkeypatch):
    """Audit C-01: a row stored in kg before quantities were kept in the
    product's unit (10,000 kg on a per-tonne product) was read as 10,000 t by
    the benchmark exposure, the purchase history and the Excel export. All
    three read it as 10 t; the history names the product unit, since the page
    saves the figure back without one."""
    import io
    import uuid

    from openpyxl import load_workbook

    from app.models.actual_volume import ActualVolume
    from app.models.cost_model import CostModel
    from app.models.price_data import ActualPrice
    from app.models.product import Product
    from app.models.supplier import Supplier

    s = Supplier(team_id=tenant_a["team_id"], name="Kg Co")
    p = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"], created_by=tenant_a["user_id"], name="Kg P", unit="t")
    db.add_all([s, p])
    db.flush()
    cm = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=p.id, created_by=tenant_a["user_id"],
                   supplier_id=s.id, region="Europe", currency="EUR")
    db.add(cm)
    db.flush()
    db.add(ActualPrice(cost_model_id=cm.id, uploaded_by=tenant_a["user_id"], year=2026, quarter=1, price=110.0))
    db.add(ActualVolume(cost_model_id=cm.id, uploaded_by=tenant_a["user_id"], year=2026, quarter=1,
                        volume=10000, unit="kg"))
    db.commit()
    monkeypatch.setattr("app.routers.suppliers._should_cost_for_period", lambda *a: 100.0)
    c = client_as(tenant_a)

    r = c.get("/api/suppliers/benchmark", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["suppliers"] if x["supplier_id"] == s.id)
    assert row["exposure"] == 100.0                       # 10 over should-cost x 10 t

    r = c.get(f"/api/suppliers/{s.id}/purchase-history")
    assert r.status_code == 200, r.text
    [hist] = r.json()["models"][0]["rows"]
    assert (hist["volume"], hist["unit"]) == (10.0, "t")

    r = c.get(f"/api/suppliers/{s.id}/export-excel")
    assert r.status_code == 200, r.text
    ws = load_workbook(io.BytesIO(r.content)).worksheets[0]
    assert ws.cell(row=4, column=8).value == "Volume"
    assert ws.cell(row=5, column=8).value == 10.0
    assert ws.cell(row=5, column=9).value == 1100.0       # revenue: 110 x 10 t
