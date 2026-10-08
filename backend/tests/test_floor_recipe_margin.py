"""The negotiation floor leaves the recipe's own margin line out.

A catalogue recipe carries the supplier margin as a fixed line inside its
weights, so a cost model built from it keeps `margin_value = 0` (adding a
margin on top would count it twice). The floor is "cost before margin", so it
must drop that line; otherwise the floor equals the should-cost and the prep
script tells the buyer the target leaves the supplier nothing.

The recipe is picked from the loaded drop at run time (a platform coverage
whose own region set has a margin-category line), never named here.
Covers both link modes: `tracking` (lines resolved live from the template)
and `pinned` (the saved snapshot, matched back to the template by name).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.product import Product
from app.services.negotiation_prep import build_script


# The price the buyer enters for the base period (the combo has none).
BASE_PRICE = 1000.0


def _pick_coverage(db) -> dict:
    """A platform combo with a base period, no chained inputs, and a margin
    line in the region's own line set. The drop stores no base price on the
    combo, so the buyer types one (BASE_PRICE), as CostModelBuilder asks."""
    row = db.execute(text("""
        SELECT c.id AS coverage_id, c.template_id, c.region,
               c.base_year, c.base_quarter
        FROM formula_region_coverage c
        JOIN formula_templates t ON t.id = c.template_id AND t.team_id IS NULL
        WHERE c.base_year IS NOT NULL AND c.base_quarter IS NOT NULL
          AND EXISTS (SELECT 1 FROM formula_template_components m
                      WHERE m.template_id = c.template_id AND m.region = c.region
                        AND m.cost_category = 'margin')
          AND NOT EXISTS (SELECT 1 FROM formula_template_components f
                          WHERE f.template_id = c.template_id
                            AND f.component_type = 'formula')
        ORDER BY c.template_id, c.region
        LIMIT 1
    """)).mappings().first()
    assert row is not None, "the loaded drop has no platform combo with a margin line"
    return dict(row)


def _margin_share(db, template_id, region) -> float:
    """The margin lines' share of the region's recipe weights."""
    total, margin = db.execute(text("""
        SELECT sum(weight_pct), sum(weight_pct) FILTER (WHERE cost_category = 'margin')
        FROM formula_template_components
        WHERE template_id = :t AND region = :r
    """), {"t": str(template_id), "r": region}).one()
    return float(margin) / float(total)


@pytest.fixture
def catalogue_model(content_loaded, db, tenant_a, client_as):
    """Factory: a team cost model built from the picked combo the way
    CostModelBuilder's `loadTemplateIntoModel` does it (resolve, then save the
    lines with margin 0 and the combo link)."""
    cov = _pick_coverage(db)
    c = client_as(tenant_a)
    team_id = tenant_a["team_id"]
    product = Product(team_id=team_id, created_by=tenant_a["user_id"],
                      name="Floor check product", unit="t")
    db.add(product)
    db.commit()
    made: list[str] = []

    r = c.get(f"/api/formulas/{cov['template_id']}/resolve",
              params={"team_id": str(team_id), "region": cov["region"]})
    assert r.status_code == 200, r.text
    lines = r.json()["lines"]
    total = sum(l["effective_weight_pct"] for l in lines)
    components = [{
        # FormulaComponent.label is 64 characters; the demo seed cuts the same way.
        "label": l["name"][:64],
        "commodity_id": l["commodity_id"],
        "weight": l["effective_weight_pct"] / total,
        "component_type": l["component_type"],
        "depth": l["depth"],
        "via_template_id": l["via_template_id"],
        "line_region": l["line_region"],
        "is_proxy": l["is_proxy"],
    } for l in lines]

    def _make(link_mode: str) -> str:
        r = c.post(f"/api/cost-models/?team_id={team_id}", json={
            "product_id": str(product.id), "region": cov["region"],
            "currency": "EUR",
            "formula": {
                "formula_type": "simple",
                "base_price": BASE_PRICE,
                "base_year": cov["base_year"], "base_quarter": cov["base_quarter"],
                "margin_type": "pct", "margin_value": 0,
                "components": components,
                "source_coverage_id": str(cov["coverage_id"]),
                "link_mode": link_mode,
            },
        })
        assert r.status_code == 201, r.text
        made.append(r.json()["id"])
        return r.json()["id"]

    yield c, cov, _make

    bypass_rls_var.set(True)
    for cmid in made:
        db.execute(text("DELETE FROM cost_models WHERE id = :id"), {"id": cmid})
    db.execute(text("DELETE FROM products WHERE id = :id"), {"id": str(product.id)})
    db.commit()


@pytest.mark.parametrize("link_mode", ["tracking", "pinned"])
def test_floor_is_should_cost_minus_the_recipe_margin_line(catalogue_model, db, link_mode):
    c, cov, make = catalogue_model
    cm_id = make(link_mode)

    r = c.post("/api/costing/brief", json={"cost_model_id": cm_id})
    assert r.status_code == 200, r.text
    b = r.json()
    should_cost, floor = b["current_should_cost"], b["current_floor"]
    assert floor is not None
    assert floor < should_cost

    # Margin lines are fixed (ratio 1), and with margin 0 the recipe's cost
    # pool is the whole base price, so the line is worth base x its share.
    # Pinned weights are stored to 4 decimals, hence the tolerance.
    expected = BASE_PRICE * _margin_share(db, cov["template_id"], cov["region"])
    assert should_cost - floor == pytest.approx(expected, rel=1e-3, abs=0.01)

    # The breakdown itemises the same line(s): floor + margin line = should-cost.
    margin_labels = {n[:64] for (n,) in db.execute(text("""
        SELECT name FROM formula_template_components
        WHERE template_id = :t AND region = :r AND cost_category = 'margin'
    """), {"t": str(cov["template_id"]), "r": cov["region"]}).all()}
    r = c.post("/api/costing/should-cost/breakdown", json={"cost_model_id": cm_id})
    assert r.status_code == 200, r.text
    itemised = sum(x["contribution"] for x in r.json()["components"]
                   if x["label"] in margin_labels)
    assert itemised > 0
    assert floor + itemised == pytest.approx(should_cost, abs=0.01)


@pytest.mark.parametrize("link_mode", ["tracking", "pinned"])
def test_prep_ladder_and_script_use_the_lower_floor(catalogue_model, link_mode):
    c, _cov, make = catalogue_model
    cm_id = make(link_mode)

    r = c.get(f"/api/negotiation-prep/{cm_id}/prep")
    assert r.status_code == 200, r.text
    p = r.json()
    ladder = p["ladder"]
    assert ladder["floor"] is not None
    assert ladder["floor"] < ladder["should_cost"]
    close = next(ln for ln in p["script"] if ln.startswith("We are ready to settle"))
    assert "no margin left" in close
    assert f"{ladder['floor']:,.2f}" in close
    assert f"{ladder['should_cost']:,.2f}" in close


def test_script_says_nothing_about_a_floor_equal_to_the_target():
    """A recipe with no margin anywhere has floor == should-cost. The script
    must not then say the target leaves the supplier no margin."""
    class _Brief:
        product_name = "Hand-built product"
        currency = "EUR"
        unit = "t"
        period_label = "Q1-26 to Q2-26"
        current_should_cost = 500.0
        current_floor = 500.0
        current_actual_price = None
        gap = None
        data_gaps: list = []

    body = " ".join(build_script(_Brief(), []))
    assert "500.00 EUR/t" in body
    assert "no margin left" not in body
