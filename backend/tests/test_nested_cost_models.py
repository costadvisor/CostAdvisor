"""Nested cost models — "Lego" formulas (Scrum 27).

The thing that makes this risky is that it reaches into the costing engine, so
the first assertion is the one that says an unnested model still prices exactly
as it did. Everything else tests the nesting itself.

The convention under test, because the other reading is tempting and wrong: a
nested child contributes its INDEX COMPOSITION, not its price. Its lines fold
into the parent with weights multiplied, and the parent's base_price stays the
anchor — the same convention a chained FormulaTemplate already uses, and what
keeps a recipe's weights summing to one.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.index_data import CommodityIndex, IndexValue
from app.models.product import Product
from app.services.formula_resolver import (
    FormulaChainError, MAX_NEST_DEPTH, assert_valid_nesting, get_effective_lines,
)

REGION = "Europe"
BASE_Y, BASE_Q = 2023, 1
CUR_Y, CUR_Q = 2024, 1


@pytest.fixture
def lego(tenant_a, db):
    """Parent P with one index line and one sub-model line; child C with two
    index lines. Yields a dict of the objects and the commodities.

        P: acid 60%  ·  [C] 40%
        C: caustic 75%  ·  energy 25%

    Effective, flattened: acid 60%, caustic 30%, energy 10%.
    """
    suffix = uuid.uuid4().hex[:8]
    acid = CommodityIndex(name=f"Acid-{suffix}", currency="USD", unit="t")
    caustic = CommodityIndex(name=f"Caustic-{suffix}", currency="USD", unit="t")
    energy = CommodityIndex(name=f"Energy-{suffix}", currency="USD", unit="MWh")
    db.add_all([acid, caustic, energy])
    db.flush()
    db.add_all([
        IndexValue(commodity_id=acid.id, region=REGION, year=BASE_Y, quarter=BASE_Q, value=100),
        IndexValue(commodity_id=acid.id, region=REGION, year=CUR_Y, quarter=CUR_Q, value=110),
        IndexValue(commodity_id=caustic.id, region=REGION, year=BASE_Y, quarter=BASE_Q, value=100),
        IndexValue(commodity_id=caustic.id, region=REGION, year=CUR_Y, quarter=CUR_Q, value=200),
        IndexValue(commodity_id=energy.id, region=REGION, year=BASE_Y, quarter=BASE_Q, value=100),
        IndexValue(commodity_id=energy.id, region=REGION, year=CUR_Y, quarter=CUR_Q, value=50),
    ])

    def _model(name, base_price):
        product = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"],
                          created_by=tenant_a["user_id"], name=name, unit="t")
        db.add(product)
        db.flush()
        cm = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=product.id,
                       created_by=tenant_a["user_id"], region=REGION, currency="USD")
        db.add(cm)
        db.flush()
        fv = FormulaVersion(cost_model_id=cm.id, base_price=base_price,
                            base_year=BASE_Y, base_quarter=BASE_Q,
                            margin_type="pct", margin_value=0)
        db.add(fv)
        db.flush()
        return cm, fv, product

    child, child_fv, child_product = _model(f"Child-{suffix}", 500)
    db.add_all([
        FormulaComponent(formula_version_id=child_fv.id, label="Caustic",
                         commodity_id=caustic.id, weight=0.75, component_type="index"),
        FormulaComponent(formula_version_id=child_fv.id, label="Energy",
                         commodity_id=energy.id, weight=0.25, component_type="index"),
    ])

    parent, parent_fv, parent_product = _model(f"Parent-{suffix}", 1000)
    db.add_all([
        FormulaComponent(formula_version_id=parent_fv.id, label="Acid",
                         commodity_id=acid.id, weight=0.60, component_type="index"),
        FormulaComponent(formula_version_id=parent_fv.id, label="Blend",
                         weight=0.40, component_type="model", child_cost_model_id=child.id),
    ])
    db.commit()

    yield {
        "parent": parent, "parent_fv": parent_fv, "child": child, "child_fv": child_fv,
        "acid": acid, "caustic": caustic, "energy": energy,
        "products": [parent_product.id, child_product.id],
    }

    bypass_rls_var.set(True)
    for cm_id in (parent.id, child.id):
        db.execute(text("DELETE FROM formula_components WHERE formula_version_id IN "
                        "(SELECT id FROM formula_versions WHERE cost_model_id = :c)"),
                   {"c": str(cm_id)})
    db.execute(text("DELETE FROM cost_models WHERE id = :c"), {"c": str(parent.id)})
    db.execute(text("DELETE FROM cost_models WHERE id = :c"), {"c": str(child.id)})
    for pid in (parent_product.id, child_product.id):
        db.execute(text("DELETE FROM products WHERE id = :p"), {"p": str(pid)})
    db.commit()


# ── the flattening ────────────────────────────────────────────────────────

def test_a_sub_model_folds_in_with_its_weights_multiplied(db, lego):
    lines, fallback = get_effective_lines(db, lego["parent_fv"], lego["parent"])
    assert fallback is None
    by_label = {l.label: l for l in lines}
    assert set(by_label) == {"Acid", "Caustic", "Energy"}, "the model line is replaced, not kept"
    assert by_label["Acid"].weight == pytest.approx(0.60)
    assert by_label["Caustic"].weight == pytest.approx(0.30)   # 0.75 of 0.40
    assert by_label["Energy"].weight == pytest.approx(0.10)    # 0.25 of 0.40
    # The invariant the whole convention exists to preserve.
    assert sum(l.weight for l in lines) == pytest.approx(1.0)


def test_a_folded_line_says_which_sub_model_it_came_through(db, lego):
    lines, _ = get_effective_lines(db, lego["parent_fv"], lego["parent"])
    by_label = {l.label: l for l in lines}
    assert by_label["Caustic"].via_cost_model_id == lego["child"].id
    assert by_label["Caustic"].via_cost_model_name is not None
    assert by_label["Caustic"].depth == 1
    # A line that is the parent's own is not attributed to a sub-model.
    assert by_label["Acid"].via_cost_model_id is None
    assert by_label["Acid"].depth in (None, 0)


def test_the_should_cost_matches_the_flattened_arithmetic(db, client_as, tenant_a, lego):
    """Acid +10% at 60%, caustic +100% at 30%, energy -50% at 10%:
    1000 x (0.6*1.1 + 0.3*2.0 + 0.1*0.5) = 1000 x 1.31 = 1310."""
    r = client_as(tenant_a).post("/api/costing/should-cost", json={
        "cost_model_id": str(lego["parent"].id),
        "target_year": CUR_Y, "target_quarter": CUR_Q,
    })
    assert r.status_code == 200, r.text
    assert r.json()["should_cost"] == pytest.approx(1310.0, rel=1e-6)


def test_the_child_still_prices_on_its_own_anchor(db, client_as, tenant_a, lego):
    """Nesting must not change what the child is worth by itself:
    500 x (0.75*2.0 + 0.25*0.5) = 500 x 1.625 = 812.5."""
    r = client_as(tenant_a).post("/api/costing/should-cost", json={
        "cost_model_id": str(lego["child"].id),
        "target_year": CUR_Y, "target_quarter": CUR_Q,
    })
    assert r.json()["should_cost"] == pytest.approx(812.5, rel=1e-6)


def test_the_breakdown_lists_the_nested_lines_and_sums_to_the_total(db, client_as, tenant_a, lego):
    r = client_as(tenant_a).post("/api/costing/should-cost/breakdown", json={
        "cost_model_id": str(lego["parent"].id),
        "target_year": CUR_Y, "target_quarter": CUR_Q,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    labels = {c["label"] for c in body["components"]}
    assert labels == {"Acid", "Caustic", "Energy"}
    nested = [c for c in body["components"] if c["label"] == "Caustic"][0]
    assert nested["via_cost_model_name"] is not None, "a brief must not flatten a sub-model anonymously"
    total = sum(c["contribution"] for c in body["components"])
    assert total == pytest.approx(body["cost_before_margin"], rel=1e-6)


# ── refusals ──────────────────────────────────────────────────────────────

def test_a_child_with_no_formula_is_a_gap_not_a_flat_line(db, lego):
    """It keeps its weight — dropping it would silently rescale everything
    else — but it must not pass for a fixed cost."""
    bypass_rls_var.set(True)
    db.query(FormulaVersion).filter(
        FormulaVersion.cost_model_id == lego["child"].id).delete()
    db.flush()
    db.expire_all()

    lines, _ = get_effective_lines(db, lego["parent_fv"], lego["parent"])
    by_label = {l.label: l for l in lines}
    assert "Blend" in by_label
    assert by_label["Blend"].weight == pytest.approx(0.40)
    assert by_label["Blend"].component_type == "model"
    assert "no formula" in by_label["Blend"].unresolved_reason
    assert sum(l.weight for l in lines) == pytest.approx(1.0)
    db.rollback()


def test_an_unresolved_sub_model_reaches_the_engine_as_a_data_gap(db, client_as, tenant_a, lego):
    bypass_rls_var.set(True)
    db.query(FormulaVersion).filter(
        FormulaVersion.cost_model_id == lego["child"].id).delete()
    db.commit()

    r = client_as(tenant_a).post("/api/costing/should-cost/breakdown", json={
        "cost_model_id": str(lego["parent"].id),
        "target_year": CUR_Y, "target_quarter": CUR_Q,
    })
    assert r.status_code == 200, r.text
    reasons = " ".join(g["reason"] for g in r.json()["data_gaps"])
    assert "no formula" in reasons


def test_a_cycle_is_refused_at_save_time(db, lego):
    """A loop is unbounded recursion inside the engine, not a wrong number, so
    it has to fail where somebody can still fix it."""
    with pytest.raises(FormulaChainError) as exc:
        assert_valid_nesting(db, lego["child"].id, lego["parent"].id)
    assert "circular" in str(exc.value).lower()


def test_a_model_cannot_contain_itself(db, lego):
    with pytest.raises(FormulaChainError):
        assert_valid_nesting(db, lego["parent"].id, lego["parent"].id)


def test_a_legal_nesting_passes_the_guard(db, lego):
    assert_valid_nesting(db, lego["parent"].id, lego["child"].id)  # no raise


def test_the_api_refuses_a_circular_save(db, client_as, tenant_a, lego):
    r = client_as(tenant_a).post(f"/api/cost-models/{lego['child'].id}/renegotiate", json={
        "base_price": 500, "base_year": BASE_Y, "base_quarter": BASE_Q,
        "margin_type": "pct", "margin_value": 0,
        "components": [
            {"label": "Loop", "weight": 1.0, "component_type": "model",
             "child_cost_model_id": str(lego["parent"].id)},
        ],
    })
    assert r.status_code == 400, r.text
    assert "circular" in r.json()["detail"].lower()


def test_a_component_cannot_be_both_an_index_and_a_sub_model(db, client_as, tenant_a, lego):
    r = client_as(tenant_a).post(f"/api/cost-models/{lego['parent'].id}/renegotiate", json={
        "base_price": 1000, "base_year": BASE_Y, "base_quarter": BASE_Q,
        "margin_type": "pct", "margin_value": 0,
        "components": [
            {"label": "Both", "weight": 1.0, "commodity_id": lego["acid"].id,
             "child_cost_model_id": str(lego["child"].id)},
        ],
    })
    assert r.status_code == 400
    assert "not both" in r.json()["detail"].lower()


def test_a_child_from_another_team_is_refused(db, client_as, tenant_a, tenant_b, lego, user_factory):
    other_product = Product(id=uuid.uuid4(), team_id=tenant_b["team_id"],
                            created_by=tenant_b["user_id"], name="Theirs", unit="t")
    bypass_rls_var.set(True)
    db.add(other_product)
    db.flush()
    other_cm = CostModel(id=uuid.uuid4(), team_id=tenant_b["team_id"], product_id=other_product.id,
                         created_by=tenant_b["user_id"], region=REGION, currency="USD")
    db.add(other_cm)
    db.commit()
    try:
        r = client_as(tenant_a).post(f"/api/cost-models/{lego['parent'].id}/renegotiate", json={
            "base_price": 1000, "base_year": BASE_Y, "base_quarter": BASE_Q,
            "margin_type": "pct", "margin_value": 0,
            "components": [
                {"label": "Theirs", "weight": 1.0, "component_type": "model",
                 "child_cost_model_id": str(other_cm.id)},
            ],
        })
        assert r.status_code == 400
        assert "inaccessible" in r.json()["detail"].lower()
    finally:
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM cost_models WHERE id = :c"), {"c": str(other_cm.id)})
        db.execute(text("DELETE FROM products WHERE id = :p"), {"p": str(other_product.id)})
        db.commit()


def test_deleting_a_nested_child_is_refused_rather_than_silently_repricing(db, lego):
    """RESTRICT, not SET NULL. A vanished child would change the parent's
    price with nothing on screen to say why."""
    from sqlalchemy.exc import IntegrityError
    bypass_rls_var.set(True)
    with pytest.raises(IntegrityError):
        db.execute(text("DELETE FROM cost_models WHERE id = :c"), {"c": str(lego["child"].id)})
        db.flush()
    db.rollback()


# ── the picker ────────────────────────────────────────────────────────────

def test_nestable_excludes_what_the_save_would_reject(db, client_as, tenant_a, lego):
    """A picker that offers a candidate the save rejects teaches people to
    distrust it, so the exclusion happens before the offer."""
    r = client_as(tenant_a).get(f"/api/cost-models/{lego['child'].id}/nestable")
    assert r.status_code == 200, r.text
    by_id = {c["cost_model_id"]: c for c in r.json()["candidates"]}
    parent = by_id[str(lego["parent"].id)]
    assert parent["eligible"] is False
    assert "circular" in parent["reason"].lower()


def test_nestable_offers_a_legal_candidate(db, client_as, tenant_a, lego):
    r = client_as(tenant_a).get(f"/api/cost-models/{lego['parent'].id}/nestable")
    by_id = {c["cost_model_id"]: c for c in r.json()["candidates"]}
    assert by_id[str(lego["child"].id)]["eligible"] is True
    assert by_id[str(lego["child"].id)]["reason"] is None


# ── clone ─────────────────────────────────────────────────────────────────

def test_cloning_keeps_the_sub_model_link(db, client_as, tenant_a, lego):
    """A clone that quietly lost its sub-models would not be an equivalent
    formula — the same reasoning that makes clone carry the catalog link."""
    r = client_as(tenant_a).post(f"/api/cost-models/{lego['parent'].id}/clone")
    assert r.status_code == 201, r.text
    clone_id = r.json()["id"]
    try:
        bypass_rls_var.set(True)
        db.expire_all()
        comps = (
            db.query(FormulaComponent)
            .join(FormulaVersion, FormulaComponent.formula_version_id == FormulaVersion.id)
            .filter(FormulaVersion.cost_model_id == uuid.UUID(clone_id))
            .all()
        )
        nested = [c for c in comps if c.component_type == "model"]
        assert len(nested) == 1
        assert nested[0].child_cost_model_id == lego["child"].id
    finally:
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM formula_components WHERE formula_version_id IN "
                        "(SELECT id FROM formula_versions WHERE cost_model_id = :c)"),
                   {"c": clone_id})
        db.execute(text("DELETE FROM cost_models WHERE id = :c"), {"c": clone_id})
        db.commit()


# ── depth ─────────────────────────────────────────────────────────────────

def test_the_depth_cap_is_the_same_number_as_the_template_chain():
    from app.services.formula_resolver import MAX_CHAIN_DEPTH
    assert MAX_NEST_DEPTH == MAX_CHAIN_DEPTH == 3
