"""Composite / calculated indexes — value computed live from other indexes."""
import uuid

import pytest

from app.models.index_data import CommodityIndex, IndexValue
from app.services.data_resolver import get_single_index_value

REGION = "Europe"
Y, Q = 2025, 4


@pytest.fixture
def parts(db):
    """Two component indexes (Graphite=100, Wood=50) + an empty composite commodity.
    Platform rows (no team) — cleaned up explicitly since no team CASCADE covers them."""
    suf = uuid.uuid4().hex[:6]
    g = CommodityIndex(name=f"Graphite-{suf}", unit="$/mt", currency="USD", category="Metal")
    w = CommodityIndex(name=f"Wood-{suf}", unit="$/mt", currency="USD", category="Custom")
    pencil = CommodityIndex(name=f"Pencil-{suf}", unit="$/mt", currency="USD", category="Composite")
    db.add_all([g, w, pencil])
    db.flush()
    db.add_all([
        IndexValue(commodity_id=g.id, region=REGION, year=Y, quarter=Q, value=100),
        IndexValue(commodity_id=w.id, region=REGION, year=Y, quarter=Q, value=50),
    ])
    db.commit()
    ids = [g.id, w.id, pencil.id]
    yield g, w, pencil
    db.query(IndexValue).filter(IndexValue.commodity_id.in_(ids)).delete(synchronize_session=False)
    db.query(CommodityIndex).filter(CommodityIndex.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


def _set_composite(client, cid, expr, variables):
    return client.put(f"/api/indexes/{cid}/composite",
                      json={"composite_expression": expr, "composite_variables": variables})


def test_composite_computes_from_components(db, parts, client_as, user_factory):
    g, w, pencil = parts
    admin = user_factory(is_super_admin=True)
    r = _set_composite(client_as(admin), pencil.id, "0.6*Graphite + 0.3*Wood + FC", {
        "Graphite": {"type": "index", "commodity_id": g.id},
        "Wood": {"type": "index", "commodity_id": w.id},
        "FC": {"type": "fixed", "value": 10},
    })
    assert r.status_code == 200, r.text
    db.expire_all()
    # 0.6*100 + 0.3*50 + 10 = 85
    val = get_single_index_value(db, admin["team_id"], pencil.id, REGION, Y, Q)
    assert val == pytest.approx(85.0)


def test_missing_component_returns_none(db, parts, client_as, user_factory):
    g, w, pencil = parts
    admin = user_factory(is_super_admin=True)
    _set_composite(client_as(admin), pencil.id, "Graphite + Missing", {
        "Graphite": {"type": "index", "commodity_id": g.id},
        "Missing": {"type": "index", "commodity_id": 999_999_999},
    })
    db.expire_all()
    # 999999999 is a valid-looking id with no data → whole composite not computable
    assert get_single_index_value(db, admin["team_id"], pencil.id, REGION, Y, Q) is None


def test_grid_emits_composite_row(db, parts, client_as, user_factory):
    g, w, pencil = parts
    admin = user_factory(is_super_admin=True)
    _set_composite(client_as(admin), pencil.id, "Graphite + Wood", {
        "Graphite": {"type": "index", "commodity_id": g.id},
        "Wood": {"type": "index", "commodity_id": w.id},
    })
    rows = client_as(admin).get("/api/indexes/values", params={
        "team_id": str(admin["team_id"]), "commodity_name": pencil.name,
        "from_year": Y, "from_quarter": Q, "to_year": Y, "to_quarter": Q,
    }).json()
    match = [r for r in rows if r["commodity_id"] == pencil.id and r["year"] == Y and r["quarter"] == Q]
    assert match and match[0]["source"] == "composite" and match[0]["value"] == pytest.approx(150.0)


def test_validation_errors(db, parts, client_as, user_factory):
    g, w, pencil = parts
    c = client_as(user_factory(is_super_admin=True))
    # undefined variable in expression
    assert _set_composite(c, pencil.id, "Graphite + Unknown",
                          {"Graphite": {"type": "index", "commodity_id": g.id}}).status_code == 422
    # self-reference
    assert _set_composite(c, pencil.id, "2*Self",
                          {"Self": {"type": "index", "commodity_id": pencil.id}}).status_code == 422
    # unknown commodity id
    assert _set_composite(c, pencil.id, "X",
                          {"X": {"type": "index", "commodity_id": 987_654_321}}).status_code == 422
    # unparseable expression
    assert _set_composite(c, pencil.id, "0.6*Graphite +",
                          {"Graphite": {"type": "index", "commodity_id": g.id}}).status_code == 422


def test_non_super_admin_forbidden(parts, client_as, tenant_a):
    _, _, pencil = parts
    r = _set_composite(client_as(tenant_a), pencil.id, "1+1", {})
    assert r.status_code == 403
