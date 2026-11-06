"""Actual volumes are stored in the unit the cost model is priced in (audit C-01).

The engine (Squeeze, Brief) and the portfolio exposure read the volume number
without its unit. A quantity sent without a unit used to be stored as "kg" on a
tonne-priced model, and an upload in kg on that model was read as tonnes: a
1000x total impact.
"""
from __future__ import annotations

import uuid

from app.models.actual_volume import ActualVolume
from app.models.cost_model import CostModel
from app.models.product import Product


def _mk_model(db, tenant, unit="t"):
    p = Product(id=uuid.uuid4(), team_id=tenant["team_id"], created_by=tenant["user_id"],
                name="Volume unit product", unit=unit)
    db.add(p)
    db.flush()
    cm = CostModel(id=uuid.uuid4(), team_id=tenant["team_id"], product_id=p.id,
                   created_by=tenant["user_id"], region="Europe", currency="EUR")
    db.add(cm)
    db.commit()
    return cm


def _stored(db, cm, year, quarter):
    db.expire_all()
    return db.query(ActualVolume).filter_by(cost_model_id=cm.id, year=year, quarter=quarter).one()


def test_put_without_unit_takes_the_product_unit(client_as, tenant_a, db):
    cm = _mk_model(db, tenant_a, unit="t")
    r = client_as(tenant_a).put(f"/api/volumes/{cm.id}/2026/4", json={"year": 2026, "quarter": 4, "volume": 12})
    assert r.status_code == 200, r.text
    assert r.json()["unit"] == "t"
    row = _stored(db, cm, 2026, 4)
    assert (float(row.volume), row.unit) == (12.0, "t")


def test_put_in_kg_on_a_tonne_product_is_stored_in_tonnes(client_as, tenant_a, db):
    cm = _mk_model(db, tenant_a, unit="t")
    r = client_as(tenant_a).put(f"/api/volumes/{cm.id}/2026/3",
                                json={"year": 2026, "quarter": 3, "volume": 600000, "unit": "kg"})
    assert r.status_code == 200, r.text
    row = _stored(db, cm, 2026, 3)
    assert (float(row.volume), row.unit) == (600.0, "t")


def test_put_in_an_unconvertible_unit_is_refused(client_as, tenant_a, db):
    cm = _mk_model(db, tenant_a, unit="t")
    r = client_as(tenant_a).put(f"/api/volumes/{cm.id}/2026/3",
                                json={"year": 2026, "quarter": 3, "volume": 5, "unit": "m3"})
    assert r.status_code == 400
    assert db.query(ActualVolume).filter_by(cost_model_id=cm.id).count() == 0


def test_template_and_upload_use_the_product_unit(client_as, tenant_a, db):
    cm = _mk_model(db, tenant_a, unit="t")
    c = client_as(tenant_a)
    tpl = c.get(f"/api/volumes/{cm.id}/template")
    assert tpl.status_code == 200
    lines = tpl.text.strip().splitlines()
    assert lines[0] == "period,volume,unit"
    assert all(line.endswith(",t") for line in lines[1:])

    csv = "period,volume,unit\nQ1-2026,600000,kg\nQ2-2026,700,\nQ3-2026,5,m3\n"
    r = c.post(f"/api/volumes/{cm.id}/upload",
               files={"file": ("volumes.csv", csv.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["rows_processed"] == 2
    assert len(r.json()["errors"]) == 1          # the m3 row is skipped, with a reason
    assert (float(_stored(db, cm, 2026, 1).volume), _stored(db, cm, 2026, 1).unit) == (600.0, "t")
    assert (float(_stored(db, cm, 2026, 2).volume), _stored(db, cm, 2026, 2).unit) == (700.0, "t")


def test_negative_quantity_and_price_are_refused(client_as, tenant_a, db):
    """Audit C-06: -12 t and a -5 price were stored and fed gap and impact."""
    cm = _mk_model(db, tenant_a, unit="t")
    c = client_as(tenant_a)
    r = c.put(f"/api/volumes/{cm.id}/2026/4", json={"year": 2026, "quarter": 4, "volume": -12})
    assert r.status_code == 422
    r = c.put(f"/api/prices/{cm.id}/2026/4", json={"year": 2026, "quarter": 4, "price": -5})
    assert r.status_code == 422
    r = c.put(f"/api/prices/{cm.id}/2026/4", json={"year": 2026, "quarter": 4, "price": 0})
    assert r.status_code == 422
    assert c.put(f"/api/volumes/{cm.id}/2026/4", json={"year": 2026, "quarter": 4, "volume": 0}).status_code == 200

    csv = "period,price\nQ1-2026,-5\nQ2-2026,300\n"
    r = c.post(f"/api/prices/{cm.id}/upload", files={"file": ("prices.csv", csv.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["rows_processed"] == 1
    assert len(r.json()["errors"]) == 1
