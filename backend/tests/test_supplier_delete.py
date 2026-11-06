"""Deleting a supplier that cost models still name is refused (audit C-03).

It used to succeed and silently null the supplier on each of them, so a model
lost its supplier (and its benchmark history) with a one-line "Delete this
supplier?" as the only warning. Contracts keep their history without the
supplier, by design, so they do not block the delete.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.models.contract import Contract
from app.models.cost_model import CostModel
from app.models.product import Product
from app.models.supplier import Supplier


def _mk_supplier(db, tenant, name="Delete Me Chemicals"):
    s = Supplier(team_id=tenant["team_id"], name=name)
    db.add(s)
    db.commit()
    return s


def test_unused_supplier_deletes(client_as, tenant_a, db):
    sid = _mk_supplier(db, tenant_a).id
    r = client_as(tenant_a).delete(f"/api/suppliers/{sid}")
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Supplier, sid) is None


def test_supplier_of_a_cost_model_is_kept(client_as, tenant_a, db):
    s = _mk_supplier(db, tenant_a)
    p = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"], created_by=tenant_a["user_id"], name="P", unit="t")
    db.add(p)
    db.flush()
    cm = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=p.id, created_by=tenant_a["user_id"],
                   supplier_id=s.id, region="Europe", currency="EUR")
    db.add(cm)
    db.commit()

    r = client_as(tenant_a).delete(f"/api/suppliers/{s.id}")
    assert r.status_code == 409
    assert "1 cost model" in r.json()["detail"]
    db.expire_all()
    assert db.get(Supplier, s.id) is not None
    assert db.get(CostModel, cm.id).supplier_id == s.id


def test_supplier_of_a_contract_only_deletes(client_as, tenant_a, db):
    s = _mk_supplier(db, tenant_a)
    sid = s.id
    c = Contract(team_id=tenant_a["team_id"], supplier_id=sid, reference="C-1",
                 term_start=date(2026, 1, 1), term_end=date(2026, 12, 31))
    db.add(c)
    db.commit()
    cid = c.id

    r = client_as(tenant_a).delete(f"/api/suppliers/{sid}")
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Contract, cid).supplier_id is None
