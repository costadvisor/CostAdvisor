"""The Intelligence reference API (routers/intel_reference.py, services/intel_reference.py).

Runs on a test database that holds a content load of the current drop
(`content_loaded`: it fails, never skips, without one). Every expected number
and name comes from the drop files at run time (`tests/content_drop_expect.py`)
or from the loaded rows; no drop text lives here. The module writes nothing
but the throwaway users and teams `user_factory` creates and removes.

What is pinned (design §4.2, WP-7):

* auth: every endpoint is 401 without a session;
* lines: every visible line is listed, the counts agree with the drop's
  listed and default-view sets, `?status=` narrows the products, a line
  answers by id, by its current key and by a former key;
* no supplier count, no share, no `confidence`, no authored flag text, no
  line `axis_meta`, no out-row `why`, no category note anywhere;
* a line key that is not a current line reads "Product line not yet
  published", and the deliberately unnamed line names never appear;
* report caveats name unpublished joins without their key;
* industries: out rows carry a reason label and their `to` industries linked
  by name (the exact names with a comma stay whole);
* suppliers: the directory is exactly the producers with a counting link on a
  listed card; buckets are only in the generic note; detail lists links with
  their evidence label, counting links first;
* the snapshot resolves keys with the loader's rule, and is reused until reset;
* startup warms the snapshots in the background, best-effort;
* warm latency < 500 ms per endpoint.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import types
import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import select, text

from app.services import intel_reference as svc
from app.services.catalog_visibility import EVIDENCE_LABEL_TEXT, listed_clause
from tests import content_drop_expect as expect

pytestmark = pytest.mark.usefixtures("content_loaded")

# Keys that must never appear in any payload of this API (design §4.1, §4.2).
BANNED_KEYS = frozenset({
    "supplier_count", "share_pct", "share_disclosed", "disclosed_share_count", "share",
    "integration_why", "role_why", "role_changed", "count_why", "confidence", "axis_meta",
    "why_this_name", "note", "notes", "why", "to_was", "to_industry", "is_demo_grade",
    "is_line_only", "maker_quote", "maker_source", "internal_meta", "archival_note",
})


def _keys(value, found: set | None = None) -> set:
    found = set() if found is None else found
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(k)
            _keys(v, found)
    elif isinstance(value, list):
        for v in value:
            _keys(v, found)
    return found


def _assert_clean(payload, where: str) -> None:
    keys = _keys(payload)
    assert not keys & BANNED_KEYS, f"{where}: banned keys {sorted(keys & BANNED_KEYS)}"
    assert not {k for k in keys if k.startswith("_")}, f"{where}: private keys"
    blob = json.dumps(payload, ensure_ascii=False)
    for i, name in enumerate(expect.unpublished_line_names()):
        assert name not in blob, f"{where}: holds unpublished line name #{i}"


def _enc(key: str) -> str:
    return quote(key, safe="")


@pytest.fixture
def api(user_factory, client_as):
    return client_as(user_factory())


@pytest.fixture(scope="module")
def drop():
    """What the drop says, computed once."""
    listed = set(expect.listed_codes())
    default = set(expect.default_view_codes())
    by_line: dict[str, set[str]] = {}
    for pid in listed:
        key = expect.template_line(pid)
        if key:
            by_line.setdefault(key, set()).add(pid)
    return {"lines": expect.product_lines(), "listed": listed, "default": default,
            "cards_by_line": by_line, "lines_with_listed": expect.lines_with_listed()}


def _line_ids(db) -> dict[str, int]:
    return dict(db.execute(text("SELECT line_key, id FROM product_lines")).all())


def _hidden_keys(db) -> set[str]:
    return set(db.execute(text(
        "SELECT line_key FROM product_lines WHERE retired_at IS NOT NULL "
        "OR coalesce(flags ? 'do_not_publish', false)")).scalars())


def _report_with_joins(db, *, with_null: bool = False, min_rows: int = 2) -> str | None:
    """A report with at least `min_rows` joins: all published, or (with_null)
    some published and some not."""
    if with_null:
        cond = ("HAVING count(*) >= :n AND count(*) FILTER (WHERE product_line_id IS NULL) > 0 "
                "AND count(*) FILTER (WHERE product_line_id IS NOT NULL) > 0")
    else:
        cond = "HAVING count(*) >= :n AND count(*) FILTER (WHERE product_line_id IS NULL) = 0"
    return db.execute(text(
        f"SELECT slug FROM market_report_lines GROUP BY slug {cond} ORDER BY count(*) DESC, slug "
        "LIMIT 1"), {"n": min_rows}).scalar()


def _directory_ids(db) -> set[str]:
    """Producers with a counting link on a listed card, by SQL (independent of
    the service's snapshot)."""
    from app.models.formula_template import FormulaTemplate as T
    from app.models.producer import Producer, ProducerFormula as PF
    q = (select(Producer.id).distinct()
         .join(PF, PF.producer_id == Producer.id)
         .join(T, T.code == PF.subject_code)
         .where(T.team_id.is_(None), listed_clause(T), PF.counts_toward_floor.is_(True),
                Producer.is_bucket.is_(False)))
    return {str(r) for r in db.execute(q).scalars()}


def _a_maker(db) -> str:
    """A directory producer linked to the leak-probe card (a listed LIVE card)."""
    row = db.execute(text(
        "SELECT p.id FROM producers p JOIN producer_formulas pf ON pf.producer_id = p.id "
        "WHERE pf.subject_code = :c AND pf.counts_toward_floor AND NOT p.is_bucket "
        "ORDER BY pf.row_order LIMIT 1"), {"c": expect.leak_probe_card()}).scalar()
    assert row is not None, "the leak-probe card has no counting maker"
    return str(row)


def _endpoints(db) -> list[str]:
    probe_line = _line_ids(db)[expect.template_line(expect.leak_probe_card())]
    ind = db.execute(text("SELECT slug FROM industries ORDER BY sort_order LIMIT 1")).scalar()
    return [
        "/api/intel/lines",
        f"/api/intel/lines/{probe_line}",
        f"/api/intel/reports/{_report_with_joins(db)}",
        "/api/intel/industries",
        f"/api/intel/industries/{ind}",
        "/api/intel/suppliers",
        f"/api/intel/suppliers/{_a_maker(db)}",
    ]


# ── Auth ─────────────────────────────────────────────────────────────────────

def test_every_endpoint_requires_a_session(client, db):
    for url in _endpoints(db):
        assert client.get(url).status_code == 401, url


# ── Lines ────────────────────────────────────────────────────────────────────

def test_line_list_lists_every_visible_line_with_drop_counts(api, db, drop):
    body = api.get("/api/intel/lines").json()
    _assert_clean(body, "/api/intel/lines")
    items = body["items"]
    hidden = _hidden_keys(db)
    assert body["total"] == len(items) == len(set(drop["lines"]) - hidden)
    assert {it["line_key"] for it in items} == set(drop["lines"]) - hidden
    assert body["counts"]["lines_with_listed"] == len(drop["lines_with_listed"] - hidden)
    assert sum(f["count"] for f in body["families"]) == body["total"]
    for fam in body["families"]:
        assert sum(s["count"] for s in fam["subfamilies"]) == fam["count"]
    for it in items:
        line = drop["lines"][it["line_key"]]
        assert it["name"] == line.name and it["family"]["name"] == line.family
        assert (it["subfamily"] or {}).get("name") == line.subfamily
        cards = drop["cards_by_line"].get(it["line_key"], set())
        assert set(it["pids"]) == cards
        assert it["product_count"] == len(it["pids"]) == it["counts"]["listed"]
        assert it["counts"]["default_view"] == len(cards & drop["default"])
        assert it["has_report"] == bool(it["reports"])
        assert all(f.keys() == {"code", "label"} for f in it["flags"])
    order = [(it["family"]["name"].lower(), it["subfamily"] is None,
              ((it["subfamily"] or {}).get("name") or "").lower(), it["name"].lower())
             for it in items]
    assert order == sorted(order)


def test_status_filter_narrows_products_not_lines(api, drop):
    full = {it["id"]: it for it in api.get("/api/intel/lines").json()["items"]}
    view = api.get("/api/intel/lines", params={"status": "live,supply_exception"}).json()
    assert view["total"] == len(full)
    for it in view["items"]:
        assert it["product_count"] == it["counts"]["default_view"]
        assert set(it["pids"]) == drop["cards_by_line"].get(it["line_key"], set()) & drop["default"]
        assert it["counts"] == full[it["id"]]["counts"]
    assert view["counts"]["lines_with_products"] == sum(
        1 for it in view["items"] if it["product_count"])
    # Verified first within a line.
    rank = {"live": 0, "supply_exception": 1, "supply_pending": 2, "not_audited": 3}
    statuses = {pid: s.supply_status for pid, s in expect.card_statuses().items()}
    for it in full.values():
        ranks = [rank[statuses[p]] for p in it["pids"]]
        assert ranks == sorted(ranks)
    assert api.get("/api/intel/lines", params={"status": "all"}).json()["total"] == len(full)
    for bad in ("verified", "all,live", "live,nope"):
        assert api.get("/api/intel/lines", params={"status": bad}).status_code == 422, bad


def test_line_filters(api, db):
    items = api.get("/api/intel/lines").json()["items"]
    fam = items[0]["family"]
    by_fam = api.get("/api/intel/lines", params={"family_id": fam["id"]}).json()
    assert by_fam["total"] == sum(1 for it in items if it["family"]["id"] == fam["id"]) > 0
    assert [f["id"] for f in by_fam["families"]] == [fam["id"]]

    sub = next(it["subfamily"] for it in items if it["subfamily"])
    by_sub = api.get("/api/intel/lines", params={"subfamily_id": sub["id"]}).json()
    assert by_sub["total"] >= 1
    assert all(it["subfamily"]["id"] == sub["id"] for it in by_sub["items"])

    no_rep = api.get("/api/intel/lines", params={"has_report": "false"}).json()
    assert no_rep["total"] and all(not it["has_report"] for it in no_rep["items"])

    ind = db.execute(text(
        "SELECT i.slug, i.name FROM industries i JOIN category_placements p "
        "ON p.industry_id = i.id GROUP BY i.slug, i.name ORDER BY count(*) DESC LIMIT 1")).first()
    by_slug = api.get("/api/intel/lines", params={"industry": ind.slug}).json()
    by_name = api.get("/api/intel/lines", params={"industry": ind.name}).json()
    assert by_slug["total"] == by_name["total"] > 0
    assert all(ind.name in it["industries"] for it in by_slug["items"])

    probe = expect.leak_probe_card()
    q = api.get("/api/intel/lines", params={"q": probe}).json()
    assert expect.template_line(probe) in {it["line_key"] for it in q["items"]}


def test_line_detail_by_id_key_and_former_key(api, db, drop):
    ids = _line_ids(db)
    probe = expect.leak_probe_card()
    key = expect.template_line(probe)
    d = api.get(f"/api/intel/lines/{ids[key]}")
    assert d.status_code == 200
    d = d.json()
    _assert_clean(d, "/api/intel/lines/{id}")
    assert d["line_key"] == key and probe in d["pids"]
    assert set(d["pids"]) == drop["cards_by_line"][key]
    assert [p["pid"] for p in d["products"]] == d["pids"]
    assert all(set(p["regions"]) <= set(svc.REGION_ORDER) for p in d["products"])
    assert all(p["status"] and p["status"].keys() == {"code", "label", "tone"}
               for p in d["products"])
    assert api.get("/api/intel/lines/" + _enc(key)).json()["id"] == d["id"]
    # Status filter on the detail.
    live = api.get(f"/api/intel/lines/{d['id']}", params={"status": "live"}).json()
    assert {p["status"]["code"] for p in live["products"]} <= {"live"}
    assert live["counts"] == d["counts"]

    # A former key answers with the current line (old links keep working).
    hidden = _hidden_keys(db)
    formers = [(old, ln.key) for ln in drop["lines"].values() for old in ln.former_keys
               if expect.resolve_line_key(old) == ln.key and ln.key not in hidden]
    assert formers, "the drop has no former line key to test with"
    for old, current in formers[:5]:
        r = api.get("/api/intel/lines/" + _enc(old))
        assert r.status_code == 200, old
        assert r.json()["line_key"] == current and r.json()["id"] == ids[current]
        assert r.json()["name"] not in r.json()["former_names"]


def test_line_detail_suppliers_are_counting_makers(api, db):
    key = expect.template_line(expect.leak_probe_card())
    d = api.get(f"/api/intel/lines/{_line_ids(db)[key]}").json()
    directory = _directory_ids(db)
    assert d["suppliers"]
    assert {s["id"] for s in d["suppliers"]} <= directory
    assert {s["id"] for s in d["top_suppliers"]} <= {s["id"] for s in d["suppliers"]}
    buckets = set(db.execute(text("SELECT name FROM producers WHERE is_bucket")).scalars())
    assert not {s["name"] for s in d["suppliers"]} & buckets
    assert set(d["generic_suppliers"]) <= buckets
    assert d["demand"]["industry_count"] == len(d["demand"]["industries"])


def test_hidden_and_unknown_lines_are_404(api, db):
    for key in _hidden_keys(db):
        assert api.get("/api/intel/lines/" + _enc(key)).status_code == 404
    assert api.get("/api/intel/lines/" + _enc("Nope|||Not a line")).status_code == 404
    # Unicode digits pass str.isdigit() but not int(): a 404, never a 500.
    for odd in ("²", "٣", " 12 ", "-1", "0", "999999999"):
        assert api.get("/api/intel/lines/" + _enc(odd)).status_code == 404, odd
    # An unpublished key is not a line either.
    for u in expect.unnamed_platforms():
        for key in u["todays_keys"]:
            assert api.get("/api/intel/lines/" + _enc(key)).status_code == 404


def test_only_do_not_publish_and_retirement_hide_a_line():
    assert svc.HIDDEN_LINE_FLAGS == ("do_not_publish",)
    assert svc._line_hidden({"retracted": "a naming rule"}, None) is False
    assert svc._line_hidden({"do_not_publish": True}, None) is True
    assert svc._line_hidden({}, "2026-10-01") is True


def test_flags_never_carry_authored_text(api, db):
    authored = [t for (t,) in db.execute(text(
        "SELECT value FROM product_lines, jsonb_each_text(coalesce(flags, '{}'::jsonb)) "
        "WHERE length(value) >= 20"))]
    items = api.get("/api/intel/lines").json()["items"]
    blob = json.dumps(items, ensure_ascii=False)
    for i, t in enumerate(authored):
        assert t not in blob, f"authored flag text #{i} served"
    pending = db.execute(text(
        "SELECT count(*) FROM product_lines WHERE flags->>'status' ILIKE '%pending%'")).scalar()
    assert sum(1 for it in items if it["pending"]) == pending
    assert all(it["flags"] == ([svc.PENDING_FLAG] if it["pending"] else []) for it in items)


# ── Reports ──────────────────────────────────────────────────────────────────

def test_report_and_caveat(api, db):
    slug = _report_with_joins(db)
    d = api.get(f"/api/intel/reports/{slug}").json()
    assert d["sections"] and all(s["html"] for s in d["sections"])
    n_rows = db.execute(text("SELECT count(*) FROM market_report_lines WHERE slug = :s"),
                        {"s": slug}).scalar()
    cav = d["caveat"]
    assert cav["split_into_n_lines"] == n_rows == len(cav["lines"]) == len(d["lines"])
    assert f"split into {n_rows} product lines" in cav["text"]
    assert d["old_line_name"] in cav["text"]
    assert {ln["id"] for ln in d["lines"]} == {ln["id"] for ln in cav["lines"]}
    assert "old_line_key" not in d


def test_caveat_names_an_unpublished_join_without_its_key(api, db):
    slug = _report_with_joins(db, with_null=True)
    if slug is None:
        pytest.skip("no report mixes published and unpublished joins in this drop")
    d = api.get(f"/api/intel/reports/{slug}").json()
    current = set(_line_ids(db))
    unpublished = [ln for ln in d["caveat"]["lines"] if ln["id"] is None]
    assert unpublished
    assert all(ln["line_key"] is None and ln["name"] == svc.UNPUBLISHED_LINE
               and ln["visible"] is False for ln in unpublished)
    assert all(ln["line_key"] in current for ln in d["caveat"]["lines"] if ln["id"] is not None)
    assert "not yet published" in d["caveat"]["text"]
    assert all(ln["line_key"] in current for ln in d["lines"])
    raw = db.execute(text("SELECT line_key FROM market_report_lines WHERE slug = :s "
                          "AND product_line_id IS NULL"), {"s": slug}).scalars().all()
    blob = json.dumps(d["caveat"]["lines"], ensure_ascii=False)
    assert not any(k in blob for k in raw)


def test_caveat_wording_for_zero_and_one_line(api, db):
    zero = db.execute(text(
        "SELECT r.slug FROM market_reports r WHERE NOT EXISTS "
        "(SELECT 1 FROM market_report_lines l WHERE l.slug = r.slug) LIMIT 1")).scalar()
    if zero:
        cav = api.get(f"/api/intel/reports/{zero}").json()["caveat"]
        assert cav["split_into_n_lines"] == 0 and cav["lines"] == []
        assert "No current product line maps to it." in cav["text"]
    one = db.execute(text(
        "SELECT slug FROM market_report_lines GROUP BY slug HAVING count(*) = 1 "
        "AND bool_and(product_line_id IS NOT NULL) LIMIT 1")).scalar()
    assert one
    cav = api.get(f"/api/intel/reports/{one}").json()["caveat"]
    assert cav["split_into_n_lines"] == 1
    assert f"That line is now called {cav['lines'][0]['name']}." in cav["text"]
    assert "1 product lines" not in cav["text"]


def test_unknown_report_is_404(api):
    assert api.get("/api/intel/reports/no_such_report").status_code == 404


def test_a_report_on_an_unpublished_line_only_is_not_served(api, db):
    """Its title is the unpublished line's name; no line links to it."""
    slugs = db.execute(text(
        "SELECT slug FROM market_report_lines GROUP BY slug "
        "HAVING bool_and(product_line_id IS NULL)")).scalars().all()
    if not slugs:
        pytest.skip("every report reaches a published line in this drop")
    for slug in slugs:
        assert api.get(f"/api/intel/reports/{slug}").status_code == 404, slug
    blob = json.dumps(api.get("/api/intel/lines").json(), ensure_ascii=False)
    assert not any(f'"{slug}"' in blob for slug in slugs)


# ── Industries ───────────────────────────────────────────────────────────────

def test_industry_list_mirrors_the_demand_tables(api, db):
    d = api.get("/api/intel/industries").json()
    assert d["total"] == len(d["items"]) == len(expect.industries())
    n_cats = db.execute(text("SELECT count(*) FROM categories")).scalar()
    assert sum(it["category_count"] for it in d["items"]) == n_cats
    assert sum(d["categories_by_status"].values()) == n_cats
    for it in d["items"]:
        assert sum(it["categories_by_status"].values()) == it["category_count"]


def test_every_industry_page_is_clean_and_null_safe(api, db, drop):
    current = set(_line_ids(db))
    for slug in db.execute(text("SELECT slug FROM industries ORDER BY sort_order")).scalars():
        d = api.get(f"/api/intel/industries/{slug}")
        assert d.status_code == 200, slug
        d = d.json()
        _assert_clean(d, f"/api/intel/industries/{slug}")
        assert len(d["categories"]) == d["category_count"]
        assert len(d["out"]) == d["out_count"]
        listed = set()
        for cat in d["categories"]:
            rows = cat["members"] + [ln for sh in cat["shared"] for ln in sh["lines"]]
            for m in rows:
                if m["line_key"] is None:
                    assert m["line_id"] is None and m["line_name"] == svc.UNPUBLISHED_LINE
                else:
                    assert m["line_key"] in current
                listed.update(p["pid"] for p in m["products"])
        assert listed <= drop["listed"]
        assert len(listed) == d["product_count"]
    assert api.get("/api/intel/industries/no_such_industry").status_code == 404


def test_unpublished_member_rows_keep_their_products(api, db):
    """A member row whose key is not a current line says so and still lists
    the cards it names (the tree places them)."""
    row = db.execute(text(
        "SELECT c.code, i.slug, m.pids FROM category_members m "
        "JOIN categories c ON c.id = m.category_id JOIN industries i ON i.id = c.industry_id "
        "WHERE m.line_key NOT IN (SELECT line_key FROM product_lines) "
        "AND NOT EXISTS (SELECT 1 FROM product_lines p, jsonb_array_elements_text("
        "coalesce(p.former_keys, '[]'::jsonb)) k WHERE k = m.line_key) "
        "AND m.pids IS NOT NULL ORDER BY c.code LIMIT 1")).first()
    assert row is not None, "the drop has no member row on an unpublished key"
    d = api.get(f"/api/intel/industries/{row.slug}").json()
    cat = next(c for c in d["categories"] if c["code"] == row.code)
    rows = [m for m in cat["members"] if m["line_key"] is None]
    assert rows and all(m["line_name"] == svc.UNPUBLISHED_LINE for m in rows)
    listed = set(expect.listed_codes())
    want = {p for p in row.pids if p in listed}
    assert want <= {p["pid"] for m in rows for p in m["products"]}


def test_out_rows_have_reasons_and_linked_industries(api, db):
    names = expect.industries()
    slugs = dict(db.execute(text("SELECT name, slug FROM industries")).all())
    expected = {(r.industry, r.pid): r for r in expect.out_rows()}
    exact_comma = 0
    for slug, name in db.execute(text("SELECT slug, name FROM industries")).all():
        for o in api.get(f"/api/intel/industries/{slug}").json()["out"]:
            if o["reason"]:
                assert o["reason"]["label"] == svc.OUT_REASONS.get(
                    o["reason"]["code"], svc.OUT_REASON_FALLBACK)
            want = expected[(name, o["pid"])]
            assert [(t["name"], t["slug"] is not None) for t in o["to_industries"]] == [
                (txt, ok) for txt, ok in want.to_industries]
            for t in o["to_industries"]:
                if t["slug"]:
                    assert slugs[t["name"]] == t["slug"]
            if want.split == "exact" and "," in (want.to_raw or ""):
                exact_comma += 1
                assert len(o["to_industries"]) == 1 and o["to_industries"][0]["name"] in names
            if o["product"] and o["product"]["redirect_to"]:
                assert o["product"]["listed"] is False
    assert exact_comma == expect.out_split_summary()["exact_with_comma"] > 0


# ── Suppliers ────────────────────────────────────────────────────────────────

def _all_suppliers(api) -> list[dict]:
    out, offset = [], 0
    while True:
        page = api.get("/api/intel/suppliers", params={"limit": 1000, "offset": offset}).json()
        out += page["items"]
        offset += len(page["items"])
        if not page["items"] or offset >= page["total"]:
            return out


def test_directory_is_exactly_the_counting_makers_on_listed_cards(api, db):
    items = _all_suppliers(api)
    first = api.get("/api/intel/suppliers", params={"limit": 1000}).json()
    _assert_clean(first, "/api/intel/suppliers")
    assert {it["id"] for it in items} == _directory_ids(db)
    assert first["total"] == len(items)
    counts = [it["product_count"] for it in items]
    assert counts == sorted(counts, reverse=True)
    buckets = set(db.execute(text("SELECT name FROM producers WHERE is_bucket")).scalars())
    assert not {it["name"] for it in items} & buckets
    assert {g["name"] for g in first["generic_suppliers"]} <= buckets
    for it in items[:50]:
        assert it["family_count"] == len(it["families"])
        assert sum(f["count"] for f in it["families"]) == it["product_count"]
        assert it["region_count"] == len(it["regions"])
        assert it["integrated_count"] <= it["product_count"]


def test_directory_product_count_is_counting_cards(api, db):
    sid = _a_maker(db)
    n = db.execute(text(
        "SELECT count(DISTINCT pf.subject_code) FROM producer_formulas pf "
        "JOIN formula_templates t ON t.code = pf.subject_code AND t.team_id IS NULL "
        "WHERE pf.producer_id = :p AND pf.counts_toward_floor AND t.card_kind IN "
        "('product', 'group') AND EXISTS (SELECT 1 FROM formula_region_coverage c "
        "WHERE c.template_id = t.id AND c.withdrawn_at IS NULL)"), {"p": sid}).scalar()
    d = api.get(f"/api/intel/suppliers/{sid}").json()
    assert d["product_count"] == n == sum(1 for p in d["products"] if p["counts"])


def test_supplier_filters_and_paging(api, db):
    full = api.get("/api/intel/suppliers", params={"limit": 1000}).json()
    items = full["items"]
    page = api.get("/api/intel/suppliers", params={"limit": 5, "offset": 5}).json()
    assert [it["id"] for it in page["items"]] == [it["id"] for it in items[5:10]]

    big = api.get("/api/intel/suppliers", params={"min_products": 5}).json()
    assert big["total"] > 0 and all(it["product_count"] >= 5 for it in big["items"])

    fam = full["families"][0]
    by_fam = api.get("/api/intel/suppliers", params={"family_id": fam["id"]}).json()
    assert by_fam["total"] == fam["count"] > 0
    assert all(any(f["id"] == fam["id"] for f in it["families"]) for it in by_fam["items"])

    ind = full["industries"][0]
    by_ind = api.get("/api/intel/suppliers", params={"industry": ind["slug"]}).json()
    assert by_ind["total"] == ind["count"] > 0

    integ = api.get("/api/intel/suppliers", params={"integrated": "true"}).json()
    assert all(it["integrated_count"] for it in integ["items"])

    name = api.get(f"/api/intel/suppliers/{_a_maker(db)}").json()["name"]
    found = api.get("/api/intel/suppliers", params={"q": name.lower()}).json()
    assert name in {it["name"] for it in found["items"]}


def test_supplier_detail_lists_links_with_evidence_counting_first(api, db):
    sid = _a_maker(db)
    d = api.get(f"/api/intel/suppliers/{sid}").json()
    _assert_clean(d, "/api/intel/suppliers/{id}")
    assert d["in_directory"] is True and d["is_bucket"] is False
    flags = [p["counts"] for p in d["products"]]
    assert flags == sorted(flags, reverse=True)
    stored = {code: (c, label) for code, c, label in db.execute(text(
        "SELECT subject_code, counts_toward_floor, evidence_label FROM producer_formulas "
        "WHERE producer_id = :p"), {"p": sid}).all()}
    listed = set(expect.listed_codes())
    for p in d["products"]:
        assert p["pid"] in listed
        assert (p["counts"], p["evidence"]["code"]) == stored[p["pid"]]
        assert p["evidence"]["label"] == EVIDENCE_LABEL_TEXT[p["evidence"]["code"]]
        if p["origin_restriction"]:
            assert p["origin_restriction"]["label"]
    assert {p["pid"] for p in d["products"]} | set(d["unlisted_codes"]) == set(stored)
    comp = d["competitors"]
    assert all(c["id"] != sid for c in comp)
    ranked = [(-c["shared_products"], -c["shared_lines"], c["name"].lower()) for c in comp]
    assert ranked == sorted(ranked)
    assert {c["id"] for c in comp} <= _directory_ids(db)


def test_a_maker_counting_only_off_cards_answers_but_is_not_listed(api, db):
    directory = _directory_ids(db)
    rows = db.execute(text(
        "SELECT p.id FROM producers p WHERE NOT p.is_bucket AND EXISTS "
        "(SELECT 1 FROM producer_formulas pf WHERE pf.producer_id = p.id "
        "AND pf.counts_toward_floor) ORDER BY p.name")).scalars().all()
    off = next((str(r) for r in rows if str(r) not in directory), None)
    if off is None:
        pytest.skip("every counting maker has a link on a listed card")
    d = api.get(f"/api/intel/suppliers/{off}").json()
    assert d["in_directory"] is False and d["competitors"] == []
    assert not any(p["counts"] for p in d["products"])
    assert off not in {it["id"] for it in _all_suppliers(api)}


def test_bucket_and_unknown_suppliers(api, db):
    bucket = db.execute(text(
        "SELECT p.id FROM producers p WHERE p.is_bucket AND EXISTS "
        "(SELECT 1 FROM producer_formulas pf WHERE pf.producer_id = p.id) LIMIT 1")).scalar()
    if bucket:
        d = api.get(f"/api/intel/suppliers/{bucket}").json()
        assert d["is_bucket"] is True and d["in_directory"] is False and d["competitors"] == []
    assert api.get(f"/api/intel/suppliers/{uuid.uuid4()}").status_code == 404
    assert api.get("/api/intel/suppliers/not-a-uuid").status_code == 422


# ── Snapshot, startup and latency ────────────────────────────────────────────

def test_snapshot_resolves_keys_with_the_loader_rule(db):
    from app.services.content_drop.taxonomy import line_index
    assert svc.get_snapshot(db).line_index == line_index(db)


def test_snapshot_is_reused_until_reset(db):
    first = svc.get_snapshot(db)
    assert svc.get_snapshot(db) is first
    svc.reset_cache()
    assert svc.get_snapshot(db) is not first


def test_startup_warms_in_the_background_and_never_fails(monkeypatch):
    """The lifespan runs `intel_warm.warm()` in a worker thread; startup does
    not wait for it, and a failure is logged, never raised."""
    from fastapi.testclient import TestClient
    import app.main as main
    import app.services as services_pkg

    called = threading.Event()
    release = threading.Event()

    def warm():
        called.set()
        release.wait(5)

    fake = types.ModuleType("app.services.intel_warm")
    fake.warm = warm
    monkeypatch.setitem(sys.modules, "app.services.intel_warm", fake)
    monkeypatch.setattr(services_pkg, "intel_warm", fake, raising=False)
    t0 = time.perf_counter()
    with TestClient(main.app) as c:
        assert c.get("/health").status_code == 200
        assert time.perf_counter() - t0 < 4   # did not wait for the warm-up
        assert called.wait(5)
        release.set()

    def broken():
        raise RuntimeError("no database")

    fake.warm = broken
    main._warm_intelligence()        # logged, not raised


def test_warm_latency_under_500ms(api, db):
    urls = _endpoints(db)
    for url in urls:
        assert api.get(url).status_code == 200, url  # warm
    for url in urls:
        best = min(_timed(api, url) for _ in range(3))
        assert best < 0.5, f"{url} took {best:.3f}s"


def _timed(api, url: str) -> float:
    t0 = time.perf_counter()
    api.get(url)
    return time.perf_counter() - t0
