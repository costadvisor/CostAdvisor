"""Scrum 15 — negotiation brief deliverable.

Covers the server-side brief generation that backs the exportable PDF (the page
the client prints). Two concerns:

  * the calculation is correct — verdict (gap), should-cost, total impact, and
    drivers ranked by cost contribution;
  * the endpoint is locked down — authentication, authorization (`briefs.view`),
    and cross-tenant isolation via RLS.

Numbers in the happy-path model are chosen so the expected output is exact:

    base price          100  (margin 0% -> should-cost == indexed cost)
    component weights    Caustic 0.7, Energy 0.3
    base period 2024Q1   both indices = 100
    current 2024Q2       Caustic = 120 (+20%), Energy = 90 (-10%)

    indexed cost @ 2024Q2 = 100*0.7*1.2 + 100*0.3*0.9 = 84 + 27 = 111
    actual price @ 2024Q2 = 150  ->  gap = 150 - 111 = 39  (+39.0%)
    volume      @ 2024Q2 = 10    ->  total impact = 39 * 10 = 390
"""
from __future__ import annotations

import uuid

import pytest

from app.models.actual_volume import ActualVolume
from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.index_data import CommodityIndex, IndexValue
from app.models.price_data import ActualPrice
from app.models.product import Product
from app.models.rbac import Permission, Role, RolePermission, TeamMemberRole
from app.models.supplier import Supplier
from app.models.team import TeamMembership

ISOLATION_CODES = {403, 404}
REGION = "Europe"
BASE_Y, BASE_Q = 2024, 1
CUR_Y, CUR_Q = 2024, 2


@pytest.fixture
def brief_model(tenant_a, db):
    """A complete, runnable cost model for tenant_a (two indexed components,
    base + current index values, an actual price and a volume in the current
    quarter). Yields the CostModel."""
    suffix = uuid.uuid4().hex[:8]  # commodity_indexes.name is globally unique
    caustic = CommodityIndex(name=f"Caustic-{suffix}", currency="USD", unit="t")
    energy = CommodityIndex(name=f"Energy-{suffix}", currency="USD", unit="MWh")
    db.add_all([caustic, energy])
    db.flush()

    db.add_all([
        IndexValue(commodity_id=caustic.id, region=REGION, year=BASE_Y, quarter=BASE_Q, value=100),
        IndexValue(commodity_id=caustic.id, region=REGION, year=CUR_Y, quarter=CUR_Q, value=120),
        IndexValue(commodity_id=energy.id, region=REGION, year=BASE_Y, quarter=BASE_Q, value=100),
        IndexValue(commodity_id=energy.id, region=REGION, year=CUR_Y, quarter=CUR_Q, value=90),
    ])

    product = Product(
        id=uuid.uuid4(), team_id=tenant_a["team_id"], created_by=tenant_a["user_id"],
        name="Sodium Hydroxide", unit="kg",
    )
    supplier = Supplier(team_id=tenant_a["team_id"], name="Acme Chemicals", country="Germany")
    db.add_all([product, supplier])
    db.flush()

    cm = CostModel(
        id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=product.id,
        supplier_id=supplier.id, created_by=tenant_a["user_id"],
        region=REGION, currency="USD", destination_country="France",
    )
    db.add(cm)
    db.flush()

    # margin 0% keeps should-cost == indexed cost so the expected output is exact
    fv = FormulaVersion(
        cost_model_id=cm.id, base_price=100, base_year=BASE_Y, base_quarter=BASE_Q,
        formula_type="simple", margin_type="pct", margin_value=0,
    )
    db.add(fv)
    db.flush()
    db.add_all([
        FormulaComponent(formula_version_id=fv.id, label="Caustic feedstock", commodity_id=caustic.id, weight=0.7),
        FormulaComponent(formula_version_id=fv.id, label="Energy", commodity_id=energy.id, weight=0.3),
    ])
    # actual price above should-cost + a volume so total impact is computed.
    # Note: no actual at the base quarter, so base price stays the formula's 100.
    db.add(ActualPrice(cost_model_id=cm.id, uploaded_by=tenant_a["user_id"], year=CUR_Y, quarter=CUR_Q, price=150))
    db.add(ActualVolume(cost_model_id=cm.id, uploaded_by=tenant_a["user_id"], year=CUR_Y, quarter=CUR_Q, volume=10))
    db.commit()

    yield cm

    # commodity_indexes / index_values are global reference tables (no team
    # CASCADE). Drop the model first (cascades formula versions/components so no
    # FK still points at the commodities), then the reference rows. Everything
    # else cascades when user_factory drops the team.
    from sqlalchemy import text
    db.execute(text("DELETE FROM cost_models WHERE id = :id"), {"id": str(cm.id)})
    db.query(IndexValue).filter(IndexValue.commodity_id.in_([caustic.id, energy.id])).delete(synchronize_session=False)
    db.query(CommodityIndex).filter(CommodityIndex.id.in_([caustic.id, energy.id])).delete(synchronize_session=False)
    db.commit()


def test_brief_happy_path(client_as, tenant_a, brief_model):
    r = client_as(tenant_a).post("/api/costing/brief", json={"cost_model_id": str(brief_model.id)})
    assert r.status_code == 200, r.text
    b = r.json()

    assert b["product_name"] == "Sodium Hydroxide"
    assert b["supplier_name"] == "Acme Chemicals"
    assert b["currency"] == "USD"
    assert b["unit"] == "kg"

    assert b["current_should_cost"] == pytest.approx(111.0)
    assert b["current_actual_price"] == pytest.approx(150.0)
    assert b["gap"] == pytest.approx(39.0)          # verdict signal
    assert b["gap_pct"] == pytest.approx(39.0)
    assert b["volumes_missing"] is False
    assert b["total_impact"] == pytest.approx(390.0)  # gap * volume

    assert isinstance(b["narrative"], str) and b["narrative"].strip()


def test_brief_generation_is_audited(client_as, tenant_a, brief_model, db):
    """Scrum 10 — assembling the negotiation brief (the exportable deliverable)
    writes a security-relevant audit event attributed to the acting user."""
    from app.models.audit_log import AuditLog
    r = client_as(tenant_a).post("/api/costing/brief", json={"cost_model_id": str(brief_model.id)})
    assert r.status_code == 200, r.text
    db.expire_all()
    evt = (
        db.query(AuditLog)
        .filter(AuditLog.team_id == tenant_a["team_id"],
                AuditLog.event_type == "brief_generated",
                AuditLog.entity_id == str(brief_model.id))
        .first()
    )
    assert evt is not None
    assert evt.user_id == tenant_a["user_id"]


def test_brief_is_deterministic(client_as, tenant_a, brief_model):
    """Scrum 11 — the costing engine must produce identical output for identical
    input on repeated runs (no nondeterminism in the calc path). Compare two
    fresh calls field-by-field, excluding only the AI narrative (Ollama text)."""
    c = client_as(tenant_a)
    a = c.post("/api/costing/brief", json={"cost_model_id": str(brief_model.id)}).json()
    b = c.post("/api/costing/brief", json={"cost_model_id": str(brief_model.id)}).json()
    a.pop("narrative", None)
    b.pop("narrative", None)
    assert a == b
    # And the numeric anchors are exactly what the inputs imply (regression guard)
    assert a["current_should_cost"] == pytest.approx(111.0)
    assert a["gap"] == pytest.approx(39.0)
    assert a["total_impact"] == pytest.approx(390.0)


def test_evolution_is_deterministic(client_as, tenant_a, brief_model):
    """Scrum 11 — repeated evolution calls return identical output."""
    c = client_as(tenant_a)
    a = c.post("/api/costing/evolution", json={"cost_model_id": str(brief_model.id)})
    b = c.post("/api/costing/evolution", json={"cost_model_id": str(brief_model.id)})
    assert a.status_code == 200 and b.status_code == 200, a.text
    assert a.json() == b.json()


def test_squeeze_is_deterministic(client_as, tenant_a, brief_model):
    """Scrum 11 — repeated squeeze calls return identical output."""
    c = client_as(tenant_a)
    a = c.post("/api/costing/squeeze", json={"cost_model_id": str(brief_model.id)})
    b = c.post("/api/costing/squeeze", json={"cost_model_id": str(brief_model.id)})
    assert a.status_code == 200 and b.status_code == 200, a.text
    assert a.json() == b.json()


def test_brief_drivers_ranked(client_as, tenant_a, brief_model):
    """Drivers are ranked by absolute cost contribution, each with the right
    direction and index change — the 'ranked drivers' deliverable."""
    b = client_as(tenant_a).post("/api/costing/brief", json={"cost_model_id": str(brief_model.id)}).json()
    drivers = b["drivers"]
    assert len(drivers) == 2

    # caustic (cost 84) ranks above energy (cost 27)
    assert [d["component_label"] for d in drivers] == ["Caustic feedstock", "Energy"]
    assert drivers[0]["component_cost"] == pytest.approx(84.0)
    assert drivers[0]["index_change_pct"] == pytest.approx(20.0)
    assert drivers[0]["direction"] == "up"
    assert drivers[1]["component_cost"] == pytest.approx(27.0)
    assert drivers[1]["index_change_pct"] == pytest.approx(-10.0)
    assert drivers[1]["direction"] == "down"

    costs = [abs(d["component_cost"]) for d in drivers]
    assert costs == sorted(costs, reverse=True)


def test_brief_requires_authentication(client, brief_model):
    r = client.post("/api/costing/brief", json={"cost_model_id": str(brief_model.id)})
    assert r.status_code == 401, r.text


def test_brief_cross_tenant_isolation(client_as, tenant_b, brief_model):
    """A user outside the owning team cannot generate the brief — RLS hides the
    model so the lookup 404s (or 403s at the permission layer)."""
    r = client_as(tenant_b).post("/api/costing/brief", json={"cost_model_id": str(brief_model.id)})
    assert r.status_code in ISOLATION_CODES, r.text


def test_brief_nonexistent_model(client_as, tenant_a):
    r = client_as(tenant_a).post("/api/costing/brief", json={"cost_model_id": str(uuid.uuid4())})
    assert r.status_code == 404, r.text


def test_brief_requires_briefs_view_permission(client_as, user_factory, tenant_a, brief_model, db):
    """A same-team member whose role lacks `briefs.view` is denied (403), even
    though RLS lets them see the model — isolates authz from tenant isolation."""
    member = user_factory()
    db.add(TeamMembership(user_id=member["user_id"], team_id=tenant_a["team_id"], role="member"))
    role = Role(team_id=tenant_a["team_id"], name=f"NoBriefs-{uuid.uuid4().hex[:6]}")
    db.add(role)
    db.flush()
    # give the role one unrelated permission so it's a real, non-empty role that
    # simply doesn't include briefs.view
    other = db.query(Permission).filter(Permission.key != "briefs.view").first()
    db.add(RolePermission(role_id=role.id, permission_id=other.id))
    db.add(TeamMemberRole(user_id=member["user_id"], team_id=tenant_a["team_id"], role_id=role.id))
    db.commit()

    r = client_as(member).post("/api/costing/brief", json={"cost_model_id": str(brief_model.id)})
    assert r.status_code == 403, r.text
