"""Scrum 27b — sheet round-trip mechanism (export -> edit offline -> reimport
-> diff -> apply), exercised against the one registered payload
(FormulaRegionCoverage base-price editing). One or more tests per acceptance
criterion, plus the concurrency behavior the ticket calls out explicitly.
"""
from __future__ import annotations

import io
import uuid

import openpyxl
import pytest

from datetime import datetime, timezone

from app.models.chemical_family import ChemicalFamily
from app.models.product_line import ProductLine
from app.models.formula_template import FormulaTemplate, FormulaRegionCoverage
from app.models.sheet_import_run import SheetImportRun

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def coverage_setup(db, tenant_a):
    suffix = uuid.uuid4().hex[:8]
    family = ChemicalFamily(name=f"Fam-{suffix}")
    db.add(family)
    db.flush()
    line_a = ProductLine(family_id=family.id, platform=f"PLAT-SA-{suffix}",
                         line_key=f"{family.name}|||LineA-{suffix}", name=f"LineA-{suffix}")
    line_b = ProductLine(family_id=family.id, platform=f"PLAT-SB-{suffix}",
                         line_key=f"{family.name}|||LineB-{suffix}", name=f"LineB-{suffix}")
    db.add_all([line_a, line_b])
    db.flush()

    def template(n, line):
        return FormulaTemplate(team_id=None, created_by=tenant_a["user_id"], name=f"T{n}-{suffix}",
                               code=f"T{n}-{suffix}", family_id=family.id,
                               product_line_id=line.id)

    t1, t2, t3 = template(1, line_a), template(2, line_a), template(3, line_b)
    # On line A but never in the sheet: a pointer card (not listed), and a
    # product whose only combo the source has withdrawn.
    t4, t5 = template(4, line_a), template(5, line_a)
    t4.card_kind = "pointer"
    db.add_all([t1, t2, t3, t4, t5])
    db.flush()

    c1 = FormulaRegionCoverage(template_id=t1.id, region="Europe", base_price=100, currency="USD",
                                margin_pct=10, base_year=2024, base_quarter=1,
                                needs_review=False, data_confidence="CONF-HIGH")
    c2 = FormulaRegionCoverage(template_id=t2.id, region="Europe", base_price=200, currency="USD",
                                margin_pct=12, base_year=2024, base_quarter=1,
                                needs_review=True, data_confidence="CONF-LOW")
    c3 = FormulaRegionCoverage(template_id=t3.id, region="Europe", base_price=300, currency="USD",
                                margin_pct=8, base_year=2024, base_quarter=1,
                                needs_review=False, data_confidence="CONF-HIGH")
    c4 = FormulaRegionCoverage(template_id=t4.id, region="Europe", base_price=400, currency="USD",
                                needs_review=False)
    c5 = FormulaRegionCoverage(template_id=t5.id, region="Europe", base_price=500, currency="USD",
                                needs_review=False, withdrawn_at=datetime.now(timezone.utc))
    db.add_all([c1, c2, c3, c4, c5])
    db.commit()

    yield {"family": family, "line_a": line_a, "line_b": line_b,
           "t1": t1, "t2": t2, "t3": t3, "t4": t4, "t5": t5}

    ids = [t.id for t in (t1, t2, t3, t4, t5)]
    for m in (FormulaRegionCoverage,):
        db.query(m).filter(m.template_id.in_(ids)).delete(synchronize_session=False)
    db.query(FormulaTemplate).filter(FormulaTemplate.id.in_(ids)).delete(synchronize_session=False)
    db.query(ProductLine).filter(ProductLine.id.in_([line_a.id, line_b.id])).delete(synchronize_session=False)
    db.query(ChemicalFamily).filter(ChemicalFamily.id == family.id).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def admin(user_factory, db):
    """A super-admin user, with any SheetImportRun rows it creates cleaned up
    before user_factory's own teardown deletes the user row (sheet_import_runs
    has no CASCADE on imported_by/applied_by — it's platform audit-trail data
    that should outlive a deleted user in production, so the FK is plain)."""
    u = user_factory(is_super_admin=True)
    yield u
    db.query(SheetImportRun).filter(
        (SheetImportRun.imported_by == u["user_id"]) | (SheetImportRun.applied_by == u["user_id"])
    ).delete(synchronize_session=False)
    db.commit()


def _export(client, product_line_id=None, needs_review=None, **extra):
    params = dict(extra)
    if product_line_id is not None:
        params["product_line_id"] = product_line_id
    if needs_review is not None:
        params["needs_review"] = needs_review
    r = client.get("/api/sheets/formula_coverage_price/export", params=params)
    assert r.status_code == 200, r.text
    return r.content


def _load(content: bytes):
    return openpyxl.load_workbook(io.BytesIO(content))


def _find_col(ws, label_prefix: str) -> int:
    for cell in ws[1]:
        if cell.value and str(cell.value).startswith(label_prefix):
            return cell.column
    raise AssertionError(f"column '{label_prefix}' not found in header row")


def _save(wb) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import(client, content: bytes, product_line_id=None, needs_review=None):
    params = {}
    if product_line_id is not None:
        params["product_line_id"] = product_line_id
    if needs_review is not None:
        params["needs_review"] = needs_review
    r = client.post(
        "/api/sheets/formula_coverage_price/import",
        params=params,
        files={"file": ("edited.xlsx", content, XLSX_MEDIA)},
    )
    return r


# ── AC1 ──────────────────────────────────────────────────────────────────

def _codes(content: bytes) -> list:
    ws = _load(content).active
    code_col = _find_col(ws, "Formula Code")
    return [row[code_col - 1].value for row in ws.iter_rows(min_row=2)]


def test_export_filters_by_product_line_and_needs_review(client_as, admin, coverage_setup):
    c = client_as(admin)
    content = _export(c, product_line_id=coverage_setup["line_a"].id)
    wb = _load(content)
    ws = wb.active
    code_col = _find_col(ws, "Formula Code")
    codes = {row[code_col - 1].value for row in ws.iter_rows(min_row=2)}
    assert codes == {coverage_setup["t1"].code, coverage_setup["t2"].code}

    content2 = _export(c, product_line_id=coverage_setup["line_a"].id, needs_review=True)
    wb2 = _load(content2)
    ws2 = wb2.active
    codes2 = {row[code_col - 1].value for row in ws2.iter_rows(min_row=2)}
    assert codes2 == {coverage_setup["t2"].code}


def test_the_line_and_family_filters_narrow_the_export(client_as, admin, coverage_setup):
    """Pydantic drops an unknown field silently, so a filter the spec does not
    declare would export the whole catalogue: each filter must actually narrow."""
    c = client_as(admin)
    s = coverage_setup
    everything = _codes(_export(c))
    line_a = _codes(_export(c, product_line_id=s["line_a"].id))
    line_b = _codes(_export(c, product_line_id=s["line_b"].id))
    family = _codes(_export(c, family_id=s["family"].id))
    assert sorted(line_a) == sorted([s["t1"].code, s["t2"].code])
    assert line_b == [s["t3"].code]
    assert sorted(family) == sorted(line_a + line_b)
    assert set(family) < set(everything)


def test_the_export_holds_listed_cards_and_live_combos_only(client_as, admin, coverage_setup):
    """A pointer card is not listed, and a withdrawn combo is no longer priced
    by the source: neither is in the sheet, filtered or not."""
    c = client_as(admin)
    s = coverage_setup
    for codes in (_codes(_export(c)), _codes(_export(c, family_id=s["family"].id))):
        assert s["t4"].code not in codes
        assert s["t5"].code not in codes


def test_export_locks_readonly_and_key_columns_not_editable_columns(client_as, admin, coverage_setup):
    c = client_as(admin)
    content = _export(c, product_line_id=coverage_setup["line_a"].id)
    wb = _load(content)
    ws = wb.active
    assert ws.protection.sheet is True

    key_col = _find_col(ws, "Formula Code")
    editable_col = _find_col(ws, "Base Price")
    readonly_col = _find_col(ws, "Data Confidence")

    assert ws.cell(row=2, column=key_col).protection.locked is True
    assert ws.cell(row=2, column=editable_col).protection.locked is False
    assert ws.cell(row=2, column=readonly_col).protection.locked is True


def test_export_requires_formulas_edit_permission(client_as, tenant_a, coverage_setup):
    r = client_as(tenant_a).get("/api/sheets/formula_coverage_price/export",
                                 params={"product_line_id": coverage_setup["line_a"].id})
    assert r.status_code == 403


# ── AC2 ──────────────────────────────────────────────────────────────────

def test_reimport_unmodified_export_is_empty(client_as, admin, coverage_setup):
    c = client_as(admin)
    content = _export(c, product_line_id=coverage_setup["line_a"].id)
    r = _import(c, content, product_line_id=coverage_setup["line_a"].id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "empty"
    assert body["diffs"] == []


# ── AC3 ──────────────────────────────────────────────────────────────────

def test_reimport_edited_sheet_produces_named_diff_and_import_never_mutates(client_as, admin, coverage_setup, db):
    c = client_as(admin)
    content = _export(c, product_line_id=coverage_setup["line_a"].id)
    wb = _load(content)
    ws = wb.active
    price_col = _find_col(ws, "Base Price")
    code_col = _find_col(ws, "Formula Code")

    t1_code = coverage_setup["t1"].code
    for row in ws.iter_rows(min_row=2):
        if row[code_col - 1].value == t1_code:
            row[price_col - 1].value = 150

    r = _import(c, _save(wb), product_line_id=coverage_setup["line_a"].id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "diffed"
    changes = [d for d in body["diffs"] if d["kind"] == "change"]
    assert len(changes) == 1
    d = changes[0]
    assert d["row_key"] == {"code": t1_code, "region": "Europe"}
    assert d["column"] == "base_price"
    assert d["old_value"] == "100.0"
    assert d["new_value"] == "150.0"
    assert d["applied"] is False

    db.expire_all()
    cov = db.query(FormulaRegionCoverage).filter(FormulaRegionCoverage.template_id == coverage_setup["t1"].id).first()
    assert float(cov.base_price) == 100.0  # import never mutates

    run_id = body["id"]
    apply_r = c.post(f"/api/sheets/import-runs/{run_id}/apply")
    assert apply_r.status_code == 200, apply_r.text
    apply_body = apply_r.json()
    assert len(apply_body["applied"]) == 1
    assert apply_body["run"]["status"] == "applied"

    db.expire_all()
    cov = db.query(FormulaRegionCoverage).filter(FormulaRegionCoverage.template_id == coverage_setup["t1"].id).first()
    assert float(cov.base_price) == 150.0


# ── AC4 ──────────────────────────────────────────────────────────────────

def test_reordered_rows_still_rekey_by_business_key(client_as, admin, coverage_setup, db):
    c = client_as(admin)
    content = _export(c, product_line_id=coverage_setup["line_a"].id)
    wb = _load(content)
    ws = wb.active
    price_col = _find_col(ws, "Base Price")
    code_col = _find_col(ws, "Formula Code")

    data_rows = [[cell.value for cell in row] for row in ws.iter_rows(min_row=2)]
    assert len(data_rows) == 2
    data_rows.reverse()  # simulate a human sorting the sheet

    t2_code = coverage_setup["t2"].code
    for r_idx, row_values in enumerate(data_rows, start=2):
        for c_idx, val in enumerate(row_values, start=1):
            ws.cell(row=r_idx, column=c_idx, value=val)
        if row_values[code_col - 1] == t2_code:
            ws.cell(row=r_idx, column=price_col, value=250)

    r = _import(c, _save(wb), product_line_id=coverage_setup["line_a"].id)
    assert r.status_code == 200, r.text
    changes = [d for d in r.json()["diffs"] if d["kind"] == "change"]
    assert len(changes) == 1
    assert changes[0]["row_key"]["code"] == t2_code
    assert changes[0]["new_value"] == "250.0"


# ── AC5 ──────────────────────────────────────────────────────────────────

def test_readonly_column_edit_is_rejected_not_applied(client_as, admin, coverage_setup, db):
    c = client_as(admin)
    content = _export(c, product_line_id=coverage_setup["line_a"].id)
    wb = _load(content)
    ws = wb.active
    conf_col = _find_col(ws, "Data Confidence")
    code_col = _find_col(ws, "Formula Code")
    t1_code = coverage_setup["t1"].code
    for row in ws.iter_rows(min_row=2):
        if row[code_col - 1].value == t1_code:
            row[conf_col - 1].value = "CONF-LOW"

    r = _import(c, _save(wb), product_line_id=coverage_setup["line_a"].id)
    assert r.status_code == 200, r.text
    body = r.json()
    rejected = [d for d in body["diffs"] if d["kind"] == "rejected_readonly_edit"]
    assert len(rejected) == 1
    assert rejected[0]["column"] == "data_confidence"
    assert not any(d["kind"] == "change" for d in body["diffs"])

    # Even calling apply must never touch it — only "change" diffs are appliable.
    c.post(f"/api/sheets/import-runs/{body['id']}/apply")
    db.expire_all()
    cov = db.query(FormulaRegionCoverage).filter(FormulaRegionCoverage.template_id == coverage_setup["t1"].id).first()
    assert cov.data_confidence == "CONF-HIGH"


# ── AC6 ──────────────────────────────────────────────────────────────────

def test_import_run_is_persisted_and_fetchable(client_as, admin, coverage_setup):
    c = client_as(admin)
    content = _export(c, product_line_id=coverage_setup["line_a"].id)
    r = _import(c, content, product_line_id=coverage_setup["line_a"].id)
    run_id = r.json()["id"]

    fresh = c.get(f"/api/sheets/import-runs/{run_id}")
    assert fresh.status_code == 200
    assert fresh.json()["id"] == run_id
    assert fresh.json()["status"] == "empty"


def test_list_import_runs_by_payload_key(client_as, admin, coverage_setup):
    c = client_as(admin)
    content = _export(c, product_line_id=coverage_setup["line_a"].id)
    _import(c, content, product_line_id=coverage_setup["line_a"].id)

    listed = c.get("/api/sheets/import-runs", params={"payload_key": "formula_coverage_price"})
    assert listed.status_code == 200
    assert len(listed.json()) >= 1


# ── Concurrency ──────────────────────────────────────────────────────────

def test_apply_skips_stale_row_when_live_value_changed_since_diff(client_as, admin, coverage_setup, db):
    c = client_as(admin)
    content = _export(c, product_line_id=coverage_setup["line_a"].id)
    wb = _load(content)
    ws = wb.active
    price_col = _find_col(ws, "Base Price")
    code_col = _find_col(ws, "Formula Code")
    t1_code = coverage_setup["t1"].code
    for row in ws.iter_rows(min_row=2):
        if row[code_col - 1].value == t1_code:
            row[price_col - 1].value = 150

    r = _import(c, _save(wb), product_line_id=coverage_setup["line_a"].id)
    run_id = r.json()["id"]

    # Simulate a second officer's concurrent change landing first.
    cov = db.query(FormulaRegionCoverage).filter(FormulaRegionCoverage.template_id == coverage_setup["t1"].id).first()
    cov.base_price = 999
    db.commit()

    apply_r = c.post(f"/api/sheets/import-runs/{run_id}/apply")
    assert apply_r.status_code == 200, apply_r.text
    body = apply_r.json()
    assert len(body["applied"]) == 0
    assert len(body["skipped_stale"]) == 1

    db.expire_all()
    cov = db.query(FormulaRegionCoverage).filter(FormulaRegionCoverage.template_id == coverage_setup["t1"].id).first()
    assert float(cov.base_price) == 999.0  # untouched by this apply


# ── Extras ───────────────────────────────────────────────────────────────

def test_invalid_value_reported_not_silently_dropped(client_as, admin, coverage_setup):
    c = client_as(admin)
    content = _export(c, product_line_id=coverage_setup["line_a"].id)
    wb = _load(content)
    ws = wb.active
    price_col = _find_col(ws, "Base Price")
    code_col = _find_col(ws, "Formula Code")
    t1_code = coverage_setup["t1"].code
    for row in ws.iter_rows(min_row=2):
        if row[code_col - 1].value == t1_code:
            row[price_col - 1].value = "not-a-number"

    r = _import(c, _save(wb), product_line_id=coverage_setup["line_a"].id)
    assert r.status_code == 200, r.text
    invalid = [d for d in r.json()["diffs"] if d["kind"] == "invalid_value"]
    assert len(invalid) == 1
    assert invalid[0]["column"] == "base_price"


def test_unmatched_key_reported(client_as, admin, coverage_setup):
    c = client_as(admin)
    content = _export(c, product_line_id=coverage_setup["line_a"].id)
    wb = _load(content)
    ws = wb.active
    code_col = _find_col(ws, "Formula Code")
    ws.cell(row=2, column=code_col, value="NO-SUCH-CODE")

    r = _import(c, _save(wb), product_line_id=coverage_setup["line_a"].id)
    assert r.status_code == 200, r.text
    unmatched = [d for d in r.json()["diffs"] if d["kind"] == "unmatched_key"]
    assert len(unmatched) == 1


def test_import_requires_formulas_edit_permission(client_as, tenant_a, coverage_setup):
    r = _import(client_as(tenant_a), b"irrelevant", product_line_id=coverage_setup["line_a"].id)
    assert r.status_code == 403


def test_apply_is_idempotent_on_already_applied_diffs(client_as, admin, coverage_setup, db):
    c = client_as(admin)
    content = _export(c, product_line_id=coverage_setup["line_a"].id)
    wb = _load(content)
    ws = wb.active
    price_col = _find_col(ws, "Base Price")
    code_col = _find_col(ws, "Formula Code")
    t1_code = coverage_setup["t1"].code
    for row in ws.iter_rows(min_row=2):
        if row[code_col - 1].value == t1_code:
            row[price_col - 1].value = 150

    r = _import(c, _save(wb), product_line_id=coverage_setup["line_a"].id)
    run_id = r.json()["id"]

    first = c.post(f"/api/sheets/import-runs/{run_id}/apply")
    assert len(first.json()["applied"]) == 1
    second = c.post(f"/api/sheets/import-runs/{run_id}/apply")
    assert second.status_code == 200
    assert len(second.json()["applied"]) == 0
    assert len(second.json()["skipped_stale"]) == 0

    db.expire_all()
    cov = db.query(FormulaRegionCoverage).filter(FormulaRegionCoverage.template_id == coverage_setup["t1"].id).first()
    assert float(cov.base_price) == 150.0


# ── Filter binding is per-payload (B5 regression) ────────────────────────────

class _FakeRequest:
    """Only `query_params` is read by `_bind_filter`; a real Request needs an
    ASGI scope this test has no reason to build."""

    def __init__(self, params):
        self.query_params = params


def test_each_payload_binds_its_own_filter_fields():
    """The router used to hardcode the FIRST payload's filter fields and pass
    them into whatever spec was asked for. Pydantic drops unknown kwargs
    silently, so `dimension_decision`'s `kind` was always None and every export
    covered the whole unresolved register regardless of the facet picked —
    silently wrong data rather than an error.

    Needs no database: it is a pure check that each spec binds from its own
    `model_fields`.
    """
    from app.routers.sheets import _bind_filter
    from app.services.sheet_roundtrip import PAYLOAD_REGISTRY

    qs = {"kind": "industry", "min_occurrences": "5",
          "product_line_id": "7", "family_id": "3", "needs_review": "true", "bogus": "x"}

    prices = _bind_filter(PAYLOAD_REGISTRY["formula_coverage_price"], _FakeRequest(qs))
    assert prices.product_line_id == 7
    assert prices.family_id == 3
    assert prices.needs_review is True
    assert not hasattr(prices, "kind"), "price filter must not grow a dimension field"

    dims = _bind_filter(PAYLOAD_REGISTRY["dimension_decision"], _FakeRequest(qs))
    assert dims.kind == "industry", "the facet must actually reach the filter"
    assert dims.min_occurrences == 5
    assert not hasattr(dims, "product_line_id")


def test_an_unknown_query_param_is_ignored_but_a_bad_value_is_refused():
    """A stray param is not an error; a malformed value for a REAL field must
    not fall back to the default and quietly widen the slice."""
    import pytest as _pytest
    from fastapi import HTTPException
    from app.routers.sheets import _bind_filter
    from app.services.sheet_roundtrip import PAYLOAD_REGISTRY

    spec = PAYLOAD_REGISTRY["dimension_decision"]
    assert _bind_filter(spec, _FakeRequest({"nonsense": "1"})).kind is None

    with _pytest.raises(HTTPException) as exc:
        _bind_filter(spec, _FakeRequest({"min_occurrences": "not-a-number"}))
    assert exc.value.status_code == 422
