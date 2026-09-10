"""Cost-structure estimator for combos with no usable decomposition (Scrum 33).

Covers every acceptance criterion:
- AC1: callable service returns a proposed line set + per-line reasoning,
  never mutating the existing recipe.
- AC2: persisted as an ai_draft, lands in the existing review state machine;
  approving is what makes the combo priceable.
- AC3: a candidate with no usable series is flagged, not silently included.
- AC4: a backtest report over combos that already have lines, inspectable
  via an endpoint.
- AC5: re-running doesn't create duplicate drafts.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.formula_estimator import EstimatorProposal
from app.models.formula_template import FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent
from app.models.index_data import CommodityIndex, IndexValue
from app.models.price_data import ActualPrice
from app.models.product import Product
from app.services.formula_estimator import propose_recipe, run_backtest
from app.services.formula_resolver import evaluate_weighted_template


def _mk_template(db, name, created_by, team_id=None) -> FormulaTemplate:
    t = FormulaTemplate(team_id=team_id, created_by=created_by, name=name, expression=None)
    db.add(t)
    db.commit()
    return t


def _mk_index(db, name, role=None, retrieval_status=None) -> CommodityIndex:
    idx = CommodityIndex(name=name, unit="$/mt", currency="USD", scrape_enabled=False,
                          role=role, retrieval_status=retrieval_status)
    db.add(idx)
    db.commit()
    return idx


def _mk_coverage(db, template_id, region, data_confidence, base_price=1000, base_year=2025, base_quarter=1):
    cov = FormulaRegionCoverage(template_id=template_id, region=region, base_price=base_price,
                                 base_year=base_year, base_quarter=base_quarter,
                                 data_confidence=data_confidence,
                                 needs_review=(data_confidence == "CONF-LOW"))
    db.add(cov)
    db.commit()
    return cov


def _mk_component(db, template_id, region, name, weight_pct, commodity_id=None, component_type="index"):
    c = FormulaTemplateComponent(template_id=template_id, region=region, name=name,
                                  component_type=component_type, commodity_id=commodity_id,
                                  weight_pct=weight_pct)
    db.add(c)
    db.commit()
    return c


def _cleanup(db, template_ids=(), commodity_ids=(), product_ids=(), cost_model_ids=()):
    bypass_rls_var.set(True)
    for cmid in cost_model_ids:
        db.execute(text("DELETE FROM cost_models WHERE id = :id"), {"id": str(cmid)})
    for pid in product_ids:
        db.execute(text("DELETE FROM products WHERE id = :id"), {"id": str(pid)})
    for tid in template_ids:
        db.execute(text("DELETE FROM estimator_proposals WHERE template_id = :id"), {"id": str(tid)})
        db.execute(text("DELETE FROM formula_templates WHERE id = :id"), {"id": str(tid)})
    for cid in commodity_ids:
        db.execute(text("DELETE FROM index_values WHERE commodity_id = :id"), {"id": cid})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :id"), {"id": cid})
    db.commit()


# ── Sibling-region inheritance (AC1, AC3) ───────────────────────────────────

def test_sibling_inheritance(db, tenant_a, client_as):
    idx_a = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    idx_b = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")  # no data anywhere -> unavailable in target
    t = _mk_template(db, "sibling-test", tenant_a["user_id"], team_id=tenant_a["team_id"])
    _mk_coverage(db, t.id, "Europe", "CONF-HIGH")
    _mk_component(db, t.id, "Europe", "Feedstock A", 60, commodity_id=idx_a.id)
    _mk_component(db, t.id, "Europe", "Feedstock B", 40, commodity_id=idx_b.id)
    db.add(IndexValue(commodity_id=idx_a.id, region="NA", year=2025, quarter=1, value=100))
    db.commit()

    c = client_as(tenant_a)
    try:
        r = c.post(f"/api/formulas/{t.id}/estimator/propose", params={"region": "NA"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ai_draft"
        assert body["evidence_summary"]["method"] == "sibling_region"
        assert body["evidence_summary"]["source_region"] == "Europe"

        by_name = {l["name"]: l for l in body["lines"]}
        assert by_name["Feedstock A"]["weight_pct"] == 60.0
        assert by_name["Feedstock A"]["series_available"] is True
        assert "Europe" in by_name["Feedstock A"]["candidate_reason"]
        # AC3 — flagged, not silently excluded.
        assert by_name["Feedstock B"]["series_available"] is False
        assert by_name["Feedstock B"] in body["lines"]

        # AC1 — the live recipe for NA is still untouched.
        assert FormulaTemplateComponent().__class__
        live = db.query(FormulaTemplateComponent).filter(
            FormulaTemplateComponent.template_id == t.id, FormulaTemplateComponent.region == "NA",
        ).count()
        assert live == 0
    finally:
        _cleanup(db, [t.id], [idx_a.id, idx_b.id])


def test_no_evidence_returns_evaluable_false(db, tenant_a, client_as):
    t = _mk_template(db, "no-evidence", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    try:
        r = c.post(f"/api/formulas/{t.id}/estimator/propose", params={"region": "Europe"})
        assert r.status_code == 400
        assert "no sibling region recipe and no priced history" in r.json()["detail"]
    finally:
        _cleanup(db, [t.id])


# ── Blind correlation fallback (AC1) ────────────────────────────────────────

def test_blind_correlation_with_linked_price_history(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}", role="feedstock")
    # Year 2099 — no real system data will coincidentally have a non-flat
    # series there, so this is deterministic regardless of the shared dev DB.
    for q, val in enumerate([100, 110, 120, 130], start=1):
        db.add(IndexValue(commodity_id=idx.id, region="Europe", year=2099, quarter=q, value=val))
    db.commit()

    t = _mk_template(db, "blind-corr", tenant_a["user_id"], team_id=tenant_a["team_id"])
    cov = _mk_coverage(db, t.id, "Europe", None, base_price=1000, base_year=2099, base_quarter=1)
    product = Product(team_id=tenant_a["team_id"], created_by=tenant_a["user_id"], name="P", unit="kg")
    db.add(product)
    db.flush()
    cm = CostModel(team_id=tenant_a["team_id"], product_id=product.id, region="Europe",
                    currency="USD", created_by=tenant_a["user_id"])
    db.add(cm)
    db.flush()
    fv = FormulaVersion(cost_model_id=cm.id, base_price=100, base_year=2099, base_quarter=1,
                         margin_type="pct", margin_value=0, formula_type="simple",
                         source_coverage_id=cov.id, link_mode="tracking")
    db.add(fv)
    db.flush()
    db.add(FormulaComponent(formula_version_id=fv.id, label="Fixed", weight=1.0, component_type="fixed"))
    db.commit()
    for q, price in zip([1, 2, 3, 4], [50, 55, 60, 65]):  # exactly idx/2 -> r=1.0
        db.add(ActualPrice(cost_model_id=cm.id, uploaded_by=tenant_a["user_id"], year=2099, quarter=q, price=price))
    db.commit()

    c = client_as(tenant_a)
    try:
        r = c.post(f"/api/formulas/{t.id}/estimator/propose", params={"region": "Europe"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["evidence_summary"]["method"] == "correlation"
        assert body["evidence_summary"]["priced_history_quarters"] == 4

        commodity_ids = {l["commodity_id"] for l in body["lines"]}
        assert idx.id in commodity_ids
        total = sum(l["weight_pct"] for l in body["lines"])
        assert round(total, 2) == 100.0
        assert any(l["component_type"] == "fixed" and l["name"] == "Margin / unexplained" for l in body["lines"])
    finally:
        _cleanup(db, [t.id], [idx.id], [product.id], [cm.id])


# ── Persistence, re-run, approval (AC2, AC5) ────────────────────────────────

def test_rerun_upserts_and_approve_makes_priceable(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    t = _mk_template(db, "approve-test", tenant_a["user_id"], team_id=tenant_a["team_id"])
    _mk_coverage(db, t.id, "Europe", "CONF-HIGH")
    _mk_component(db, t.id, "Europe", "Feedstock", 100, commodity_id=idx.id)
    # NA already has a real base-price anchor but no trustworthy recipe yet
    # (the CONF-LOW-placeholder-or-empty scenario this scrum targets) — the
    # base period must pre-exist so approval doesn't have to invent one.
    _mk_coverage(db, t.id, "NA", "CONF-LOW", base_price=500, base_year=2025, base_quarter=1)
    db.add(IndexValue(commodity_id=idx.id, region="NA", year=2025, quarter=1, value=100))
    db.commit()

    c = client_as(tenant_a)
    try:
        r1 = c.post(f"/api/formulas/{t.id}/estimator/propose", params={"region": "NA"})
        proposal_id = r1.json()["id"]

        r2 = c.post(f"/api/formulas/{t.id}/estimator/propose", params={"region": "NA"})
        assert r2.json()["id"] == proposal_id  # same row, no duplicate (AC5)
        assert db.query(EstimatorProposal).filter(EstimatorProposal.template_id == t.id).count() == 1

        r3 = c.post(f"/api/formulas/estimator/proposals/{proposal_id}/approve")
        assert r3.status_code == 200, r3.text
        cov_out = r3.json()
        assert cov_out["needs_review"] is False

        db.expire_all()
        live = db.query(FormulaTemplateComponent).filter(
            FormulaTemplateComponent.template_id == t.id, FormulaTemplateComponent.region == "NA",
        ).all()
        assert len(live) == 1 and live[0].commodity_id == idx.id

        cov = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.template_id == t.id, FormulaRegionCoverage.region == "NA",
        ).first()
        assert cov.provenance == "human_approved"

        # Now priceable end to end.
        result = evaluate_weighted_template(db, tenant_a["team_id"], t.id, "NA", 2025, 1)
        assert result["evaluable"] is True

        # Re-running against an already-approved combo is a no-op, not an error.
        r4 = c.post(f"/api/formulas/{t.id}/estimator/propose", params={"region": "NA"})
        assert r4.status_code == 200
        assert r4.json()["status"] == "human_approved"

        # Approving twice fails cleanly.
        r5 = c.post(f"/api/formulas/estimator/proposals/{proposal_id}/approve")
        assert r5.status_code == 400
    finally:
        _cleanup(db, [t.id], [idx.id])


def test_reject_then_cannot_reapprove(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    t = _mk_template(db, "reject-test", tenant_a["user_id"], team_id=tenant_a["team_id"])
    _mk_coverage(db, t.id, "Europe", "CONF-HIGH")
    _mk_component(db, t.id, "Europe", "Feedstock", 100, commodity_id=idx.id)
    db.commit()

    c = client_as(tenant_a)
    try:
        r1 = c.post(f"/api/formulas/{t.id}/estimator/propose", params={"region": "NA"})
        proposal_id = r1.json()["id"]
        r2 = c.post(f"/api/formulas/estimator/proposals/{proposal_id}/reject")
        assert r2.status_code == 200
        r3 = c.post(f"/api/formulas/estimator/proposals/{proposal_id}/approve")
        assert r3.status_code == 400
    finally:
        _cleanup(db, [t.id], [idx.id])


# ── Backtest (AC4) ───────────────────────────────────────────────────────────

def test_backtest_non_circular(db, tenant_a, user_factory, client_as):
    sa = user_factory(is_super_admin=True)
    idx_a = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    t = _mk_template(db, "backtest-test", sa["user_id"])  # platform template
    _mk_coverage(db, t.id, "Europe", "CONF-HIGH")
    _mk_component(db, t.id, "Europe", "Feedstock", 60, commodity_id=idx_a.id)
    _mk_component(db, t.id, "Europe", "Fixed", 40, component_type="fixed")
    _mk_coverage(db, t.id, "NA", "CONF-HIGH")
    _mk_component(db, t.id, "NA", "Feedstock", 60, commodity_id=idx_a.id)
    _mk_component(db, t.id, "NA", "Fixed", 40, component_type="fixed")
    db.add(IndexValue(commodity_id=idx_a.id, region="Europe", year=2025, quarter=1, value=100))
    db.add(IndexValue(commodity_id=idx_a.id, region="NA", year=2025, quarter=1, value=100))
    db.commit()

    c = client_as(sa)
    try:
        r = c.get("/api/formulas/estimator/backtest", params={"template_id": str(t.id)})
        assert r.status_code == 200, r.text
        body = r.json()
        combos = {(x["region"]): x for x in body["combos"]}
        assert combos["Europe"]["evaluable"] is True
        assert combos["Europe"]["match_fraction"] == 1.0  # NA's real recipe was the sibling evidence
        assert combos["NA"]["evaluable"] is True
        assert combos["NA"]["match_fraction"] == 1.0  # Europe's real recipe was the sibling evidence

        # Remove the only other sibling (NA) -> Europe now has nothing to
        # inherit from and no priced history -> non-circular: its OWN real
        # lines were never used as their own evidence.
        db.query(FormulaTemplateComponent).filter(
            FormulaTemplateComponent.template_id == t.id, FormulaTemplateComponent.region == "NA",
        ).delete()
        db.commit()
        r2 = c.get("/api/formulas/estimator/backtest", params={"template_id": str(t.id)})
        combos2 = {(x["region"]): x for x in r2.json()["combos"]}
        assert combos2["Europe"]["evaluable"] is False
        assert combos2["Europe"]["match_fraction"] == 0.0
    finally:
        _cleanup(db, [t.id], [idx_a.id])


# ── Permission gates ─────────────────────────────────────────────────────────

def test_permission_gates(db, tenant_a, tenant_b, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    t = _mk_template(db, "perm-test", tenant_a["user_id"], team_id=tenant_a["team_id"])
    _mk_coverage(db, t.id, "Europe", "CONF-HIGH")
    _mk_component(db, t.id, "Europe", "Feedstock", 100, commodity_id=idx.id)
    db.commit()

    try:
        r = client_as(tenant_b).post(f"/api/formulas/{t.id}/estimator/propose", params={"region": "NA"})
        assert r.status_code in (403, 404)

        r = client_as(tenant_b).get("/api/formulas/estimator/backtest")
        assert r.status_code == 403
    finally:
        _cleanup(db, [t.id], [idx.id])
