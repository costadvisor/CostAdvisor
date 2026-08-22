"""Scrum 14b — advanced formula persistence.

Regression test: an advanced-mode cost model created via the API must persist
formula_type / expression / variables on its FormulaVersion. (These were being
dropped by the cost_models router, silently degrading advanced formulas to the
base-price fallback.)
"""
from __future__ import annotations

import uuid

from app.models.product import Product
from app.models.cost_model import FormulaVersion


def test_advanced_formula_round_trips(client_as, tenant_a, db):
    product = Product(
        id=uuid.uuid4(), team_id=tenant_a["team_id"],
        created_by=tenant_a["user_id"], name="Adv Formula Product",
    )
    db.add(product)
    db.commit()

    payload = {
        "product_id": str(product.id),
        "region": "Europe", "currency": "USD",
        "formula": {
            "formula_type": "advanced",
            "base_price": 100,
            "base_year": 2024, "base_quarter": 1,
            "margin_type": "pct", "margin_value": 0,
            "expression": "0.75*ACN + 1500",
            "variables": {"ACN": {"type": "fixed", "value": 10}},
            "components": [],
        },
    }
    r = client_as(tenant_a).post(f"/api/cost-models/?team_id={tenant_a['team_id']}", json=payload)
    assert r.status_code == 201, r.text
    cm_id = uuid.UUID(r.json()["id"])

    db.expire_all()
    fv = db.query(FormulaVersion).filter(FormulaVersion.cost_model_id == cm_id).first()
    assert fv is not None
    assert fv.formula_type == "advanced"
    assert fv.expression == "0.75*ACN + 1500"
    assert fv.variables == {"ACN": {"type": "fixed", "value": 10}}
