"""AI cost modeler (Scrum 32).

The LLM is mocked at the router boundary throughout — what is worth testing is
everything around it, because everything around it is what stops a plausible
paragraph becoming a priced cost model.

Three invariants carry most of the weight:
- nothing real is written until an explicit promote;
- a suggested index that matches nothing we track is FLAGGED, never dropped;
- an unavailable model says so rather than returning an empty draft that reads
  like a considered answer.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.ai_cost_draft import AiCostDraft
from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.index_data import CommodityIndex
from app.models.product import Product
from app.services.ai_cost_modeler import (
    ModelerUnparseable, parse_response, promotion_blockers, resolve_indexes,
)


# ── parsing (pure) ────────────────────────────────────────────────────────

GOOD = {
    "rationale": "Made by ethoxylating lauryl alcohol.",
    "lines": [
        {"label": "Lauryl alcohol", "weight_pct": 34, "type": "index",
         "index": "Fatty Alcohol", "confidence": "high", "why": "primary feedstock"},
        {"label": "Ethylene oxide", "weight_pct": 28, "type": "index", "index": "EO"},
        {"label": "Conversion", "weight_pct": 28, "type": "fixed", "index": None},
        {"label": "Margin", "weight_pct": 10, "type": "fixed", "index": None},
    ],
}


def test_a_clean_reply_parses():
    out = parse_response(json.dumps(GOOD))
    assert len(out["lines"]) == 4
    assert out["rationale"].startswith("Made by")
    assert out["lines"][0]["confidence"] == "high"
    assert out["lines"][2]["component_type"] == "fixed"


def test_a_fenced_or_chatty_reply_still_parses():
    """Models wrap JSON in prose and code fences constantly. Refusing those
    would make the feature fail most of the time for no good reason."""
    fenced = "Sure! Here you go:\n```json\n" + json.dumps(GOOD) + "\n```\nHope that helps."
    assert len(parse_response(fenced)["lines"]) == 4

    chatty = "Here is the structure: " + json.dumps(GOOD)
    assert len(parse_response(chatty)["lines"]) == 4


def test_nonsense_is_refused_with_the_raw_reply_kept():
    """A draft whose parse went wrong is only debuggable against the words it
    came from."""
    with pytest.raises(ModelerUnparseable) as exc:
        parse_response("I'm afraid I can't help with that.")
    assert "I'm afraid" in exc.value.raw


def test_an_almost_right_shape_is_refused_not_half_accepted():
    with pytest.raises(ModelerUnparseable):
        parse_response(json.dumps({"rationale": "x", "components": []}))
    # One usable line is not a cost structure.
    with pytest.raises(ModelerUnparseable):
        parse_response(json.dumps({"lines": [{"label": "Only", "weight_pct": 100}]}))


def test_absurd_weights_are_dropped_rather_than_carried():
    reply = {"lines": [
        {"label": "Good", "weight_pct": 50, "index": "X"},
        {"label": "Negative", "weight_pct": -10, "index": "Y"},
        {"label": "Over", "weight_pct": 400, "index": "Z"},
        {"label": "Fine", "weight_pct": 50, "type": "fixed"},
    ]}
    labels = [l["label"] for l in parse_response(json.dumps(reply))["lines"]]
    assert labels == ["Good", "Fine"]


def test_a_line_with_no_index_becomes_fixed_not_a_broken_index_line():
    reply = {"lines": [{"label": "Conversion", "weight_pct": 50},
                       {"label": "Other", "weight_pct": 50}]}
    out = parse_response(json.dumps(reply))
    assert all(l["component_type"] == "fixed" for l in out["lines"])


# ── index resolution ──────────────────────────────────────────────────────

def test_a_suggested_index_that_matches_nothing_is_flagged_not_dropped(db):
    """The expected failure here is a language model producing a plausible
    feed name. Dropping the line would leave a recipe that no longer sums to
    100 with nothing to explain why."""
    suffix = uuid.uuid4().hex[:8]
    real = CommodityIndex(name=f"Caustic Soda {suffix}", unit="t", scrape_enabled=False)
    bypass_rls_var.set(True)
    db.add(real)
    db.flush()
    try:
        lines = [
            {"label": "A", "weight_pct": 50, "component_type": "index",
             "suggested_index": f"Caustic Soda {suffix}"},
            {"label": "B", "weight_pct": 50, "component_type": "index",
             "suggested_index": "Unobtanium Futures"},
        ]
        out = resolve_indexes(db, lines)
        assert out[0]["index_resolved"] is True and out[0]["commodity_id"] == real.id
        assert out[1]["index_resolved"] is False and out[1]["commodity_id"] is None
        assert len(out) == 2, "the unresolved line is still there"
    finally:
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": real.id})
        db.commit()


def test_an_ambiguous_name_is_left_for_a_human(db):
    """Two plausible candidates is a question. Binding to whichever sorted
    first would point a cost line at the wrong series."""
    suffix = uuid.uuid4().hex[:8]
    a = CommodityIndex(name=f"Naphtha {suffix} EU", unit="t", scrape_enabled=False)
    b = CommodityIndex(name=f"Naphtha {suffix} NA", unit="t", scrape_enabled=False)
    bypass_rls_var.set(True)
    db.add_all([a, b])
    db.flush()
    try:
        out = resolve_indexes(db, [{"label": "N", "weight_pct": 100, "component_type": "index",
                                    "suggested_index": f"Naphtha {suffix}"}])
        assert out[0]["index_resolved"] is False
    finally:
        db.execute(text("DELETE FROM commodity_indexes WHERE id IN (:a, :b)"),
                   {"a": a.id, "b": b.id})
        db.commit()


# ── promotion gates ───────────────────────────────────────────────────────

class _Line:
    def __init__(self, label, weight_pct, component_type="fixed", index_resolved=True):
        self.label, self.weight_pct = label, weight_pct
        self.component_type, self.index_resolved = component_type, index_resolved


def test_a_recipe_that_does_not_close_at_100_cannot_be_promoted():
    """It would price wrong without looking wrong."""
    blockers = promotion_blockers([_Line("A", 50), _Line("B", 47)])
    assert any("100%" in b for b in blockers)


def test_an_unresolved_index_line_blocks_promotion_and_is_named():
    blockers = promotion_blockers([
        _Line("A", 60, "index", index_resolved=True),
        _Line("Mystery", 40, "index", index_resolved=False),
    ])
    assert any("Mystery" in b for b in blockers)


def test_a_clean_recipe_has_no_blockers():
    assert promotion_blockers([
        _Line("A", 60, "index", index_resolved=True), _Line("Margin", 40)]) == []


def test_rounding_is_tolerated():
    assert promotion_blockers([_Line("A", 33.33), _Line("B", 33.33), _Line("C", 33.34)]) == []


# ── API ───────────────────────────────────────────────────────────────────

def _mock_llm(monkeypatch, reply):
    async def _gen(prompt, system=None):
        return reply
    monkeypatch.setattr("app.routers.ai_cost_modeler.ollama_generate", _gen)


def _cleanup(db, team_id, extra_cost_models=()):
    bypass_rls_var.set(True)
    db.execute(text("DELETE FROM ai_cost_drafts WHERE team_id = :t"), {"t": str(team_id)})
    for cid in extra_cost_models:
        db.execute(text("DELETE FROM formula_components WHERE formula_version_id IN "
                        "(SELECT id FROM formula_versions WHERE cost_model_id = :c)"), {"c": str(cid)})
        db.execute(text("DELETE FROM cost_models WHERE id = :c"), {"c": str(cid)})
    db.commit()


def test_an_unavailable_model_says_so_rather_than_returning_an_empty_draft(
        db, tenant_a, client_as, monkeypatch):
    """In production llm_enabled is False and a cache miss returns None. An
    empty draft would read as a considered answer that found nothing."""
    async def _none(prompt, system=None):
        return None
    monkeypatch.setattr("app.routers.ai_cost_modeler.ollama_generate", _none)

    r = client_as(tenant_a).post(
        f"/api/ai-cost-modeler/drafts?team_id={tenant_a['team_id']}",
        json={"product_name": "Something"})
    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"].lower()
    bypass_rls_var.set(True)
    assert db.query(AiCostDraft).filter(AiCostDraft.team_id == tenant_a["team_id"]).count() == 0


def test_an_unparseable_reply_is_a_502_not_a_draft(db, tenant_a, client_as, monkeypatch):
    _mock_llm(monkeypatch, "I don't know how that product is made.")
    r = client_as(tenant_a).post(
        f"/api/ai-cost-modeler/drafts?team_id={tenant_a['team_id']}",
        json={"product_name": "Something"})
    assert r.status_code == 502
    bypass_rls_var.set(True)
    assert db.query(AiCostDraft).filter(AiCostDraft.team_id == tenant_a["team_id"]).count() == 0


def test_a_draft_writes_nothing_real_and_reports_its_blockers(
        db, tenant_a, client_as, monkeypatch):
    _mock_llm(monkeypatch, json.dumps(GOOD))
    before = db.query(CostModel).filter(CostModel.team_id == tenant_a["team_id"]).count()
    try:
        r = client_as(tenant_a).post(
            f"/api/ai-cost-modeler/drafts?team_id={tenant_a['team_id']}",
            json={"product_name": "SLES 70%", "sector": "Surfactants", "rough_price": 1240})
        assert r.status_code == 201, r.text
        body = r.json()
        assert len(body["lines"]) == 4
        assert body["weight_total"] == 100.0
        # The two index lines name feeds that do not exist in this database.
        assert body["blockers"], "unresolved index lines must block promotion"
        assert any("do not match" in b for b in body["blockers"])
        # And nothing real was created.
        assert db.query(CostModel).filter(
            CostModel.team_id == tenant_a["team_id"]).count() == before
    finally:
        _cleanup(db, tenant_a["team_id"])


def test_refining_then_promoting_creates_a_formula_tagged_as_an_estimate(
        db, tenant_a, client_as, monkeypatch):
    """The whole intended path: suggest, correct, promote."""
    suffix = uuid.uuid4().hex[:8]
    idx = CommodityIndex(name=f"Feedstock {suffix}", unit="t", scrape_enabled=False)
    bypass_rls_var.set(True)
    db.add(idx)
    db.flush()
    product = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"],
                      created_by=tenant_a["user_id"], name=f"Prod {suffix}", unit="t")
    db.add(product)
    db.commit()

    _mock_llm(monkeypatch, json.dumps(GOOD))
    c = client_as(tenant_a)
    created_cm = None
    try:
        draft = c.post(f"/api/ai-cost-modeler/drafts?team_id={tenant_a['team_id']}",
                       json={"product_name": f"Prod {suffix}"}).json()
        assert draft["blockers"]

        # Correct it: bind the index lines and make it close at 100.
        fixed = c.put(f"/api/ai-cost-modeler/drafts/{draft['id']}/lines", json={"lines": [
            {"label": "Feedstock", "weight_pct": 60, "component_type": "index",
             "commodity_id": idx.id},
            {"label": "Conversion", "weight_pct": 30, "component_type": "fixed"},
            {"label": "Margin", "weight_pct": 10, "component_type": "fixed"},
        ]}).json()
        assert fixed["blockers"] == [], fixed["blockers"]

        r = c.post(f"/api/ai-cost-modeler/drafts/{draft['id']}/promote", json={
            "product_id": str(product.id), "base_price": 1240,
            "base_year": 2026, "base_quarter": 1,
        })
        assert r.status_code == 201, r.text
        result = r.json()
        created_cm = uuid.UUID(result["cost_model_id"])
        # The caveat travels with it.
        assert result["provenance"] == "ai_draft"

        bypass_rls_var.set(True)
        db.expire_all()
        fv = db.query(FormulaVersion).filter(
            FormulaVersion.cost_model_id == created_cm).first()
        assert fv is not None and "estimate" in (fv.notes or "").lower()
        comps = db.query(FormulaComponent).filter(
            FormulaComponent.formula_version_id == fv.id).all()
        assert len(comps) == 3
        # Weights normalised to fractions of one, the convention every other
        # writer of this table uses.
        assert sum(float(x.weight) for x in comps) == pytest.approx(1.0)
        # Margin is a line in the recipe, so no separate margin on top.
        assert float(fv.margin_value) == 0

        # Promoting twice is refused rather than making a second formula.
        again = c.post(f"/api/ai-cost-modeler/drafts/{draft['id']}/promote", json={
            "product_id": str(product.id), "base_price": 1240,
            "base_year": 2026, "base_quarter": 1})
        assert again.status_code == 400
    finally:
        _cleanup(db, tenant_a["team_id"], [created_cm] if created_cm else [])
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM products WHERE id = :p"), {"p": str(product.id)})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": idx.id})
        db.commit()


def test_promotion_is_refused_while_the_recipe_is_broken(db, tenant_a, client_as, monkeypatch):
    _mock_llm(monkeypatch, json.dumps(GOOD))
    c = client_as(tenant_a)
    try:
        draft = c.post(f"/api/ai-cost-modeler/drafts?team_id={tenant_a['team_id']}",
                       json={"product_name": "Broken"}).json()
        r = c.post(f"/api/ai-cost-modeler/drafts/{draft['id']}/promote", json={
            "product_id": str(uuid.uuid4()), "base_price": 100,
            "base_year": 2026, "base_quarter": 1})
        assert r.status_code == 400
        assert "do not match" in r.json()["detail"]
    finally:
        _cleanup(db, tenant_a["team_id"])


def test_another_team_cannot_read_or_edit_a_draft(db, tenant_a, tenant_b, client_as, monkeypatch):
    _mock_llm(monkeypatch, json.dumps(GOOD))
    try:
        draft = client_as(tenant_a).post(
            f"/api/ai-cost-modeler/drafts?team_id={tenant_a['team_id']}",
            json={"product_name": "Theirs"}).json()
        other = client_as(tenant_b)
        assert other.get(f"/api/ai-cost-modeler/drafts/{draft['id']}").status_code in (403, 404)
        assert other.delete(f"/api/ai-cost-modeler/drafts/{draft['id']}").status_code in (403, 404)
        # And cannot spend the model's time on someone else's team.
        assert other.post(
            f"/api/ai-cost-modeler/drafts?team_id={tenant_a['team_id']}",
            json={"product_name": "x"}).status_code == 403
    finally:
        _cleanup(db, tenant_a["team_id"])


def test_unauthenticated_is_401(client):
    assert client.get(f"/api/ai-cost-modeler/drafts?team_id={uuid.uuid4()}").status_code == 401
