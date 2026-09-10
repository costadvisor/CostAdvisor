"""Quote & price-list extraction service (Scrum 31b).

Field-extraction logic is tested as pure functions over hand-built `pages`
dicts — never through real PDF rendering, so tests are fast and
deterministic. Only the "document can't be read" case exercises the real
pdfplumber.open boundary, fed genuinely invalid bytes. API-flow tests mock
`extract_quote` at the router boundary so confirm/reject/RLS/round-trip
tests don't depend on PDF parsing at all.

Covers every acceptance criterion:
- AC1: nothing lands in the quote record until confirmed.
- AC2: every field carries a confidence + locator.
- AC3: absent fields are absent, never defaulted (including cascading
  absence — no quote_date means no computed valid_until either).
- AC4: a multi-product/multi-tier table yields multiple lines.
- AC5: an unreadable document fails structurally, same shape as the
  existing parsers' ValueError path.
- AC6: round-trip — confirm lands in the quote record, the position engine
  computes against it, ActualPrice is unchanged.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.formula_template import FormulaRegionCoverage, FormulaTemplate
from app.models.index_data import CommodityIndex, IndexValue
from app.models.price_data import ActualPrice
from app.services import quote_extraction as qe


# ── Pure extraction-logic tests (no API, no PDF rendering) ─────────────────

def test_structural_failure_raises_value_error():
    try:
        qe.extract_quote(b"not a pdf", "quote.pdf")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "quote.pdf" in str(exc)


def test_multi_product_multi_tier_table():
    pages = [{"page": 1, "text": "", "tables": [[
        ["Product", "Price", "Currency", "Unit"],
        ["Widget A", "100", "USD", "kg"],
        ["Widget B", "90", "USD", "kg"],
        ["Widget C", "80", "USD", "kg"],
    ]]}]
    lines = qe._lines_from_tables(pages)
    assert len(lines) == 3
    assert lines[0]["product_reference"]["value"] == "Widget A"
    assert lines[1]["price"]["value"] == 90.0


def test_absent_fields_not_defaulted():
    # No Incoterm anywhere -> the key is absent, not null.
    pages = [{"page": 1, "text": "Price: $100 USD", "tables": []}]
    line = qe._line_from_full_text(pages)
    assert "incoterm" not in line
    assert "named_place" not in line

    # "Valid for 30 days" with no quote_date found -> valid_until stays
    # absent too (can't compute a relative date from nothing).
    pages2 = [{"page": 1, "text": "Price: $100 USD\nValid for 30 days", "tables": []}]
    line2 = qe._line_from_full_text(pages2)
    assert "valid_until" not in line2

    # With a quote_date present, the same phrase now resolves.
    pages3 = [{"page": 1, "text": "Price: $100\nQuote Date: 2025-01-01\nValid for 30 days", "tables": []}]
    line3 = qe._line_from_full_text(pages3)
    assert line3["valid_until"]["value"] == "2025-01-31"


def test_confidence_and_locator_on_every_field():
    # Table-derived: explicit column header -> CONF_LABELED.
    pages = [{"page": 3, "text": "", "tables": [[
        ["Product", "Price"], ["Widget", "100"],
    ]]}]
    line = qe._lines_from_tables(pages)[0]
    assert line["price"]["confidence"] == qe.CONF_LABELED
    assert line["price"]["locator"]["page"] == 3
    assert line["price"]["locator"]["snippet"]

    # Full-text fallback, no label -> a lower confidence tier, still with a locator.
    pages2 = [{"page": 1, "text": "Somewhere in here: $250 total", "tables": []}]
    line2 = qe._line_from_full_text(pages2)
    assert line2["price"]["confidence"] < qe.CONF_LABELED
    assert line2["price"]["locator"]["page"] == 1


def test_incoterm_case_sensitive_avoids_english_word_false_positive():
    # "for" (lowercase, common English word) must not be mistaken for the
    # deprecated Incoterm code "FOR".
    pages = [{"page": 1, "text": "Valid for 30 days. Price: $100", "tables": []}]
    line = qe._line_from_full_text(pages)
    assert "incoterm" not in line


# ── API-flow tests (extract_quote mocked at the router boundary) ───────────

FAKE_LINES = [
    {
        "product_reference": {"value": "Widget A", "confidence": 0.9, "locator": {"page": 1, "snippet": "Widget A"}},
        "price": {"value": 1240.0, "confidence": 0.9, "locator": {"page": 1, "snippet": "Price: 1240"}},
        "currency": {"value": "USD", "confidence": 0.9, "locator": {"page": 1, "snippet": "USD"}},
        "unit": {"value": "kg", "confidence": 0.9, "locator": {"page": 1, "snippet": "kg"}},
        "incoterm": {"value": "FOB", "confidence": 0.9, "locator": {"page": 1, "snippet": "FOB Shanghai"}},
    },
    {
        "product_reference": {"value": "Widget B", "confidence": 0.6, "locator": {"page": 1, "snippet": "Widget B"}},
        "price": {"value": 900.0, "confidence": 0.6, "locator": {"page": 1, "snippet": "900"}},
    },
]


def _mock_extract(monkeypatch, lines=None):
    monkeypatch.setattr(
        "app.routers.quotes.extract_quote",
        lambda content, filename: {"extracted_text": "mock extracted text", "lines": lines if lines is not None else FAKE_LINES},
    )


def _upload(c, team_id):
    return c.post(
        f"/api/quotes/extract?team_id={team_id}",
        files={"file": ("quote.pdf", b"%PDF-fake", "application/pdf")},
    )


def _cleanup_quotes(db, run_ids=()):
    bypass_rls_var.set(True)
    for rid in run_ids:
        db.execute(text("DELETE FROM quote_extraction_runs WHERE id = :id"), {"id": str(rid)})
    db.commit()


def test_extract_persists_draft_nothing_in_quote_record(db, tenant_a, client_as, monkeypatch):
    _mock_extract(monkeypatch)
    c = client_as(tenant_a)
    run_id = None
    try:
        r = _upload(c, tenant_a["team_id"])
        assert r.status_code == 201, r.text
        body = r.json()
        run_id = body["id"]
        assert len(body["lines"]) == 2
        assert all(l["status"] == "pending" for l in body["lines"])

        r = c.get("/api/quotes/records", params={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 200 and r.json() == []
    finally:
        _cleanup_quotes(db, [run_id] if run_id else [])


def test_confirm_reject_state_machine(db, tenant_a, client_as, monkeypatch):
    _mock_extract(monkeypatch)
    c = client_as(tenant_a)
    run_id = None
    try:
        r = _upload(c, tenant_a["team_id"])
        lines = r.json()["lines"]
        run_id = r.json()["id"]

        # Reject one line -> no quote record line created for it.
        r = c.post(f"/api/quotes/lines/{lines[1]['id']}/reject")
        assert r.status_code == 200, r.text

        # Confirm the other, with an override on price.
        r = c.post(f"/api/quotes/lines/{lines[0]['id']}/confirm", json={"price": 1300.0})
        assert r.status_code == 201, r.text
        rec_line = r.json()
        assert rec_line["price"] == 1300.0        # override wins
        assert rec_line["currency"] == "USD"       # extracted value used as-is
        assert rec_line["field_confidence"] is not None

        # Confirming an already-confirmed line fails.
        r = c.post(f"/api/quotes/lines/{lines[0]['id']}/confirm", json={})
        assert r.status_code == 400

        # The quote record now exists with exactly one line.
        r = c.get("/api/quotes/records", params={"team_id": str(tenant_a["team_id"])})
        records = r.json()
        assert len(records) == 1 and len(records[0]["lines"]) == 1
    finally:
        _cleanup_quotes(db, [run_id] if run_id else [])


def test_permission_and_rls(db, tenant_a, tenant_b, client_as, monkeypatch):
    _mock_extract(monkeypatch)
    c = client_as(tenant_a)
    run_id = None
    try:
        r = _upload(c, tenant_a["team_id"])
        run_id = r.json()["id"]
        line_id = r.json()["lines"][0]["id"]

        # tenant_b can't act on tenant_a's run/line — either RLS hides the
        # row (404) or, since this test's own `db` fixture bypasses RLS in
        # this same context (the established reason RLS-isolation tests
        # elsewhere in this repo use a direct _as_user() session instead of
        # the HTTP client), the row is visible and the permission check
        # rejects it instead (403). Either is a correct deny.
        r = client_as(tenant_b).get(f"/api/quotes/runs/{run_id}")
        assert r.status_code in (403, 404)
        r = client_as(tenant_b).post(f"/api/quotes/lines/{line_id}/confirm", json={})
        assert r.status_code in (403, 404)

        # tenant_b uploading to their own team is unaffected by tenant_a's data.
        r = _upload(client_as(tenant_b), tenant_b["team_id"])
        assert r.status_code == 201
        _cleanup_quotes(db, [r.json()["id"]])
    finally:
        _cleanup_quotes(db, [run_id] if run_id else [])


# ── Round-trip: confirm -> position engine computes against it -> ActualPrice untouched ──

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


def test_round_trip_negotiation_position_and_actual_price_untouched(db, tenant_a, client_as, monkeypatch):
    idx = _mk_index(db, f"IDX-{uuid.uuid4().hex[:8]}")
    db.add(IndexValue(commodity_id=idx.id, region="Europe", year=2025, quarter=1, value=100))
    db.commit()
    t = _mk_template(db, "quote-rt", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    run_id = None
    try:
        assert c.put(f"/api/formulas/{t.id}/components", json={"components": [
            {"name": "Fixed", "component_type": "fixed", "weight_pct": 100},
        ]}).status_code == 200
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_price": 1000, "currency": "EUR", "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        actual_price_count_before = db.query(ActualPrice).count()

        _mock_extract(monkeypatch, lines=[{
            "price": {"value": 1150.0, "confidence": 0.9, "locator": {"page": 1, "snippet": "1150"}},
            "currency": {"value": "EUR", "confidence": 0.9, "locator": {"page": 1, "snippet": "EUR"}},
        }])
        r = _upload(c, tenant_a["team_id"])
        run_id = r.json()["id"]
        line_id = r.json()["lines"][0]["id"]

        r = c.post(f"/api/quotes/lines/{line_id}/confirm", json={})
        assert r.status_code == 201, r.text
        quote_line_id = r.json()["id"]

        r = c.get(f"/api/formulas/{t.id}/negotiation-position", params={
            "team_id": str(tenant_a["team_id"]), "region": "Europe",
            "year": 2025, "quarter": 1, "quote_line_id": quote_line_id,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target"]["should_cost"] == 1000.0
        assert body["position"]["ask"] == 150.0
        assert body["position"]["unexplained_remainder"] == 150.0

        assert db.query(ActualPrice).count() == actual_price_count_before
    finally:
        _cleanup_quotes(db, [run_id] if run_id else [])
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM formula_templates WHERE id = :id"), {"id": str(t.id)})
        db.execute(text("DELETE FROM index_values WHERE commodity_id = :id"), {"id": idx.id})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :id"), {"id": idx.id})
        db.commit()


def test_negotiation_position_requires_exactly_one_supplier_source(db, tenant_a, client_as):
    t = _mk_template(db, "quote-validation", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    q = {"team_id": str(tenant_a["team_id"]), "region": "Europe", "year": 2025, "quarter": 1}
    try:
        # Neither given.
        r = c.get(f"/api/formulas/{t.id}/negotiation-position", params=q)
        assert r.status_code == 400

        # Both given.
        r = c.get(f"/api/formulas/{t.id}/negotiation-position", params={
            **q, "supplier_price": 100, "quote_line_id": str(uuid.uuid4()),
        })
        assert r.status_code == 400
    finally:
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM formula_templates WHERE id = :id"), {"id": str(t.id)})
        db.commit()


def test_negotiation_position_rejects_priceless_quote_line(db, tenant_a, client_as, monkeypatch):
    t = _mk_template(db, "quote-nopricel", tenant_a["user_id"], team_id=tenant_a["team_id"])
    c = client_as(tenant_a)
    run_id = None
    try:
        assert c.put(f"/api/formulas/{t.id}/coverage/Europe", json={
            "base_price": 100, "base_year": 2025, "base_quarter": 1,
        }).status_code == 200

        _mock_extract(monkeypatch, lines=[{
            "currency": {"value": "EUR", "confidence": 0.9, "locator": {"page": 1, "snippet": "EUR"}},
        }])
        r = _upload(c, tenant_a["team_id"])
        run_id = r.json()["id"]
        line_id = r.json()["lines"][0]["id"]

        r = c.post(f"/api/quotes/lines/{line_id}/confirm", json={})
        assert r.status_code == 201
        quote_line_id = r.json()["id"]
        assert r.json()["price"] is None

        r = c.get(f"/api/formulas/{t.id}/negotiation-position", params={
            "team_id": str(tenant_a["team_id"]), "region": "Europe",
            "year": 2025, "quarter": 1, "quote_line_id": quote_line_id,
        })
        assert r.status_code == 400
    finally:
        _cleanup_quotes(db, [run_id] if run_id else [])
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM formula_templates WHERE id = :id"), {"id": str(t.id)})
        db.commit()
