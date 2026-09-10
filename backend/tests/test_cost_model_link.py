"""Link priced cost models to the library recipe, not a copy (Scrum 28b).

Covers:
- AC1: source_coverage_id + link_mode round-trip through create/get.
- AC2: tracking-mode should-cost tracks a live template edit; pinned doesn't.
- AC3: tracking-mode breakdown provenance (depth/via_template/line_region)
  matches GET /formulas/{id}/resolve for a chained template.
- AC4: an index-intent component with a broken link reports an explicit gap;
  a deliberately fixed component does not.
- AC5: non-round weights survive to full DB precision (no 1-decimal rounding).
- AC6: link_mode=None (hand-built, unlinked) should-cost is unaffected —
  today's exact behavior is preserved by construction (get_effective_lines
  short-circuits to the snapshot whenever link_mode isn't "tracking").
- Full-scope propagation: Evolution / Brief / Price-Change all read the same
  live-vs-frozen distinction as should-cost, for the same formula version.
- Graceful fallback when the linked coverage is deleted.
- Visibility: a team cannot link to another team's private coverage.
- clone_cost_model preserves the link and per-component provenance.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.formula_template import FormulaRegionCoverage, FormulaTemplate
from app.models.index_data import CommodityIndex, IndexValue
from app.models.product import Product


def _mk_template(db, name, created_by, team_id=None) -> FormulaTemplate:
    t = FormulaTemplate(team_id=team_id, created_by=created_by, name=name, expression=None)
    db.add(t)
    db.commit()
    return t


def _mk_index(db, name) -> CommodityIndex:
    idx = CommodityIndex(name=name, unit="$/mt", currency="USD", scrape_enabled=False)
    db.add(idx)
    db.commit()
    return idx


def _mk_product(db, team_id, created_by, name="Widget") -> Product:
    p = Product(team_id=team_id, created_by=created_by, name=name, unit="kg")
    db.add(p)
    db.commit()
    return p


def _cleanup(db, cost_model_ids=(), template_ids=(), commodity_ids=(), product_ids=()):
    bypass_rls_var.set(True)
    for cmid in cost_model_ids:
        db.execute(text("DELETE FROM cost_models WHERE id = :id"), {"id": str(cmid)})
    for tid in template_ids:
        db.execute(text("DELETE FROM formula_templates WHERE id = :id"), {"id": str(tid)})
    for pid in product_ids:
        db.execute(text("DELETE FROM products WHERE id = :id"), {"id": str(pid)})
    for cid in commodity_ids:
        db.execute(text("DELETE FROM index_values WHERE commodity_id = :id"), {"id": cid})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :id"), {"id": cid})
    db.commit()


def _mk_coverage_with_lines(c, team_id, template_id, idx_id, index_weight=60, fixed_weight=40,
                            base_price=1000, currency="EUR", base_year=2025, base_quarter=1):
    """Seed a template's region-NULL weighted lines + Europe coverage via the
    real API (mirrors how a team actually builds a catalog combo)."""
    assert c.put(f"/api/formulas/{template_id}/components", json={"components": [
        {"name": "Feedstock", "component_type": "index", "commodity_id": idx_id, "weight_pct": index_weight},
        {"name": "Fixed", "component_type": "fixed", "weight_pct": fixed_weight},
    ]}).status_code == 200
    r = c.put(f"/api/formulas/{template_id}/coverage/Europe", json={
        "base_price": base_price, "currency": currency,
        "base_year": base_year, "base_quarter": base_quarter,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _resolved_components_payload(idx_id, index_weight=0.6, fixed_weight=0.4):
    """The initial snapshot a caller would save alongside a catalog link —
    shaped like what /formulas/{id}/resolve returns, converted to fractions."""
    return [
        {"label": "Feedstock", "commodity_id": idx_id, "component_type": "index", "weight": index_weight},
        {"label": "Fixed", "component_type": "fixed", "weight": fixed_weight},
    ]


def _create_cost_model(c, team_id, product_id, region="Europe", currency="EUR", **formula_extra):
    formula = {
        "formula_type": "simple",
        "base_price": 1000,
        "base_year": 2025,
        "base_quarter": 1,
        "margin_type": "pct",
        "margin_value": 0,
        "components": _resolved_components_payload(formula_extra.pop("idx_id")),
        **formula_extra,
    }
    r = c.post(f"/api/cost-models/?team_id={team_id}", json={
        "product_id": str(product_id), "region": region, "currency": currency, "formula": formula,
    })
    assert r.status_code == 201, r.text
    return r.json()


# ── AC1 — source_coverage_id + link_mode round-trip ─────────────────────────

def test_create_records_source_coverage_and_link_mode(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    t = _mk_template(db, "linkable", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    cov_id = _mk_coverage_with_lines(c, tenant_a["team_id"], t.id, idx.id)
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"])
    cm_id = None
    try:
        cm = _create_cost_model(c, tenant_a["team_id"], product.id, idx_id=idx.id,
                                 source_coverage_id=cov_id, link_mode="tracking")
        cm_id = cm["id"]
        fv = cm["formula_versions"][0]
        assert fv["source_coverage_id"] == cov_id
        assert fv["link_mode"] == "tracking"

        r = c.get(f"/api/cost-models/{cm_id}")
        assert r.status_code == 200
        fv2 = r.json()["formula_versions"][0]
        assert fv2["source_coverage_id"] == cov_id and fv2["link_mode"] == "tracking"
    finally:
        _cleanup(db, [cm_id] if cm_id else [], [t.id], [idx.id], [product.id])


def test_source_coverage_and_link_mode_must_be_set_together(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"])
    c = client_as(tenant_a)
    try:
        formula = {
            "formula_type": "simple", "base_price": 1000, "base_year": 2025, "base_quarter": 1,
            "margin_type": "pct", "margin_value": 0,
            "components": _resolved_components_payload(idx.id),
            "link_mode": "tracking",  # source_coverage_id omitted
        }
        r = c.post(f"/api/cost-models/?team_id={tenant_a['team_id']}", json={
            "product_id": str(product.id), "region": "Europe", "currency": "EUR", "formula": formula,
        })
        assert r.status_code == 422
    finally:
        _cleanup(db, [], [], [idx.id], [product.id])


# ── AC2 — tracking tracks a live edit; pinned doesn't ───────────────────────

def test_tracking_should_cost_changes_pinned_does_not(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    db.add_all([
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=1, value=100),
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=3, value=110),
    ])
    db.commit()
    t = _mk_template(db, "tracked-recipe", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    cov_id = _mk_coverage_with_lines(c, tenant_a["team_id"], t.id, idx.id, index_weight=60, fixed_weight=40)
    p_track = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "Tracking product")
    p_pin = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "Pinned product")
    cm_ids = []
    try:
        cm_track = _create_cost_model(c, tenant_a["team_id"], p_track.id, idx_id=idx.id,
                                       source_coverage_id=cov_id, link_mode="tracking")
        cm_pin = _create_cost_model(c, tenant_a["team_id"], p_pin.id, idx_id=idx.id,
                                     source_coverage_id=cov_id, link_mode="pinned")
        cm_ids = [cm_track["id"], cm_pin["id"]]

        def _should_cost(cm_id):
            r = c.post("/api/costing/should-cost", json={
                "cost_model_id": cm_id, "target_year": 2025, "target_quarter": 3,
            })
            assert r.status_code == 200, r.text
            return r.json()["should_cost"]

        # 60/40 @ ratio 1.1/1.0 -> 1000 * (0.6*1.1 + 0.4*1.0) = 1060
        assert _should_cost(cm_track["id"]) == 1060.0
        assert _should_cost(cm_pin["id"]) == 1060.0

        # Edit the child template's recipe: 60/40 -> 80/20
        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Feedstock", "component_type": "index", "commodity_id": idx.id, "weight_pct": 80},
            {"name": "Fixed", "component_type": "fixed", "weight_pct": 20},
        ]}).status_code == 200

        # Tracking follows the live recipe: 1000 * (0.8*1.1 + 0.2*1.0) = 1080
        assert _should_cost(cm_track["id"]) == 1080.0
        # Pinned is frozen at its saved snapshot: still 1060
        assert _should_cost(cm_pin["id"]) == 1060.0

        # link_mode round-trips correctly for both
        assert c.get(f"/api/cost-models/{cm_track['id']}").json()["formula_versions"][0]["link_mode"] == "tracking"
        assert c.get(f"/api/cost-models/{cm_pin['id']}").json()["formula_versions"][0]["link_mode"] == "pinned"
    finally:
        _cleanup(db, cm_ids, [t.id], [idx.id], [p_track.id, p_pin.id])


# ── AC3 — tracking breakdown provenance matches /resolve for a chained template ──

def test_tracking_breakdown_matches_resolve_for_chained_template(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    child = _mk_template(db, "child-chain", tenant_a["user_id"], team_id=tenant_a["team_id"])
    parent = _mk_template(db, "parent-chain", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"])
    cm_id = None
    try:
        assert c.put(f"/api/formulas/{child.id}/components", json={"components": [
            {"name": "Raw", "component_type": "index", "commodity_id": idx.id, "weight_pct": 50},
            {"name": "Child fixed", "component_type": "fixed", "weight_pct": 50},
        ]}).status_code == 200
        assert c.put(f"/api/formulas/{parent.id}/components", json={"components": [
            {"name": "Base", "component_type": "formula",
             "input_template_id": str(child.id), "weight_pct": 60},
            {"name": "Parent fixed", "component_type": "fixed", "weight_pct": 40},
        ]}).status_code == 200
        r = c.put(f"/api/formulas/{parent.id}/coverage/Europe", json={
            "base_price": 1000, "currency": "EUR", "base_year": 2025, "base_quarter": 1,
        })
        assert r.status_code == 200, r.text
        cov_id = r.json()["id"]

        cm = _create_cost_model(c, tenant_a["team_id"], product.id, idx_id=idx.id,
                                 source_coverage_id=cov_id, link_mode="tracking")
        cm_id = cm["id"]

        resolve = c.get(f"/api/formulas/{parent.id}/resolve",
                         params={"region": "Europe", "team_id": str(tenant_a["team_id"])})
        assert resolve.status_code == 200, resolve.text
        resolve_by_name = {l["name"]: l for l in resolve.json()["lines"]}

        brk = c.post("/api/costing/should-cost/breakdown", json={"cost_model_id": cm_id})
        assert brk.status_code == 200, brk.text
        by_label = {x["label"]: x for x in brk.json()["components"]}

        for name in ("Raw", "Child fixed", "Parent fixed"):
            resolved_line = resolve_by_name[name]
            brk_line = by_label[name]
            assert brk_line["depth"] == resolved_line["depth"]
            assert brk_line["via_template_id"] == str(resolved_line["via_template_id"])
            assert brk_line["via_template_name"] == resolved_line["via_template_name"]
            assert brk_line["line_region"] == resolved_line["line_region"]
        # The chained line is actually exercised at depth > 0, not just depth 0.
        assert resolve_by_name["Raw"]["depth"] == 1
        assert by_label["Raw"]["depth"] == 1
    finally:
        _cleanup(db, [cm_id] if cm_id else [], [parent.id, child.id], [idx.id], [product.id])


# ── AC4 — broken index link surfaces a gap; a deliberately fixed line doesn't ──

def test_broken_index_link_reports_gap_vs_deliberately_fixed(db, tenant_a, client_as):
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"])
    c = client_as(tenant_a)
    cm_id = None
    try:
        formula = {
            "formula_type": "simple", "base_price": 1000, "base_year": 2025, "base_quarter": 1,
            "margin_type": "pct", "margin_value": 0,
            "components": [
                # commodity_name doesn't match anything real -> commodity_id stays
                # None, but component_type="index" was explicit intent.
                {"label": "Broken Link", "commodity_name": "Nonexistent Commodity XYZ",
                 "component_type": "index", "weight": 0.5},
                {"label": "Logistics", "component_type": "fixed", "weight": 0.5},
            ],
        }
        r = c.post(f"/api/cost-models/?team_id={tenant_a['team_id']}", json={
            "product_id": str(product.id), "region": "Europe", "currency": "EUR", "formula": formula,
        })
        assert r.status_code == 201, r.text
        cm_id = r.json()["id"]

        brk = c.post("/api/costing/should-cost/breakdown", json={"cost_model_id": cm_id})
        assert brk.status_code == 200, brk.text
        body = brk.json()
        gap_labels = {g["component_label"] for g in body["data_gaps"]}
        assert "Broken Link" in gap_labels
        assert "Logistics" not in gap_labels

        by_label = {x["label"]: x for x in body["components"]}
        assert by_label["Broken Link"]["has_data"] is False
        assert by_label["Logistics"]["has_data"] is True
    finally:
        _cleanup(db, [cm_id] if cm_id else [], [], [], [product.id])


# ── AC5 — non-round weights survive to full DB precision ───────────────────

def test_precise_weights_are_not_rounded(db, tenant_a, client_as):
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"])
    c = client_as(tenant_a)
    cm_id = None
    try:
        formula = {
            "formula_type": "simple", "base_price": 1000, "base_year": 2025, "base_quarter": 1,
            "margin_type": "pct", "margin_value": 0,
            "components": [
                {"label": "A", "component_type": "fixed", "weight": 0.3333},
                {"label": "B", "component_type": "fixed", "weight": 0.6667},
            ],
        }
        r = c.post(f"/api/cost-models/?team_id={tenant_a['team_id']}", json={
            "product_id": str(product.id), "region": "Europe", "currency": "EUR", "formula": formula,
        })
        assert r.status_code == 201, r.text
        cm_id = r.json()["id"]

        by_label = {x["label"]: x["weight"] for x in r.json()["formula_versions"][0]["components"]}
        # Not collapsed to one decimal (the old Math.round(x*10)/10 frontend bug).
        assert by_label["A"] == 0.3333
        assert by_label["B"] == 0.6667
    finally:
        _cleanup(db, [cm_id] if cm_id else [], [], [], [product.id])


# ── AC6 — unlinked (link_mode=None) formulas are unaffected ────────────────

def test_unlinked_cost_model_should_cost_regression(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    db.add_all([
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=1, value=100),
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=3, value=125),
    ])
    db.commit()
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"])
    c = client_as(tenant_a)
    cm_id = None
    try:
        formula = {
            "formula_type": "simple", "base_price": 2000, "base_year": 2025, "base_quarter": 1,
            "margin_type": "pct", "margin_value": 10,
            "components": [
                {"label": "Feedstock", "commodity_id": idx.id, "component_type": "index", "weight": 0.7},
                {"label": "Conversion", "component_type": "fixed", "weight": 0.3},
            ],
        }
        r = c.post(f"/api/cost-models/?team_id={tenant_a['team_id']}", json={
            "product_id": str(product.id), "region": "Europe", "currency": "EUR", "formula": formula,
        })
        assert r.status_code == 201, r.text
        cm_id = r.json()["id"]
        fv = r.json()["formula_versions"][0]
        assert fv["source_coverage_id"] is None and fv["link_mode"] is None

        r = c.post("/api/costing/should-cost", json={
            "cost_model_id": cm_id, "target_year": 2025, "target_quarter": 3,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        # comp_base = 2000*(1-0.10) = 1800; indexed = 1800*(0.7*1.25+0.3*1.0) = 1800*1.175 = 2115
        # should_cost = indexed / (1-0.10) = 2115 / 0.9 = 2350.0
        assert body["cost_before_margin"] == 2115.0
        assert body["should_cost"] == 2350.0
        assert body["data_gaps"] == []
    finally:
        _cleanup(db, [cm_id] if cm_id else [], [], [idx.id], [product.id])


# ── Full-scope propagation: Evolution / Brief / Price-Change ───────────────

def test_tracking_propagates_across_evolution_brief_price_change(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    db.add_all([
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=1, value=100),
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=3, value=110),
    ])
    db.commit()
    t = _mk_template(db, "propagate-recipe", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    cov_id = _mk_coverage_with_lines(c, tenant_a["team_id"], t.id, idx.id, index_weight=60, fixed_weight=40)
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"])
    cm_id = None
    try:
        cm = _create_cost_model(c, tenant_a["team_id"], product.id, idx_id=idx.id,
                                 source_coverage_id=cov_id, link_mode="tracking")
        cm_id = cm["id"]

        def _evo_q3_theoretical():
            r = c.post("/api/costing/evolution", json={
                "cost_model_id": cm_id,
                "from_year": 2025, "from_quarter": 1, "to_year": 2025, "to_quarter": 3,
            })
            assert r.status_code == 200, r.text
            return [p for p in r.json()["periods"] if p["year"] == 2025 and p["quarter"] == 3][0]["theoretical"]

        def _brief_feedstock_cost():
            r = c.post("/api/costing/brief", json={
                "cost_model_id": cm_id,
                "from_year": 2025, "from_quarter": 1, "to_year": 2025, "to_quarter": 3,
            })
            assert r.status_code == 200, r.text
            drivers = {d["component_label"]: d for d in r.json()["drivers"]}
            return drivers["Feedstock"]["component_cost"]

        def _price_change_feedstock_weight():
            r = c.post("/api/costing/price-change", json={
                "cost_model_id": cm_id,
                "from_year": 2025, "from_quarter": 1, "to_year": 2025, "to_quarter": 3,
            })
            assert r.status_code == 200, r.text
            comps = {x["label"]: x for x in r.json()["components"]}
            return comps["Feedstock"]["weight"]

        evo_before = _evo_q3_theoretical()
        brief_before = _brief_feedstock_cost()
        pc_before = _price_change_feedstock_weight()
        assert pc_before == 60.0

        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Feedstock", "component_type": "index", "commodity_id": idx.id, "weight_pct": 80},
            {"name": "Fixed", "component_type": "fixed", "weight_pct": 20},
        ]}).status_code == 200

        evo_after = _evo_q3_theoretical()
        brief_after = _brief_feedstock_cost()
        pc_after = _price_change_feedstock_weight()

        assert evo_after != evo_before
        assert brief_after != brief_before
        assert pc_after == 80.0
    finally:
        _cleanup(db, [cm_id] if cm_id else [], [t.id], [idx.id], [product.id])


# ── Graceful fallback when the linked coverage disappears ──────────────────

def test_fallback_when_coverage_deleted(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    t = _mk_template(db, "vanishing-recipe", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    cov_id = _mk_coverage_with_lines(c, tenant_a["team_id"], t.id, idx.id)
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"])
    cm_id = None
    try:
        cm = _create_cost_model(c, tenant_a["team_id"], product.id, idx_id=idx.id,
                                 source_coverage_id=cov_id, link_mode="tracking")
        cm_id = cm["id"]

        assert c.delete(f"/api/formulas/{t.id}/coverage/Europe").status_code == 200

        r = c.post("/api/costing/should-cost/breakdown", json={"cost_model_id": cm_id})
        assert r.status_code == 200, r.text
        body = r.json()
        assert any("tracking link unavailable" in g["reason"] for g in body["data_gaps"])
        # Still computes from the last-known snapshot, never a 500 or a blank result.
        assert body["should_cost"] > 0
    finally:
        _cleanup(db, [cm_id] if cm_id else [], [t.id], [idx.id], [product.id])


# ── Visibility: cannot link another team's private coverage ────────────────

def test_cannot_link_another_teams_coverage(db, tenant_a, tenant_b, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    t_b = _mk_template(db, "b-private-recipe", tenant_b["user_id"], team_id=tenant_b["team_id"])
    c_b = client_as(tenant_b)
    cov_id = _mk_coverage_with_lines(c_b, tenant_b["team_id"], t_b.id, idx.id)
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"])
    c_a = client_as(tenant_a)
    try:
        formula = {
            "formula_type": "simple", "base_price": 1000, "base_year": 2025, "base_quarter": 1,
            "margin_type": "pct", "margin_value": 0,
            "components": _resolved_components_payload(idx.id),
            "source_coverage_id": cov_id, "link_mode": "tracking",
        }
        r = c_a.post(f"/api/cost-models/?team_id={tenant_a['team_id']}", json={
            "product_id": str(product.id), "region": "Europe", "currency": "EUR", "formula": formula,
        })
        assert r.status_code == 400
    finally:
        _cleanup(db, [], [t_b.id], [idx.id], [product.id])


# ── clone_cost_model preserves the link + provenance ────────────────────────

def test_clone_preserves_link_mode_and_provenance(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    t = _mk_template(db, "clone-recipe", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    cov_id = _mk_coverage_with_lines(c, tenant_a["team_id"], t.id, idx.id)
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"])
    cm_ids = []
    try:
        cm = _create_cost_model(c, tenant_a["team_id"], product.id, idx_id=idx.id,
                                 source_coverage_id=cov_id, link_mode="tracking")
        cm_ids.append(cm["id"])

        r = c.post(f"/api/cost-models/{cm['id']}/clone")
        assert r.status_code == 201, r.text
        clone = r.json()
        cm_ids.append(clone["id"])

        clone_fv = clone["formula_versions"][0]
        assert clone_fv["source_coverage_id"] == cov_id
        assert clone_fv["link_mode"] == "tracking"

        orig_by_label = {x["label"]: x for x in cm["formula_versions"][0]["components"]}
        clone_by_label = {x["label"]: x for x in clone_fv["components"]}
        assert orig_by_label and set(orig_by_label) == set(clone_by_label)
        for label, orig in orig_by_label.items():
            cloned = clone_by_label[label]
            assert cloned["component_type"] == orig["component_type"]
            assert cloned["commodity_id"] == orig["commodity_id"]
    finally:
        _cleanup(db, cm_ids, [t.id], [idx.id], [product.id])
