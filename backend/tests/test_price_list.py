"""Supplier price-list import (Scrum 30).

The parse itself is Scrum 31b's and is tested there; what is new here is
everything between a parsed row and an ActualPrice — matching a row to one of
this team's cost models, deriving the period, and committing.

Matching and period derivation are tested as pure functions. API-flow tests
mock `extract_quote` at the router boundary, the same way the quote tests do,
so nothing here depends on PDF rendering.

One per acceptance criterion, plus the failure modes that matter:
- AC1: a PDF yields product/price/date/currency with confidence.
- AC2: nothing reaches actual_prices until an explicit commit.
- AC3: tabular and free-text both work; per-field confidence is carried.
- AC4: an unreadable document fails cleanly so the caller can fall back to
  manual entry, rather than 500ing.
Plus: exact / fuzzy / ambiguous / unmatched stay four distinct states, a
cross-team cost model cannot be written through, and one bad row does not
stop the good ones beside it.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.cost_model import CostModel
from app.models.price_data import ActualPrice
from app.models.price_list import (
    MATCH_AMBIGUOUS, MATCH_EXACT, MATCH_FUZZY, MATCH_UNMATCHED,
)
from app.models.product import Product
from app.models.supplier import Supplier
from app.services import price_list as pl
from app.services import quote_extraction as qe


# ── helpers ───────────────────────────────────────────────────────────────

def _field(value, conf=0.9, snippet="x"):
    return {"value": value, "confidence": conf, "locator": {"page": 1, "snippet": snippet}}


def _mk_product(db, team_id, user_id, name, code=None):
    p = Product(id=uuid.uuid4(), team_id=team_id, created_by=user_id, name=name, formula=code, unit="t")
    db.add(p)
    db.flush()
    return p


def _mk_supplier(db, team_id, name):
    s = Supplier(team_id=team_id, name=name)
    db.add(s)
    db.flush()
    return s


def _mk_cost_model(db, team_id, user_id, product, supplier=None):
    cm = CostModel(
        id=uuid.uuid4(), team_id=team_id, product_id=product.id, created_by=user_id,
        supplier_id=supplier.id if supplier else None, region="Europe", currency="EUR",
    )
    db.add(cm)
    db.flush()
    return cm


def _mock_extract(monkeypatch, lines):
    monkeypatch.setattr(
        "app.routers.price_lists.extract_quote",
        lambda content, filename: {"extracted_text": "mock text", "lines": lines},
    )


def _upload(c, team_id, supplier_id=None):
    data = {"supplier_id": str(supplier_id)} if supplier_id is not None else None
    return c.post(
        f"/api/price-lists/extract?team_id={team_id}",
        files={"file": ("list.pdf", b"%PDF-fake", "application/pdf")},
        data=data,
    )


def _cleanup(db, run_ids=(), cost_model_ids=(), product_ids=(), supplier_ids=()):
    bypass_rls_var.set(True)
    for rid in run_ids:
        db.execute(text("DELETE FROM price_list_runs WHERE id = :i"), {"i": str(rid)})
    for cid in cost_model_ids:
        db.execute(text("DELETE FROM actual_prices WHERE cost_model_id = :i"), {"i": str(cid)})
        db.execute(text("DELETE FROM cost_models WHERE id = :i"), {"i": str(cid)})
    for pid in product_ids:
        db.execute(text("DELETE FROM products WHERE id = :i"), {"i": str(pid)})
    for sid in supplier_ids:
        db.execute(text("DELETE FROM suppliers WHERE id = :i"), {"i": sid})
    db.commit()


# ── matching (pure) ───────────────────────────────────────────────────────

class _FakeProduct:
    def __init__(self, name, formula=None):
        self.name, self.formula = name, formula


class _FakeSupplier:
    def __init__(self, name):
        self.name = name


class _FakeCM:
    def __init__(self, name, formula=None, supplier=None):
        self.id = uuid.uuid4()
        self.product = _FakeProduct(name, formula)
        self.supplier = _FakeSupplier(supplier) if supplier else None
        self.region = "Europe"


def test_exact_match_on_name_ignores_punctuation_and_case():
    cands = [_FakeCM("Acrylic Emulsion AE-40"), _FakeCM("Styrene Acrylic SA-12")]
    mid, conf, ranked = pl.match_row("acrylic emulsion ae 40", cands)
    assert conf == MATCH_EXACT and mid == cands[0].id
    # Ranked alternatives come back even on a hit, so a reviewer who disagrees
    # is not sent back to the search box.
    assert len(ranked) == 2 and ranked[0]["score"] == 1.0


def test_product_code_identifies_a_product():
    """A price list usually leads with the code, and character similarity
    alone rates it far below the threshold against the full product name —
    "AE-40" vs "Acrylic Emulsion" is not a near-miss, it is an identification.
    """
    cands = [_FakeCM("Acrylic Emulsion", "AE-40"), _FakeCM("Vinyl Acetate", "VAE-8")]
    assert pl._score("AE-40", "Acrylic Emulsion", None) < pl.FUZZY_MIN_SCORE

    # The bare code equals the product's own code: exact, not a guess.
    mid, conf, _ = pl.match_row("AE-40", cands)
    assert conf == MATCH_EXACT and mid == cands[0].id

    # The code carried inside a longer description is strong evidence but not
    # an equality, so it lands as fuzzy — a human still confirms it.
    mid, conf, _ = pl.match_row("AE-40 emulsion, 50% solids, drummed", cands)
    assert conf == MATCH_FUZZY and mid == cands[0].id


def test_two_equally_named_products_are_ambiguous_not_a_coin_flip():
    cands = [_FakeCM("Caustic Soda"), _FakeCM("Caustic Soda")]
    mid, conf, ranked = pl.match_row("Caustic Soda", cands)
    assert conf == MATCH_AMBIGUOUS and mid is None
    assert len(ranked) == 2


def test_near_tie_on_a_fuzzy_score_is_ambiguous():
    # Two plausible near-misses must not resolve to whichever sorted first.
    cands = [_FakeCM("Polymer Blend AX"), _FakeCM("Polymer Blend AY")]
    mid, conf, _ = pl.match_row("Polymer Blend A", cands)
    assert conf == MATCH_AMBIGUOUS and mid is None


def test_nothing_similar_is_unmatched_with_candidates_offered():
    cands = [_FakeCM("Caustic Soda"), _FakeCM("Chlorine")]
    mid, conf, ranked = pl.match_row("Titanium Dioxide Rutile", cands)
    assert conf == MATCH_UNMATCHED and mid is None
    assert ranked, "an unmatched row still offers candidates to choose from"


def test_no_extracted_name_is_unmatched():
    assert pl.match_row(None, [_FakeCM("Caustic Soda")])[1] == MATCH_UNMATCHED


# ── period derivation (pure) ──────────────────────────────────────────────

def test_period_prefers_valid_from_over_valid_until():
    # A list running Jan-Mar is a Q1 price. Reading valid_until first would
    # file it under the quarter it stops being true.
    fields = {
        "valid_from": _field("2026-01-05"),
        "valid_until": _field("2026-03-31"),
        "quote_date": _field("2025-12-20"),
    }
    assert pl.derive_period(fields) == (2026, 1)


def test_period_falls_back_to_quote_date_then_valid_until():
    assert pl.derive_period({"quote_date": _field("2026-08-02")}) == (2026, 3)
    assert pl.derive_period({"valid_until": _field("2026-11-30")}) == (2026, 4)


def test_no_date_means_no_period_never_today():
    assert pl.derive_period({"price": _field(100.0)}) == (None, None)


def test_unparseable_date_is_skipped_not_guessed():
    assert pl.derive_period({"quote_date": _field("sometime in spring")}) == (None, None)


# ── extraction boundary ───────────────────────────────────────────────────

def test_unreadable_document_fails_cleanly(db, tenant_a, client_as):
    """AC4 — the caller must be able to fall back to manual entry."""
    c = client_as(tenant_a)
    r = c.post(
        f"/api/price-lists/extract?team_id={tenant_a['team_id']}",
        files={"file": ("broken.pdf", b"not a pdf at all", "application/pdf")},
    )
    assert r.status_code == 400, r.text
    assert "broken.pdf" in r.json()["detail"]


def test_a_real_table_yields_per_field_confidence_and_locators():
    """AC1/AC3 — reusing the quote extractor unchanged."""
    pages = [{"page": 1, "text": "", "tables": [[
        ["Product", "Price", "Currency", "Unit", "Valid"],
        ["AE-40", "1908.00", "EUR", "MT", "2026-07-01"],
        ["SA-12", "1644.50", "EUR", "MT", "2026-07-01"],
    ]]}]
    lines = qe._lines_from_tables(pages)
    assert len(lines) == 2
    for line in lines:
        assert line["price"]["confidence"] > 0
        assert "locator" in line["price"]
    assert pl.derive_period(lines[0]) == (2026, 3)


# ── API flow ──────────────────────────────────────────────────────────────

def test_extract_persists_a_draft_and_writes_no_prices(db, tenant_a, client_as, monkeypatch):
    """AC2 — a parse is a proposal. Nothing reaches actual_prices."""
    prod = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "Acrylic Emulsion AE-40")
    cm = _mk_cost_model(db, tenant_a["team_id"], tenant_a["user_id"], prod)
    db.commit()

    _mock_extract(monkeypatch, [
        {"product_reference": _field("Acrylic Emulsion AE-40"), "price": _field(1908.0),
         "currency": _field("EUR"), "unit": _field("t"), "valid_from": _field("2026-07-01")},
        {"product_reference": _field("Something Unknown"), "price": _field(10.0)},
    ])
    c = client_as(tenant_a)
    run_id = None
    try:
        before = db.query(ActualPrice).filter(ActualPrice.cost_model_id == cm.id).count()
        r = _upload(c, tenant_a["team_id"])
        assert r.status_code == 201, r.text
        body = r.json()
        run_id = body["id"]

        assert len(body["rows"]) == 2
        assert all(row["status"] == "pending" for row in body["rows"])
        assert body["rows"][0]["match_confidence"] == MATCH_EXACT
        assert body["rows"][0]["matched_cost_model_id"] == str(cm.id)
        assert body["rows"][0]["period_year"] == 2026 and body["rows"][0]["period_quarter"] == 3
        assert body["rows"][1]["match_confidence"] == MATCH_UNMATCHED

        assert db.query(ActualPrice).filter(ActualPrice.cost_model_id == cm.id).count() == before
    finally:
        _cleanup(db, [run_id] if run_id else [], [cm.id], [prod.id])


def test_declaring_the_supplier_turns_an_ambiguity_into_a_match(db, tenant_a, client_as, monkeypatch):
    """Two suppliers quoting the same product is the normal case, and the
    document usually cannot tell them apart — the upload can."""
    team, user = tenant_a["team_id"], tenant_a["user_id"]
    sup_a = _mk_supplier(db, team, "Synthomer PL")
    sup_b = _mk_supplier(db, team, "BASF PL")
    p1 = _mk_product(db, team, user, "Caustic Soda Liquid")
    p2 = _mk_product(db, team, user, "Caustic Soda Liquid")
    cm1 = _mk_cost_model(db, team, user, p1, sup_a)
    cm2 = _mk_cost_model(db, team, user, p2, sup_b)
    db.commit()

    lines = [{"product_reference": _field("Caustic Soda Liquid"), "price": _field(500.0),
              "quote_date": _field("2026-02-10")}]
    _mock_extract(monkeypatch, lines)
    c = client_as(tenant_a)
    r1 = r2 = None
    try:
        r1 = _upload(c, team).json()
        assert r1["rows"][0]["match_confidence"] == MATCH_AMBIGUOUS
        assert r1["rows"][0]["matched_cost_model_id"] is None

        r2 = _upload(c, team, supplier_id=sup_b.id).json()
        assert r2["rows"][0]["match_confidence"] == MATCH_EXACT
        assert r2["rows"][0]["matched_cost_model_id"] == str(cm2.id)
    finally:
        _cleanup(db, [x["id"] for x in (r1, r2) if x], [cm1.id, cm2.id], [p1.id, p2.id],
                 [sup_a.id, sup_b.id])


def test_commit_writes_prices_upserts_and_reports_each_row(db, tenant_a, client_as, monkeypatch):
    team, user = tenant_a["team_id"], tenant_a["user_id"]
    prod = _mk_product(db, team, user, "Acrylic Emulsion AE-40")
    cm = _mk_cost_model(db, team, user, prod)
    db.commit()

    _mock_extract(monkeypatch, [
        # ready
        {"product_reference": _field("Acrylic Emulsion AE-40"), "price": _field(1908.0),
         "valid_from": _field("2026-07-01")},
        # matched, but the document stated no date
        {"product_reference": _field("Acrylic Emulsion AE-40"), "price": _field(1900.0)},
        # no match at all
        {"product_reference": _field("Totally Different Thing"), "price": _field(5.0)},
    ])
    c = client_as(tenant_a)
    run_id = None
    try:
        run = _upload(c, team).json()
        run_id = run["id"]
        rows = run["rows"]

        r = c.post(f"/api/price-lists/runs/{run_id}/commit",
                   json={"rows": [{"row_id": rows[0]["id"]},
                                  {"row_id": rows[1]["id"]},
                                  {"row_id": rows[2]["id"]}]})
        assert r.status_code == 200, r.text
        body = r.json()
        # One bad row must not stop the good one beside it.
        assert body["committed"] == 1 and body["failed"] == 2
        by_row = {x["row_id"]: x for x in body["results"]}
        assert by_row[rows[0]["id"]]["committed"] is True
        assert "period" in by_row[rows[1]["id"]]["error"].lower()
        assert "cost model" in by_row[rows[2]["id"]]["error"].lower()

        ap = db.query(ActualPrice).filter(ActualPrice.cost_model_id == cm.id).all()
        assert len(ap) == 1 and float(ap[0].price) == 1908.0
        assert ap[0].year == 2026 and ap[0].quarter == 3

        # The row that had no period can be committed once a human supplies it,
        # and re-issuing the same quarter is a correction, not a duplicate.
        r = c.post(f"/api/price-lists/runs/{run_id}/commit",
                   json={"rows": [{"row_id": rows[1]["id"], "year": 2026, "quarter": 3}]})
        assert r.json()["committed"] == 1
        db.expire_all()
        ap = db.query(ActualPrice).filter(ActualPrice.cost_model_id == cm.id).all()
        assert len(ap) == 1, "same (cost model, quarter) upserts rather than duplicating"
        assert float(ap[0].price) == 1900.0

        # Committing twice is refused rather than silently re-writing.
        r = c.post(f"/api/price-lists/runs/{run_id}/commit",
                   json={"rows": [{"row_id": rows[1]["id"]}]})
        assert r.json()["committed"] == 0
        assert "already" in r.json()["results"][0]["error"].lower()
    finally:
        _cleanup(db, [run_id] if run_id else [], [cm.id], [prod.id])


def test_commit_refuses_a_cost_model_from_another_team(db, tenant_a, tenant_b, client_as, monkeypatch):
    prod_a = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "Shared Name")
    cm_a = _mk_cost_model(db, tenant_a["team_id"], tenant_a["user_id"], prod_a)
    prod_b = _mk_product(db, tenant_b["team_id"], tenant_b["user_id"], "Other Team Product")
    cm_b = _mk_cost_model(db, tenant_b["team_id"], tenant_b["user_id"], prod_b)
    db.commit()

    _mock_extract(monkeypatch, [
        {"product_reference": _field("Shared Name"), "price": _field(10.0),
         "quote_date": _field("2026-05-05")},
    ])
    c = client_as(tenant_a)
    run_id = None
    try:
        run = _upload(c, tenant_a["team_id"]).json()
        run_id = run["id"]
        r = c.post(f"/api/price-lists/runs/{run_id}/commit",
                   json={"rows": [{"row_id": run["rows"][0]["id"], "cost_model_id": str(cm_b.id)}]})
        assert r.json()["committed"] == 0
        assert "team" in r.json()["results"][0]["error"].lower()
        assert db.query(ActualPrice).filter(ActualPrice.cost_model_id == cm_b.id).count() == 0
    finally:
        _cleanup(db, [run_id] if run_id else [], [cm_a.id, cm_b.id], [prod_a.id, prod_b.id])


def test_skip_takes_a_row_out_of_the_queue(db, tenant_a, client_as, monkeypatch):
    _mock_extract(monkeypatch, [{"product_reference": _field("Nothing"), "price": _field(1.0)}])
    c = client_as(tenant_a)
    run_id = None
    try:
        run = _upload(c, tenant_a["team_id"]).json()
        run_id = run["id"]
        r = c.post(f"/api/price-lists/rows/{run['rows'][0]['id']}/skip")
        assert r.status_code == 200
        body = r.json()
        assert body["rows"][0]["status"] == "skipped"
        assert body["status"] == "reviewed", "a run with nothing pending is reviewed"
    finally:
        _cleanup(db, [run_id] if run_id else [])


def test_permission_and_tenancy_gates(db, tenant_a, tenant_b, client_as, monkeypatch):
    _mock_extract(monkeypatch, [{"product_reference": _field("X"), "price": _field(1.0)}])
    run_id = None
    try:
        run = _upload(client_as(tenant_a), tenant_a["team_id"]).json()
        run_id = run["id"]

        # Another team cannot read it.
        r = client_as(tenant_b).get(f"/api/price-lists/runs/{run_id}")
        assert r.status_code in (403, 404), r.text

        # ...nor commit through it.
        r = client_as(tenant_b).post(f"/api/price-lists/runs/{run_id}/commit",
                                     json={"rows": [{"row_id": run["rows"][0]["id"]}]})
        assert r.status_code in (403, 404), r.text

        # Uploading into a team you are not on is refused.
        r = _upload(client_as(tenant_b), tenant_a["team_id"])
        assert r.status_code == 403, r.text
    finally:
        _cleanup(db, [run_id] if run_id else [])


def test_unauthenticated_is_401(client):
    r = client.get(f"/api/price-lists/runs?team_id={uuid.uuid4()}")
    assert r.status_code == 401
