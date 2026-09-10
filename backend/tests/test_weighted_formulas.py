"""Weighted formula components + per-(formula x region) coverage (Scrum 58).

Covers:
- RLS: a team's component lines are invisible to another team; platform lines
  visible to all.
- Component replace API: weights must sum to 100, type/target coherence.
- Coverage combos: upsert/delete + resolver fallback exact region → parent
  region → GLOBAL → Europe.
- Formula-as-input chaining: flattening with multiplicative weights, the
  depth cap, cycle blocking, and the platform-can't-chain-team scope rule.
- A template chained into another formula can't be deleted (409).
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import SessionLocal, bypass_rls_var, current_user_id_var
from app.models.formula_template import (
    FormulaRegionCoverage,
    FormulaTemplate,
    FormulaTemplateComponent,
)
from app.models.index_data import CommodityIndex, IndexValue


def _as_user(user_id):
    """Fresh RLS-scoped session acting as the given user (policies on)."""
    s = SessionLocal()
    bypass_rls_var.set(False)
    current_user_id_var.set(str(user_id))
    return s


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
    """Templates in parent-before-child order: a child referenced by a parent's
    input_template_id (NO ACTION FK) can only go once the parent is gone."""
    bypass_rls_var.set(True)
    for tid in template_ids:
        db.execute(text("DELETE FROM formula_templates WHERE id = :id"), {"id": str(tid)})
    for cid in commodity_ids:
        db.execute(text("DELETE FROM index_values WHERE commodity_id = :id"), {"id": cid})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :id"), {"id": cid})
    db.commit()


# ── RLS ───────────────────────────────────────────────────────────────────────

def test_components_rls_isolated(db, tenant_a, tenant_b):
    plat = _mk_template(db, f"plat-{uuid.uuid4().hex[:6]}", tenant_a["user_id"])
    team = _mk_template(db, "a-weighted", tenant_a["user_id"], team_id=tenant_a["team_id"])
    # Snapshot ids before switching RLS identity — the ORM instances are
    # expired post-commit and would refresh under tenant B's policies.
    plat_id, team_id = plat.id, team.id
    db.add_all([
        FormulaTemplateComponent(template_id=plat_id, name="plat-line",
                                 component_type="fixed", weight_pct=100),
        FormulaTemplateComponent(template_id=team_id, name="a-line",
                                 component_type="fixed", weight_pct=100),
    ])
    db.commit()

    s = _as_user(tenant_b["user_id"])
    try:
        names = {c.name for c in s.query(FormulaTemplateComponent).all()}
        assert "plat-line" in names     # platform line visible to all
        assert "a-line" not in names    # team A's line isolated from team B
    finally:
        s.close()
        _cleanup(db, [plat_id, team_id])


def test_coverage_rls_isolated(db, tenant_a, tenant_b):
    team = _mk_template(db, "a-covered", tenant_a["user_id"], team_id=tenant_a["team_id"])
    team_id = team.id
    db.add(FormulaRegionCoverage(template_id=team_id, region="Europe", base_price=1000))
    db.commit()

    s = _as_user(tenant_b["user_id"])
    try:
        assert s.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.template_id == team_id
        ).count() == 0
    finally:
        s.close()
        _cleanup(db, [team_id])


# ── Component replace API ─────────────────────────────────────────────────────

def test_replace_and_list_components(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    t = _mk_template(db, "weighted", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    try:
        r = c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Palm Oil", "component_type": "index", "commodity_id": idx.id,
             "weight_pct": 60, "is_proxy": True},
            {"name": "Other / fixed", "component_type": "fixed", "weight_pct": 40},
        ]})
        assert r.status_code == 200, r.text
        assert len(r.json()) == 2

        r = c.get(f"/api/formulas/{t.id}/components", params={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 200
        lines = r.json()
        assert [x["name"] for x in lines] == ["Palm Oil", "Other / fixed"]
        assert lines[0]["is_proxy"] is True and lines[0]["commodity_id"] == idx.id

        # Replace is a block operation — a second PUT swaps the set, not appends.
        r = c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Only line", "component_type": "fixed", "weight_pct": 100},
        ]})
        assert r.status_code == 200
        r = c.get(f"/api/formulas/{t.id}/components", params={"team_id": str(tenant_a["team_id"])})
        assert [x["name"] for x in r.json()] == ["Only line"]
    finally:
        _cleanup(db, [t.id], [idx.id])


def test_weights_must_sum_to_100(db, tenant_a, client_as):
    t = _mk_template(db, "badsum", tenant_a["user_id"], team_id=tenant_a["team_id"])
    try:
        r = client_as(tenant_a).put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "A", "component_type": "fixed", "weight_pct": 60},
            {"name": "B", "component_type": "fixed", "weight_pct": 30},
        ]})
        assert r.status_code == 422
        assert "sum to 100" in r.text
    finally:
        _cleanup(db, [t.id])


def test_component_type_coherence(db, tenant_a, client_as):
    t = _mk_template(db, "badtype", tenant_a["user_id"], team_id=tenant_a["team_id"])
    try:
        # index line without a commodity
        r = client_as(tenant_a).put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "A", "component_type": "index", "weight_pct": 100},
        ]})
        assert r.status_code == 422
        # fixed line carrying a commodity
        r = client_as(tenant_a).put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "A", "component_type": "fixed", "commodity_id": 1, "weight_pct": 100},
        ]})
        assert r.status_code == 422
    finally:
        _cleanup(db, [t.id])


# ── Coverage + resolver fallback ─────────────────────────────────────────────

def test_coverage_fallback_chain(db, tenant_a, client_as):
    t = _mk_template(db, "combo", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    team_q = {"team_id": str(tenant_a["team_id"])}
    try:
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe",
                     json={"base_price": 1000, "currency": "EUR", "margin_pct": 12,
                           "base_year": 2025, "base_quarter": 1}).status_code == 200
        assert c.put(f"/api/formulas/{t.id}/coverage/GLOBAL",
                     json={"base_price": 900, "currency": "USD"}).status_code == 200

        # Exact region wins
        r = c.get(f"/api/formulas/{t.id}/resolve", params={"region": "Europe", **team_q})
        assert r.status_code == 200 and r.json()["region_resolved"] == "Europe"
        assert r.json()["coverage"]["base_price"] == 1000

        # Uncovered top-level region → GLOBAL
        r = c.get(f"/api/formulas/{t.id}/resolve", params={"region": "NA", **team_q})
        assert r.json()["region_resolved"] == "GLOBAL"

        # Subregion → its parent (NWE prices as Europe, closer than GLOBAL)
        r = c.get(f"/api/formulas/{t.id}/resolve", params={"region": "NWE", **team_q})
        assert r.json()["region_resolved"] == "Europe"

        # Drop GLOBAL → Europe is the terminal fallback for everything
        assert c.delete(f"/api/formulas/{t.id}/coverage/GLOBAL").status_code == 200
        r = c.get(f"/api/formulas/{t.id}/resolve", params={"region": "NA", **team_q})
        assert r.json()["region_resolved"] == "Europe"

        # Unknown region on a coverage write fails instead of auto-registering
        assert c.put(f"/api/formulas/{t.id}/coverage/Nowheria",
                     json={"base_price": 1}).status_code == 400
    finally:
        _cleanup(db, [t.id])


def test_resolve_without_coverage_or_components(db, tenant_a, client_as):
    t = _mk_template(db, "empty", tenant_a["user_id"], team_id=tenant_a["team_id"])
    try:
        r = client_as(tenant_a).get(
            f"/api/formulas/{t.id}/resolve",
            params={"region": "Europe", "team_id": str(tenant_a["team_id"])},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["coverage"] is None and body["region_resolved"] is None
        assert body["lines"] == []
    finally:
        _cleanup(db, [t.id])


# ── Formula-as-input chaining ────────────────────────────────────────────────

def test_chained_formula_flattening(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    child = _mk_template(db, "child", tenant_a["user_id"], team_id=tenant_a["team_id"])
    parent = _mk_template(db, "parent", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
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

        r = c.get(f"/api/formulas/{parent.id}/resolve",
                  params={"region": "Europe", "team_id": str(tenant_a["team_id"])})
        assert r.status_code == 200, r.text
        lines = {x["name"]: x for x in r.json()["lines"]}
        # 60% of the child's 50/50 → 30/30; parent's own fixed rides at 40
        assert lines["Raw"]["effective_weight_pct"] == 30
        assert lines["Raw"]["depth"] == 1
        assert lines["Raw"]["via_template_name"] == "child"
        assert lines["Child fixed"]["effective_weight_pct"] == 30
        assert lines["Parent fixed"]["effective_weight_pct"] == 40
        assert lines["Parent fixed"]["depth"] == 0
        assert sum(x["effective_weight_pct"] for x in r.json()["lines"]) == 100
    finally:
        _cleanup(db, [parent.id, child.id], [idx.id])


def test_chain_depth_cap(db, tenant_a, client_as):
    uid, tid = tenant_a["user_id"], tenant_a["team_id"]
    d = _mk_template(db, "t-d", uid, team_id=tid)
    c3 = _mk_template(db, "t-c", uid, team_id=tid)
    b = _mk_template(db, "t-b", uid, team_id=tid)
    a = _mk_template(db, "t-a", uid, team_id=tid)
    e = _mk_template(db, "t-e", uid, team_id=tid)
    c = client_as(tenant_a)

    def _chain(parent, child):
        return c.put(f"/api/formulas/{parent.id}/components", json={"components": [
            {"name": child.name, "component_type": "formula",
             "input_template_id": str(child.id), "weight_pct": 100},
        ]})

    try:
        assert c.put(f"/api/formulas/{d.id}/components", json={"components": [
            {"name": "leaf", "component_type": "fixed", "weight_pct": 100},
        ]}).status_code == 200
        # A → B → C → D is 3 hops: at the cap, allowed
        assert _chain(c3, d).status_code == 200
        assert _chain(b, c3).status_code == 200
        assert _chain(a, b).status_code == 200
        r = c.get(f"/api/formulas/{a.id}/resolve",
                  params={"region": "Europe", "team_id": str(tid)})
        assert r.status_code == 200 and r.json()["lines"][0]["depth"] == 3

        # E → A would make it 4 hops: over the cap, rejected at write time
        r = _chain(e, a)
        assert r.status_code == 400
        assert "depth" in r.json()["detail"]
    finally:
        _cleanup(db, [e.id, a.id, b.id, c3.id, d.id])


def test_chain_cycle_blocked(db, tenant_a, client_as):
    uid, tid = tenant_a["user_id"], tenant_a["team_id"]
    a = _mk_template(db, "cyc-a", uid, team_id=tid)
    b = _mk_template(db, "cyc-b", uid, team_id=tid)
    c = client_as(tenant_a)
    try:
        assert c.put(f"/api/formulas/{a.id}/components", json={"components": [
            {"name": "b", "component_type": "formula",
             "input_template_id": str(b.id), "weight_pct": 100},
        ]}).status_code == 200

        # B → A closes the loop A → B → A
        r = c.put(f"/api/formulas/{b.id}/components", json={"components": [
            {"name": "a", "component_type": "formula",
             "input_template_id": str(a.id), "weight_pct": 100},
        ]})
        assert r.status_code == 400
        assert "circular" in r.json()["detail"].lower()

        # Direct self-reference blocked too
        r = c.put(f"/api/formulas/{a.id}/components", json={"components": [
            {"name": "self", "component_type": "formula",
             "input_template_id": str(a.id), "weight_pct": 100},
        ]})
        assert r.status_code == 400
    finally:
        _cleanup(db, [a.id, b.id])


def test_delete_template_used_as_input_conflict(db, tenant_a, client_as):
    uid, tid = tenant_a["user_id"], tenant_a["team_id"]
    child = _mk_template(db, "del-child", uid, team_id=tid)
    parent = _mk_template(db, "del-parent", uid, team_id=tid)
    c = client_as(tenant_a)
    try:
        assert c.put(f"/api/formulas/{parent.id}/components", json={"components": [
            {"name": "in", "component_type": "formula",
             "input_template_id": str(child.id), "weight_pct": 100},
        ]}).status_code == 200

        assert c.delete(f"/api/formulas/{child.id}").status_code == 409

        # Unwind the reference, then the delete goes through
        assert c.put(f"/api/formulas/{parent.id}/components",
                     json={"components": []}).status_code == 200
        assert c.delete(f"/api/formulas/{child.id}").status_code == 200
    finally:
        _cleanup(db, [parent.id])


# ── Weighted evaluation ──────────────────────────────────────────────────────

def test_evaluate_weighted_should_cost(db, tenant_a, client_as):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    db.add_all([
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=1, value=100),
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=3, value=110),
    ])
    db.commit()
    t = _mk_template(db, "eval-me", tenant_a["user_id"], team_id=tenant_a["team_id"])
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

        # Index +10% on a 60% line → level 106, should-cost 1060.
        r = c.get(f"/api/formulas/{t.id}/evaluate", params={**q, "year": 2025, "quarter": 3})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["evaluable"] is True
        assert body["index_level_pct"] == 106.0
        assert body["should_cost"] == 1060.0
        by_name = {l["name"]: l for l in body["lines"]}
        assert by_name["Feedstock"]["ratio"] == 1.1
        assert by_name["Feedstock"]["contribution_abs"] == 660.0
        assert by_name["Fixed"]["contribution_abs"] == 400.0
        assert sum(l["contribution_abs"] for l in body["lines"]) == body["should_cost"]

        # At the base period the level is exactly 100 and should-cost = anchor.
        r = c.get(f"/api/formulas/{t.id}/evaluate", params={**q, "year": 2025, "quarter": 1})
        assert r.json()["index_level_pct"] == 100.0
        assert r.json()["should_cost"] == 1000.0
    finally:
        _cleanup(db, [t.id], [idx.id])


def test_evaluate_rebases_catalog_style_sums(db, tenant_a, client_as):
    """Catalog recipes legitimately sum to more than 100 (margin lines ride
    inside); rebasing must still evaluate to exactly P0 at the base period."""
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    db.add_all([
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=1, value=100),
        IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=3, value=120),
    ])
    db.commit()
    t = _mk_template(db, "rebase-me", tenant_a["user_id"], team_id=tenant_a["team_id"])
    # Seeded-style per-region lines summing to 110 (written like the seeder,
    # not through the API which enforces Σ=100 on the template-level set).
    db.add_all([
        FormulaTemplateComponent(template_id=t.id, region="Europe", name="Feedstock",
                                 component_type="index", commodity_id=idx.id,
                                 weight_pct=60, sort_order=0),
        FormulaTemplateComponent(template_id=t.id, region="Europe", name="Margin",
                                 component_type="fixed", weight_pct=50, sort_order=1),
    ])
    db.commit()
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe"}
    try:
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_price": 500, "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        r = c.get(f"/api/formulas/{t.id}/evaluate", params={**q, "year": 2025, "quarter": 1})
        assert r.json()["index_level_pct"] == 100.0
        assert r.json()["should_cost"] == 500.0

        # weighted = 60×1.2 + 50 = 122 over base_sum 110 → 500 × 122/110
        r = c.get(f"/api/formulas/{t.id}/evaluate", params={**q, "year": 2025, "quarter": 3})
        assert r.json()["index_level_pct"] == round(100 * 122 / 110, 4)
        assert r.json()["should_cost"] == round(500 * 122 / 110, 4)
    finally:
        _cleanup(db, [t.id], [idx.id])


def test_evaluate_states_and_data_gaps(db, tenant_a, client_as):
    ghost = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")  # no values at all
    t = _mk_template(db, "gaps", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe", "year": 2025, "quarter": 1}
    try:
        # No lines yet
        r = c.get(f"/api/formulas/{t.id}/evaluate", params=q)
        assert r.json()["evaluable"] is False and "no weighted lines" in r.json()["reason"]

        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Ghost", "component_type": "index", "commodity_id": ghost.id, "weight_pct": 100},
        ]}).status_code == 200

        # Lines but no coverage
        r = c.get(f"/api/formulas/{t.id}/evaluate", params=q)
        assert r.json()["evaluable"] is False and "coverage" in r.json()["reason"]

        # Coverage without a base period anchor
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe",
                     json={"base_price": 100}).status_code == 200
        r = c.get(f"/api/formulas/{t.id}/evaluate", params=q)
        assert r.json()["evaluable"] is False and "base period" in r.json()["reason"]

        # Anchored, but the index has no data: line rides flat + explicit gap
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_price": 100, "base_year": 2025, "base_quarter": 1,
        }).status_code == 200
        r = c.get(f"/api/formulas/{t.id}/evaluate", params=q)
        body = r.json()
        assert body["evaluable"] is True and body["should_cost"] == 100.0
        assert len(body["data_gaps"]) == 1
        assert body["lines"][0]["has_data"] is False and body["lines"][0]["ratio"] == 1.0
    finally:
        _cleanup(db, [t.id], [ghost.id])


def test_mark_coverage_reviewed(db, tenant_a, tenant_b, client_as):
    t = _mk_template(db, "review-me", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    try:
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe",
                     json={"margin_pct": 9}).status_code == 200
        # Simulate a seeded CONF-LOW placeholder combo
        db.execute(text("""
            UPDATE formula_region_coverage SET needs_review = true, data_confidence = 'CONF-LOW'
            WHERE template_id = :t AND region = 'Europe'"""), {"t": str(t.id)})
        db.commit()

        r = c.post(f"/api/formulas/{t.id}/coverage/Europe/review")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["needs_review"] is False
        # SCRUM-78 moved the reviewer onto a users FK: `reviewed_by` held the
        # actor's email, so the record decayed when they changed it. The legacy
        # column is no longer written; the display identity is resolved from the
        # FK on read.
        assert body["reviewed_by_id"] and body["reviewed_at"]
        assert body["reviewed_by_name"]

        # Another team can't review — 404 (RLS-hidden) or 403 (permission gate)
        r = client_as(tenant_b).post(f"/api/formulas/{t.id}/coverage/Europe/review")
        assert r.status_code in (403, 404)
        # Unknown region → 404
        assert c.post(f"/api/formulas/{t.id}/coverage/NA/review").status_code == 404
    finally:
        _cleanup(db, [t.id])


def test_coverage_price_upload(db, tenant_a, user_factory, client_as):
    sa = user_factory(is_super_admin=True)
    code = f"ZZZ-{uuid.uuid4().hex[:6].upper()}"
    t = FormulaTemplate(team_id=None, created_by=sa["user_id"], name="upload-target",
                        code=code, expression=None)
    db.add(t)
    db.flush()
    db.add(FormulaRegionCoverage(template_id=t.id, region="Europe"))
    tid = t.id
    db.commit()

    csv = (
        "formula,region,base_price,currency,base_period,margin_pct\n"
        f"{code},Europe,1250,EUR,Q1-2025,9\n"
        "GHOST-1,Europe,100,,,\n"          # unknown formula code
        f"{code},Mars,100,,,\n"            # unknown region
        f"{code},NA,100,,,\n"              # no combo for that region
        f"{code},Europe,not-a-price,,,\n"  # parse error
    )
    files = {"file": ("prices.csv", csv.encode(), "text/csv")}
    c = client_as(sa)
    try:
        # Dry run: 1 valid update, 4 row errors, nothing written
        r = c.post("/api/formulas/coverage/upload?dry_run=true", files=files)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rows_processed"] == 1 and len(body["errors"]) == 4
        assert db.execute(text(
            "SELECT base_price FROM formula_region_coverage WHERE template_id = :t"),
            {"t": str(tid)}).scalar() is None

        # Real run: the combo gets its anchor; review/recipe fields untouched
        r = c.post("/api/formulas/coverage/upload", files=files)
        assert r.status_code == 200 and r.json()["rows_processed"] == 1
        row = db.execute(text("""
            SELECT base_price, currency, base_year, base_quarter, margin_pct
            FROM formula_region_coverage WHERE template_id = :t"""), {"t": str(tid)}).fetchone()
        assert [float(row[0]), row[1], row[2], row[3], float(row[4])] == [1250.0, "EUR", 2025, 1, 9.0]

        # Without the platform permission → 403
        r = client_as(tenant_a).post("/api/formulas/coverage/upload", files=files)
        assert r.status_code == 403
    finally:
        _cleanup(db, [tid])


def test_platform_formula_cannot_chain_team_formula(db, tenant_a, user_factory, client_as):
    sa = user_factory(is_super_admin=True)
    plat = _mk_template(db, f"plat-{uuid.uuid4().hex[:6]}", sa["user_id"])
    team = _mk_template(db, "team-input", tenant_a["user_id"], team_id=tenant_a["team_id"])
    try:
        r = client_as(sa).put(f"/api/formulas/{plat.id}/components", json={"components": [
            {"name": "team input", "component_type": "formula",
             "input_template_id": str(team.id), "weight_pct": 100},
        ]})
        assert r.status_code == 400
        assert "platform" in r.json()["detail"].lower()
    finally:
        _cleanup(db, [plat.id, team.id])
