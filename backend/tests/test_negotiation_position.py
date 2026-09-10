"""Negotiation position engine (Scrum 30b) — target/ask/unexplained remainder
off the existing weighted-evaluation "attribution service"
(formula_resolver.evaluate_weighted_template), never a fabricated supplier
counter.

Covers:
- Target/ask/attribution/remainder shape, and the sum identity the AC
  requires (attributed + unexplained == the total difference).
- Traceability of a directly-indexed line (base/current value, ratio).
- Proxy and no-data lines marked distinctly, never presented at the same
  strength as a directly-indexed line.
- Evidence is always None (no editorial/dossier data model exists here).
- A combo with no base-price anchor still returns a movement-based position,
  not an error.
- Currency/unit/Incoterm normalization, including the honest "not corrected,
  here's why" cases when data is missing.
- Permission/visibility/validation gates, and audit logging.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.formula_template import FormulaTemplate
from app.models.index_data import CommodityIndex, IndexValue


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


def _cleanup(db, template_ids=(), commodity_ids=()):
    bypass_rls_var.set(True)
    for tid in template_ids:
        db.execute(text("DELETE FROM formula_templates WHERE id = :id"), {"id": str(tid)})
    for cid in commodity_ids:
        db.execute(text("DELETE FROM index_values WHERE commodity_id = :id"), {"id": cid})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :id"), {"id": cid})
    db.commit()


def _base_url(t):
    return f"/api/formulas/{t.id}/negotiation-position"


# ── Basic shape + identity ──────────────────────────────────────────────────

def test_basic_position_identity(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    db.add_all([
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=1, value=100),
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=3, value=110),
    ])
    db.commit()
    t = _mk_template(db, "pos-basic", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Feedstock", "component_type": "index", "commodity_id": idx.id, "weight_pct": 60},
            {"name": "Fixed", "component_type": "fixed", "weight_pct": 40},
        ]}).status_code == 200
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_price": 1000, "currency": "EUR", "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        # target = 1000 * (0.6*1.1 + 0.4*1.0) = 1060; supplier asks for 1150
        r = c.get(_base_url(t), params={**q, "year": 2025, "quarter": 3, "supplier_price": 1150})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target"]["should_cost"] == 1060.0
        pos = body["position"]
        assert pos["insufficient"] is False
        assert pos["ask"] == 90.0
        attributed_sum = sum(l["attributed_amount"] for l in pos["attributed_components"])
        assert round(attributed_sum + pos["unexplained_remainder"], 4) == pos["ask"]
        assert pos["attributed_total"] == attributed_sum == 0.0
        assert pos["unexplained_remainder"] == 90.0
        assert len(pos["attributed_components"]) == 2
    finally:
        _cleanup(db, [t.id], [idx.id])


def test_traceability(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    db.add_all([
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=1, value=100),
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=2, value=112),
    ])
    db.commit()
    t = _mk_template(db, "pos-trace", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Feedstock", "component_type": "index", "commodity_id": idx.id, "weight_pct": 100},
        ]}).status_code == 200
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_price": 500, "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        r = c.get(_base_url(t), params={**q, "year": 2025, "quarter": 2, "supplier_price": 600})
        assert r.status_code == 200, r.text
        line = r.json()["position"]["attributed_components"][0]
        assert line["commodity_id"] == idx.id
        assert line["commodity_name"] == idx.name
        assert line["base_value"] == 100.0
        assert line["current_value"] == 112.0
        assert line["ratio"] == 1.12
        assert line["has_data"] is True
        assert line["component_id"] is not None
    finally:
        _cleanup(db, [t.id], [idx.id])


# ── Proxy / no-data lines marked distinctly ─────────────────────────────────

def test_proxy_and_no_data_lines_marked(db, tenant_a, client_as):
    real_idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    ghost_idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")  # no values at all
    db.add_all([
        IndexValue(commodity_id=real_idx.id, region="Europe", year=2025, quarter=1, value=100),
        IndexValue(commodity_id=real_idx.id, region="Europe", year=2025, quarter=2, value=105),
    ])
    db.commit()
    t = _mk_template(db, "pos-quality", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Direct", "component_type": "index", "commodity_id": real_idx.id, "weight_pct": 50},
            {"name": "Proxy", "component_type": "index", "commodity_id": real_idx.id,
             "weight_pct": 30, "is_proxy": True},
            {"name": "Ghost", "component_type": "index", "commodity_id": ghost_idx.id, "weight_pct": 20},
        ]}).status_code == 200
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_price": 1000, "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        r = c.get(_base_url(t), params={**q, "year": 2025, "quarter": 2, "supplier_price": 1200})
        assert r.status_code == 200, r.text
        by_name = {l["name"]: l for l in r.json()["position"]["attributed_components"]}
        assert by_name["Direct"]["is_proxy"] is False and by_name["Direct"]["has_data"] is True
        assert by_name["Proxy"]["is_proxy"] is True and by_name["Proxy"]["has_data"] is True
        assert by_name["Ghost"]["has_data"] is False and by_name["Ghost"]["ratio"] == 1.0
        gap_reasons = {g["line"] for g in r.json()["target"]["data_gaps"]}
        assert "Ghost" in gap_reasons
    finally:
        _cleanup(db, [t.id], [real_idx.id, ghost_idx.id])


# ── Evidence always None ────────────────────────────────────────────────────

def test_evidence_always_none(db, tenant_a, client_as):
    t = _mk_template(db, "pos-evidence", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Fixed", "component_type": "fixed", "weight_pct": 100},
        ]}).status_code == 200
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_price": 100, "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        r = c.get(_base_url(t), params={**q, "year": 2025, "quarter": 1, "supplier_price": 110})
        assert r.status_code == 200, r.text
        for line in r.json()["position"]["attributed_components"]:
            assert line["evidence"] is None
            assert line["attributed_amount"] == 0.0
    finally:
        _cleanup(db, [t.id])


# ── No base-price anchor: still a movement-based position, never an error ──

def test_no_base_price_returns_movement_position(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    db.add_all([
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=1, value=100),
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=2, value=108),
    ])
    db.commit()
    t = _mk_template(db, "pos-nobase", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Feedstock", "component_type": "index", "commodity_id": idx.id, "weight_pct": 100},
        ]}).status_code == 200
        # Coverage exists (base period anchored) but no base_price.
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        r = c.get(_base_url(t), params={**q, "year": 2025, "quarter": 2, "supplier_price": 500})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target"]["evaluable"] is True
        assert body["target"]["should_cost"] is None
        assert body["target"]["index_level_pct"] == 108.0
        assert body["position"]["insufficient"] is True
        assert body["position"]["ask"] is None
        assert body["position"]["unexplained_remainder"] is None
    finally:
        _cleanup(db, [t.id], [idx.id])


def test_fully_unevaluable_combo_still_200s(db, tenant_a, client_as):
    t = _mk_template(db, "pos-empty", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        r = c.get(_base_url(t), params={**q, "year": 2025, "quarter": 1, "supplier_price": 100})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target"]["evaluable"] is False
        assert body["normalization"] is None
        assert body["position"]["insufficient"] is True
    finally:
        _cleanup(db, [t.id])


# ── Normalization ────────────────────────────────────────────────────────────

def test_currency_normalization(db, tenant_a, client_as):
    from app.services.fx_converter import get_fx_rate

    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    db.add(IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=1, value=100))
    db.commit()
    t = _mk_template(db, "pos-fx", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Feedstock", "component_type": "index", "commodity_id": idx.id, "weight_pct": 100},
        ]}).status_code == 200
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_price": 1000, "currency": "EUR", "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        rate = get_fx_rate(db, "USD", "EUR", 2025, 1, team_id=tenant_a["team_id"])
        r = c.get(_base_url(t), params={
            **q, "year": 2025, "quarter": 1, "supplier_price": 1200, "supplier_currency": "USD",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        norm = body["normalization"]
        assert norm["supplier_currency"] == "USD"
        if rate is not None:
            assert norm["fx_rate_used"] == rate
            assert norm["normalized_price"] == round(1200 * rate, 4)
            assert body["position"]["ask"] == round(round(1200 * rate, 4) - 1000.0, 4)
            assert norm["notes"] == []
        else:
            assert norm["fx_rate_used"] is None
            assert norm["normalized_price"] == 1200.0
            assert any("no fx rate" in n.lower() for n in norm["notes"])
    finally:
        _cleanup(db, [t.id], [idx.id])


def test_unit_normalization(db, tenant_a, client_as):
    t = _mk_template(db, "pos-unit", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Fixed", "component_type": "fixed", "weight_pct": 100},
        ]}).status_code == 200
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_price": 3, "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        # Supplier quotes $3000/t; combo is priced per kg -> $3/kg, no ask.
        r = c.get(_base_url(t), params={
            **q, "year": 2025, "quarter": 1, "supplier_price": 3000,
            "supplier_unit": "t", "combo_unit": "kg",
        })
        assert r.status_code == 200, r.text
        norm = r.json()["normalization"]
        assert norm["unit_factor_used"] is not None
        assert norm["normalized_price"] == 3.0
        assert r.json()["position"]["ask"] == 0.0

        # One side unstated -> compared as quoted, with an explicit note.
        r = c.get(_base_url(t), params={
            **q, "year": 2025, "quarter": 1, "supplier_price": 3000, "supplier_unit": "t",
        })
        assert r.status_code == 200, r.text
        norm2 = r.json()["normalization"]
        assert norm2["unit_factor_used"] is None
        assert any("unit basis" in n.lower() for n in norm2["notes"])
    finally:
        _cleanup(db, [t.id])


def test_incoterm_mismatch(db, tenant_a, client_as):
    t = _mk_template(db, "pos-incoterm", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Fixed", "component_type": "fixed", "weight_pct": 100},
        ]}).status_code == 200
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_price": 100, "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        # Mismatch, no adjustment data -> compared as-is, explicit note.
        r = c.get(_base_url(t), params={
            **q, "year": 2025, "quarter": 1, "supplier_price": 110,
            "supplier_incoterm": "CIF", "combo_incoterm": "FOB",
        })
        assert r.status_code == 200, r.text
        norm = r.json()["normalization"]
        assert norm["incoterm_adjustment"] is None
        assert norm["normalized_price"] == 110.0
        assert any("incoterm differs" in n.lower() for n in norm["notes"])

        # Mismatch with adjustment data -> real correction applied.
        r = c.get(_base_url(t), params={
            **q, "year": 2025, "quarter": 1, "supplier_price": 110,
            "supplier_incoterm": "CIF", "combo_incoterm": "FOB",
            "incoterm_adjustments": '{"main_freight": {"type": "flat", "value": 10}}',
        })
        assert r.status_code == 200, r.text
        norm2 = r.json()["normalization"]
        assert norm2["incoterm_adjustment"] is not None
        assert norm2["normalized_price"] != 110.0
    finally:
        _cleanup(db, [t.id])


# ── Chained template ─────────────────────────────────────────────────────────

def test_chained_template(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    db.add_all([
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=1, value=100),
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=2, value=120),
    ])
    db.commit()
    child = _mk_template(db, "pos-child", tenant_a["user_id"], team_id=tenant_a["team_id"])
    parent = _mk_template(db, "pos-parent", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        assert c.put(f"/api/formulas/{child.id}/components", json={"components": [
            {"name": "Raw", "component_type": "index", "commodity_id": idx.id, "weight_pct": 100},
        ]}).status_code == 200
        assert c.put(f"/api/formulas/{parent.id}/components", json={"components": [
            {"name": "Base", "component_type": "formula",
             "input_template_id": str(child.id), "weight_pct": 100},
        ]}).status_code == 200
        assert c.put(f"/api/formulas/{parent.id}/coverage/Europe", json={
            "base_price": 1000, "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        r = c.get(_base_url(parent), params={**q, "year": 2025, "quarter": 2, "supplier_price": 1300})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target"]["should_cost"] == 1200.0
        line = body["position"]["attributed_components"][0]
        assert line["depth"] == 1
        assert line["via_template_name"] == "pos-child"
        assert body["position"]["ask"] == 100.0
    finally:
        _cleanup(db, [parent.id, child.id], [idx.id])


# ── Permission / visibility / validation ────────────────────────────────────

def test_permission_and_validation_gates(db, tenant_a, tenant_b, client_as):
    t = _mk_template(db, "pos-gates", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        # Invalid period
        r = c.get(_base_url(t), params={**q, "year": 2025, "quarter": 9, "supplier_price": 100})
        assert r.status_code == 400

        # tenant_b addresses their own (valid) team but a template that
        # belongs to tenant_a — permission passes, visibility hides it (404).
        r = client_as(tenant_b).get(_base_url(t), params={
            "team_id": str(tenant_b["team_id"]), "region": "Europe",
            "year": 2025, "quarter": 1, "supplier_price": 100,
        })
        assert r.status_code == 404

        # Cross-team template: tenant_b addressing tenant_a's private template
        # via tenant_b's own team_id (RLS-hidden -> 404 from _get_visible_template)
        r = client_as(tenant_b).get(_base_url(t), params={
            "team_id": str(tenant_a["team_id"]), "region": "Europe",
            "year": 2025, "quarter": 1, "supplier_price": 100,
        })
        assert r.status_code in (403, 404)
    finally:
        _cleanup(db, [t.id])


# ── Audit ────────────────────────────────────────────────────────────────────

def test_negotiation_position_is_audited(db, tenant_a, client_as):
    from app.models.audit_log import AuditLog

    t = _mk_template(db, "pos-audit", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Fixed", "component_type": "fixed", "weight_pct": 100},
        ]}).status_code == 200
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_price": 100, "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        r = c.get(_base_url(t), params={**q, "year": 2025, "quarter": 1, "supplier_price": 110})
        assert r.status_code == 200, r.text

        db.expire_all()
        evt = (
            db.query(AuditLog)
            .filter(AuditLog.team_id == tenant_a["team_id"],
                    AuditLog.event_type == "negotiation_position_generated",
                    AuditLog.entity_id == str(t.id))
            .first()
        )
        assert evt is not None
        assert evt.user_id == tenant_a["user_id"]
    finally:
        _cleanup(db, [t.id])
