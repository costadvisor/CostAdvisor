"""Scrum 17 — should-cost breakdown (inspectable numbers).

Reuses the `brief_model` fixture from test_brief.py: 2 indexed components
(Caustic weight 0.7, Energy weight 0.3), margin 0% (should_cost == indexed cost,
so expected numbers are exact), base 2024-Q1 -> current 2024-Q2.
"""
import pytest

from tests.test_brief import brief_model, REGION, BASE_Y, BASE_Q, CUR_Y, CUR_Q  # noqa: F401


def test_components_sum_to_cost_before_margin(client_as, tenant_a, brief_model):
    r = client_as(tenant_a).post("/api/costing/should-cost/breakdown",
                                json={"cost_model_id": str(brief_model.id), "target_year": CUR_Y, "target_quarter": CUR_Q})
    assert r.status_code == 200, r.text
    b = r.json()

    assert len(b["components"]) == 2
    comp_sum = sum(c["contribution"] for c in b["components"])
    assert comp_sum == pytest.approx(b["cost_before_margin"], abs=0.01)
    # margin 0% -> should_cost == cost_before_margin == indexed cost == 111.0
    assert b["should_cost"] == pytest.approx(111.0)
    assert b["cost_before_margin"] == pytest.approx(111.0)
    assert b["margin_amount"] == pytest.approx(0.0)
    assert b["data_gaps"] == []

    caustic = next(c for c in b["components"] if "Caustic" in c["label"])
    assert caustic["weight_pct"] == pytest.approx(70.0)
    assert caustic["base_value"] == pytest.approx(100.0)
    assert caustic["current_value"] == pytest.approx(120.0)
    assert caustic["ratio"] == pytest.approx(1.2)
    assert caustic["contribution"] == pytest.approx(84.0)  # 100*0.7*1.2
    assert caustic["source"] == "scraped_region"
    assert caustic["has_data"] is True

    energy = next(c for c in b["components"] if "Energy" in c["label"])
    assert energy["contribution"] == pytest.approx(27.0)  # 100*0.3*0.9


def test_cost_before_margin_plus_margin_equals_should_cost(client_as, tenant_a, brief_model, db):
    """Even with a non-zero margin, cost_before_margin + margin_amount == should_cost
    exactly (before any Incoterm normalization) — confirmed by direct code read of
    _apply_margin, which defines margin_amount as should_cost - indexed_cost in every
    branch."""
    from app.models.cost_model import FormulaVersion
    fv = db.query(FormulaVersion).filter(FormulaVersion.cost_model_id == brief_model.id).first()
    fv.margin_type = "pct"
    fv.margin_value = 10
    db.commit()

    r = client_as(tenant_a).post("/api/costing/should-cost/breakdown",
                                json={"cost_model_id": str(brief_model.id), "target_year": CUR_Y, "target_quarter": CUR_Q})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["cost_before_margin"] + b["margin_amount"] == pytest.approx(b["should_cost"], abs=0.01)


def test_incoterm_adjustment_completes_the_sum(client_as, tenant_a, brief_model):
    """With normalize_to_incoterm set, the final should_cost differs from the
    pre-incoterm total by exactly incoterm_adjustment."""
    r = client_as(tenant_a).post("/api/costing/should-cost/breakdown", json={
        "cost_model_id": str(brief_model.id), "normalize_to_incoterm": "FOB",
    })
    assert r.status_code == 200, r.text
    b = r.json()
    pre = b["cost_before_margin"] + b["margin_amount"]
    assert pre + b["incoterm_adjustment"] == pytest.approx(b["should_cost"], abs=0.01)


def test_missing_component_data_appears_as_gap(client_as, tenant_a, brief_model, db):
    """A component whose commodity has no index value at the target period rides
    flat (ratio 1.0) and is listed in data_gaps rather than silently vanishing."""
    from app.models.index_data import IndexValue
    from app.models.cost_model import FormulaVersion, FormulaComponent
    fv = db.query(FormulaVersion).filter(FormulaVersion.cost_model_id == brief_model.id).first()
    comp = db.query(FormulaComponent).filter(FormulaComponent.formula_version_id == fv.id,
                                             FormulaComponent.label == "Energy").first()
    # Remove ALL of Energy's index values (not just the current quarter) — the
    # resolver's temporal carry-forward would otherwise use the base-period value
    # for the "current" one too, which is a legitimate fallback, not a gap.
    db.query(IndexValue).filter(IndexValue.commodity_id == comp.commodity_id).delete()
    db.commit()

    r = client_as(tenant_a).post("/api/costing/should-cost/breakdown",
                                json={"cost_model_id": str(brief_model.id), "target_year": CUR_Y, "target_quarter": CUR_Q})
    assert r.status_code == 200, r.text
    b = r.json()
    assert len(b["data_gaps"]) == 1
    assert b["data_gaps"][0]["component_label"] == "Energy"
    energy = next(c for c in b["components"] if c["label"] == "Energy")
    assert energy["has_data"] is False
    assert energy["ratio"] == pytest.approx(1.0)


def test_non_member_forbidden(client_as, tenant_b, brief_model):
    r = client_as(tenant_b).post("/api/costing/should-cost/breakdown",
                                 json={"cost_model_id": str(brief_model.id), "target_year": CUR_Y, "target_quarter": CUR_Q})
    assert r.status_code in (403, 404)


def test_no_formula_returns_422(client_as, tenant_a, db):
    import uuid
    from app.models.cost_model import CostModel
    from app.models.product import Product
    product = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"],
                      created_by=tenant_a["user_id"], name="No Formula Yet", unit="kg")
    db.add(product)
    db.flush()
    cm = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=product.id,
                   created_by=tenant_a["user_id"], region="Europe", currency="USD")
    db.add(cm)
    db.commit()
    r = client_as(tenant_a).post("/api/costing/should-cost/breakdown",
                                 json={"cost_model_id": str(cm.id)})
    assert r.status_code == 422
