"""The Intelligence catalogue API (app/routers/intel_catalogue.py) and its
in-process snapshot (app/services/intel_catalogue.py).

Read-only against a test database holding a content load of the current drop
(`content_loaded`: it fails, never skips, without one). Every expected count,
name and number is derived from the drop at run time through
`tests/content_drop_expect.py`, or read from the database; no drop text or
figure is written here. The pure tests (cleaners, status parsing, warnings)
need no database.

What is pinned, and why (design §2.3, §4.2, TRIM T7):
* the grid lists every listed card (kind product or group, with a live
  coverage row) and, with no `limit`, returns all of them;
* the default view is the verified and concentrated-supply cards; the other
  statuses sit behind the `status` filter;
* pointers and duplicates answer with their target and `redirected_from`;
* the makers panel comes from `producer_formulas` only, in authored order,
  with the evidence label and no share;
* warnings use fixed buyer-facing text, never the stored `why`;
* the market tab has no cone, says when its data ends and which forecast
  vintage it shows;
* the snapshot is reused while the load version holds and rebuilt when it
  moves, and a team's own rows never move it.
"""
from __future__ import annotations

import re
from collections import Counter

import pytest
from sqlalchemy import text

from app.models.formula_template import FormulaTemplate
from app.services import intel_catalogue as ic
from app.services import intel_warm
from app.services.catalog_visibility import EVIDENCE_LABEL_TEXT, STATUS_BADGES, STATUS_RANK
from app.services.content_drop import sanitize
from tests import content_drop_expect as expect

# The card the design's walk opens (an identifier, not drop text).
FERRIC = "BCI-FECL3-LIQ"
DEFAULT_VIEW = "live,supply_exception"


@pytest.fixture
def api(content_loaded, tenant_a, client_as):
    return client_as(tenant_a)


def _get(api, url, **params):
    r = api.get(url, params=params)
    assert r.status_code == 200, f"{url} {params} → {r.status_code}: {r.text[:300]}"
    return r.json()


def _all(api, **params) -> dict:
    return _get(api, "/api/intel/products", **params)


def _line(pid: str):
    key = expect.template_line(pid)
    return expect.product_lines()[key] if key else None


# ── Auth ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/intel/facets", "/api/intel/products", f"/api/intel/products/{FERRIC}",
    f"/api/intel/products/{FERRIC}/market",
])
def test_every_endpoint_needs_a_signed_in_user(client, path):
    assert client.get(path).status_code == 401


def test_any_signed_in_user_reads_it_without_a_team_id(api):
    # Platform reference: no team_id parameter, no team permission check.
    assert api.get("/api/intel/facets").status_code == 200


# ── Facets ───────────────────────────────────────────────────────────────────

def test_default_facets_count_the_listed_cards_and_the_default_view(api, db):
    body = _get(api, "/api/intel/facets")
    listed, default = expect.listed_codes(), expect.default_view_codes()
    counts = body["counts"]
    assert counts["listed"] == len(listed) == counts["products"]
    assert counts["default_view"] == len(default)
    assert counts["lines"] == len(expect.lines_with_listed())
    assert counts["groups"] == sum(1 for p in listed if expect.classify(p).kind == "group")
    assert body["status_filter"] is None
    # Status checkboxes: every status, its badge words, its listed count.
    by_status = Counter(expect.classify(p).supply_status for p in listed)
    assert [(s["code"], s["label"], s["count"]) for s in body["statuses"]] == [
        (code, STATUS_BADGES[code][0], by_status.get(code, 0)) for code in STATUS_RANK]
    assert sum(f["count"] for f in body["families"]) == len(listed)
    assert counts["families"] == len(body["families"])
    # Every industry of the demand tree, zero counts included.
    assert sorted(i["name"] for i in body["industries"]) == sorted(expect.industries())
    assert "tiers" not in body and "demo_grade" not in counts
    buckets = {n for (n,) in db.execute(text("SELECT name FROM producers WHERE is_bucket"))}
    assert not buckets & {s["name"] for s in body["suppliers"]}
    assert len(body["suppliers"]) <= ic.SUPPLIER_FACET_LIMIT
    assert [s["count"] for s in body["suppliers"]] == sorted(
        (s["count"] for s in body["suppliers"]), reverse=True)
    assert body["data_version"]["source_commit"] == expect.source_commit()


def test_facets_follow_the_status_filter(api):
    default = expect.default_view_codes()
    body = _get(api, "/api/intel/facets", status=DEFAULT_VIEW)
    assert body["status_filter"] == ["live", "supply_exception"]
    assert body["counts"]["products"] == len(default)
    assert sum(f["count"] for f in body["families"]) == len(default)
    # The status checkboxes and the header totals never follow the filter.
    assert body["counts"]["listed"] == len(expect.listed_codes())
    assert body["counts"]["default_view"] == len(default)
    everything = _get(api, "/api/intel/facets", status="all")
    assert everything["counts"]["products"] == len(expect.listed_codes())


# ── The list ─────────────────────────────────────────────────────────────────

def test_status_all_lists_every_listed_card_and_absent_means_the_same(api):
    listed = set(expect.listed_codes())
    for params in ({"status": "all"}, {}):
        body = _all(api, **params)
        assert body["total"] == len(listed) == len(body["items"]), params
        assert body["limit"] is None and body["offset"] == 0
        assert {i["pid"] for i in body["items"]} == listed


def test_the_default_view_is_verified_and_concentrated_supply(api):
    body = _all(api, status=DEFAULT_VIEW)
    assert {i["pid"] for i in body["items"]} == set(expect.default_view_codes())
    assert body["total"] == len(body["items"])
    assert {i["status"]["code"] for i in body["items"]} <= {"live", "supply_exception"}


def test_without_a_limit_the_grid_gets_every_card_and_limit_still_pages(api):
    """T7: no 600 / 1,000 truncation. The page sends no limit; scripts may."""
    everything = _all(api)["items"]
    page = _all(api, limit=10, offset=20)
    assert page["total"] == len(everything) and page["limit"] == 10 and page["offset"] == 20
    assert [i["pid"] for i in page["items"]] == [i["pid"] for i in everything[20:30]]
    tail = _all(api, offset=len(everything) - 3)
    assert [i["pid"] for i in tail["items"]] == [i["pid"] for i in everything[-3:]]


def test_unlisted_cards_stay_out_of_the_grid_but_answer_by_id(api):
    statuses = expect.card_statuses()
    unlisted = [p for p in expect.template_codes() if not expect.is_listed(p)]
    kinds = Counter(statuses[p].kind for p in unlisted)
    assert {"absorbed", "pointer", "duplicate"} <= set(kinds)
    grid = {i["pid"] for i in _all(api)["items"]}
    assert not grid & set(unlisted)
    # One of each kind, plus a product with no formula, still opens.
    picks = {statuses[p].kind: p for p in sorted(unlisted)}
    no_formula = [p for p in unlisted if statuses[p].kind == "product"]
    for pid in [*picks.values(), *no_formula[:1]]:
        assert api.get(f"/api/intel/products/{pid}").status_code == 200, pid


def test_each_card_carries_its_kind_status_and_taxonomy(api):
    items = _all(api)["items"]
    for item in items:
        pid, cs = item["pid"], expect.classify(item["pid"])
        assert item["kind"] == cs.kind, pid
        assert item["status"]["code"] == cs.supply_status, pid
        assert item["status"]["label"] == STATUS_BADGES[cs.supply_status][0], pid
        assert set(item["status"]) == {"code", "label", "tone"}, pid
        assert (item["family"] or {}).get("name") == expect.template_family(pid), pid
        line = _line(pid)
        assert (item["line"] or {}).get("name") == (line.name if line else None), pid
        assert (item["subfamily"] or {}).get("name") == (line.subfamily if line else None), pid
        # Every listed card is priced, groups through a member's recipe.
        assert item["current_index"] is not None and len(item["sparkline"]) == 12, pid
        assert "tier" not in item and "is_demo_grade" not in item and "line_key" not in item
    groups = [i for i in items if i["is_group"]]
    assert groups and all(g["member_count"] >= 2 and g["kind"] == "group" for g in groups)


def test_the_grid_is_ordered_by_family_then_status_then_name(api):
    items = _all(api)["items"]
    keys = [((i["family"]["name"] if i["family"] else "~").casefold(),
             STATUS_RANK[i["status"]["code"]], i["name"].casefold(), i["pid"]) for i in items]
    assert keys == sorted(keys)


def test_the_ferric_chloride_card(api):
    items = _all(api, q=FERRIC)["items"]
    card = next(i for i in items if i["pid"] == FERRIC)
    y, m = expect.last_actual_month()
    today = expect.should_cost_index(FERRIC, "EU", y, m)
    assert card["current_index"] == pytest.approx(today, abs=0.05)
    assert card["sparkline"][-1] == pytest.approx(today, abs=0.05)
    assert card["trend_pct"] == pytest.approx(today - 100, abs=0.05)
    assert card["sparkline_region"] == "EU"
    assert card["sparkline_to"] == f"{y:04d}-{m:02d}"
    heaviest = sorted((ln for ln in expect.combo_lines(FERRIC, "EU") if ln[3] != "margin"),
                      key=lambda ln: -ln[0])
    assert card["top_lines"] == [ln[1] for ln in heaviest[:2]]
    assert set(card["regions"]) == {c["region"] for c in expect.combos(FERRIC)}


@pytest.mark.parametrize("axis", ["family_id", "subfamily_id", "line_id"])
def test_filters_by_id_narrow_the_grid(api, axis):
    ferric = next(i for i in _all(api, q=FERRIC)["items"] if i["pid"] == FERRIC)
    ref = {"family_id": "family", "subfamily_id": "subfamily", "line_id": "line"}[axis]
    wanted = ferric[ref]["id"]
    body = _all(api, **{axis: wanted})
    assert FERRIC in {i["pid"] for i in body["items"]}
    assert 0 < body["total"] < len(expect.listed_codes())
    assert all((i[ref] or {}).get("id") == wanted for i in body["items"])


@pytest.mark.parametrize("params,check", [
    ({"has_report": "true"}, lambda i: i["has_report"]),
    ({"has_report": "false"}, lambda i: not i["has_report"]),
    ({"region": "GL"}, lambda i: "GL" in i["regions"]),
    ({"trend": "up"}, lambda i: i["trend_pct"] > 2),
    ({"trend": "down"}, lambda i: i["trend_pct"] < -2),
    ({"status": "supply_pending"}, lambda i: i["status"]["code"] == "supply_pending"),
])
def test_filters_narrow_the_grid(api, params, check):
    body = _all(api, **params)
    assert 0 < body["total"] < len(expect.listed_codes())
    assert all(check(i) for i in body["items"])


def test_has_report_matches_the_report_joins_and_the_facet(api, db):
    with_report = {lid for (lid,) in db.execute(text(
        "SELECT DISTINCT product_line_id FROM market_report_lines WHERE product_line_id IS NOT NULL"))}
    items = _all(api)["items"]
    assert all(i["has_report"] == bool(i["line"] and i["line"]["id"] in with_report) for i in items)
    facets = _get(api, "/api/intel/facets")
    assert _all(api, has_report="true")["total"] == facets["counts"]["has_report"]


def test_ferric_chloride_is_found_by_every_axis(api, db):
    industry, fn = db.execute(text(
        "SELECT i.name, p.fn FROM category_placements p JOIN industries i ON i.id = p.industry_id "
        "WHERE p.pid = :c AND p.fn IS NOT NULL ORDER BY i.name LIMIT 1"), {"c": FERRIC}).one()
    maker = db.execute(text(
        "SELECT pr.name FROM producer_formulas pf JOIN producers pr ON pr.id = pf.producer_id "
        "WHERE pf.subject_code = :c AND pf.counts_toward_floor AND NOT pr.is_bucket "
        "ORDER BY pf.row_order LIMIT 1"), {"c": FERRIC}).scalar()
    cas = expect.raw("CAS_LOOKUP")[FERRIC]
    for params in ({"industry": industry}, {"fn": fn}, {"supplier": maker}, {"q": cas},
                   {"q": FERRIC.lower()}):
        body = _all(api, **params)
        assert FERRIC in {i["pid"] for i in body["items"]}, params
        assert body["total"] < len(expect.listed_codes()), params


def test_the_supplier_facet_and_filter_count_only_counting_makers_on_listed_cards(api, db):
    """A producer whose links on a card do not count toward the floor does
    not find that card through `?supplier=`."""
    row = db.execute(text(
        "SELECT pr.name, pf.subject_code FROM producer_formulas pf "
        "JOIN producers pr ON pr.id = pf.producer_id AND NOT pr.is_bucket "
        "WHERE NOT pf.counts_toward_floor ORDER BY pf.subject_code, pr.name")).all()
    listed = set(expect.listed_codes())
    pick = next(((n, c) for n, c in row if c in listed), None)
    assert pick is not None, "no non-counting link on a listed card"
    name, code = pick
    assert code not in {i["pid"] for i in _all(api, supplier=name)["items"]}
    # Every supplier in the facet has a counting link on a listed card.
    counting = {(n, c) for n, c in db.execute(text(
        "SELECT pr.name, pf.subject_code FROM producer_formulas pf "
        "JOIN producers pr ON pr.id = pf.producer_id WHERE pf.counts_toward_floor"))}
    for s in _get(api, "/api/intel/facets")["suppliers"]:
        assert any(n == s["name"] and c in listed for n, c in counting), s["name"]


def test_bad_filter_values_are_422(api):
    assert api.get("/api/intel/products", params={"trend": "sideways"}).status_code == 422
    assert api.get("/api/intel/products", params={"limit": 0}).status_code == 422
    assert api.get("/api/intel/products", params={"limit": 5001}).status_code == 422
    assert api.get("/api/intel/products", params={"status": "live,nope"}).status_code == 422
    assert api.get("/api/intel/facets", params={"status": "nope"}).status_code == 422


def test_an_unmatched_filter_is_an_empty_list_not_an_error(api):
    body = _all(api, q="no-such-product-xyz")
    assert body == {"total": 0, "limit": None, "offset": 0, "items": []}


# ── Detail ───────────────────────────────────────────────────────────────────

def test_the_ferric_chloride_page(api, db):
    d = _get(api, f"/api/intel/products/{FERRIC}")
    record = expect.raw("FORMULA_COMBOS")[FERRIC]
    cs = expect.classify(FERRIC)
    line = _line(FERRIC)
    assert d["pid"] == FERRIC and d["redirected_from"] is None
    assert d["name"] == record["name"]
    assert d["cas"] == expect.raw("CAS_LOOKUP")[FERRIC]
    assert d["kind"] == cs.kind and d["status"]["code"] == cs.supply_status
    assert d["status"]["description"] == STATUS_BADGES[cs.supply_status][2]
    assert d["listed"] is True and d["in_default_view"] == (FERRIC in expect.default_view_codes())
    assert d["family"]["name"] == line.family and d["subfamily"]["name"] == line.subfamily
    assert d["line"]["name"] == line.name == d["line_label"]
    assert d["line"]["platform"] == line.platform
    assert d["regions"] == list(dict.fromkeys(c["region"] for c in record["combos"]))
    assert d["region_info"][0]["app_region"] == "Europe"
    synth = expect.raw("SYNTHESIS_ROUTES").get(FERRIC)
    if synth:
        assert d["synthesis"]["reaction"] == synth.get("reaction")
    lines = expect.combo_lines(FERRIC, "EU")
    assert [l["weight_pct"] for l in d["cost_formula"]["lines"]] == \
        [ln[0] for ln in lines if ln[3] != "margin"]
    assert d["cost_formula"]["margin_pct"] == next(ln[0] for ln in lines if ln[3] == "margin")
    slugs = {s for (s,) in db.execute(text(
        "SELECT slug FROM market_report_lines WHERE product_line_id = :l"), {"l": d["line"]["id"]})}
    assert {r["slug"] for r in d["reports"]} == slugs
    assert d["line"]["has_report"] == bool(slugs)
    assert all(re.fullmatch(r"\d{4}-\d{2}", r["as_of"]) for r in d["reports"] if r["as_of"])
    placed = {c for (c,) in db.execute(text(
        "SELECT c.code FROM category_placements p JOIN categories c ON c.id = p.category_id "
        "WHERE p.pid = :c"), {"c": FERRIC})}
    assert {p["category_code"] for p in d["placements"]} == placed
    assert all(s["tag"] for s in d["index_sources"] if s["indexed"])
    for gone in ("grade_source", "refresh", "supply_split", "demand_split", "tier",
                 "is_line_only", "is_demo_grade", "visible_in_catalogue"):
        assert gone not in d, gone


def test_suppliers_come_from_producer_formulas_in_authored_order(api):
    pid = expect.leak_probe_card()
    rows = expect.supplier_rows(pid)
    d = _get(api, f"/api/intel/products/{pid}")
    sup = d["suppliers"]
    assert sup, pid
    orders = [s["row_order"] for s in sup]
    assert orders == sorted(orders) and len(set(orders)) == len(orders)
    for s in sup:
        row = rows[s["row_order"]]
        assert s["name"] == row.raw_name
        assert s["evidence"] == {"code": row.evidence_label,
                                 "label": EVIDENCE_LABEL_TEXT[row.evidence_label]}
        assert s["counts"] == row.counts_toward_floor
        assert (s["origin_restriction"] is not None) == (row.origin_restriction is not None)
        assert s["display_rank"] == ic._display_rank(row.evidence_label, row.counts_toward_floor)
        assert not {"share", "share_pct", "share_disclosed", "role_changed",
                    "integration_why"} & set(s)
    # Every authored row whose name opens a makers row is there, in its place.
    shown = {s["name"] for s in sup}
    first_of = {}
    for r in rows:
        first_of.setdefault(r.raw_name, r.row_order)
    assert [s["row_order"] for s in sup] == sorted(first_of[n] for n in shown)


def test_a_pointer_or_duplicate_serves_its_target(api):
    redirects = expect.redirects()
    assert redirects
    statuses = expect.card_statuses()
    for pid, target in sorted(redirects.items()):
        kind = statuses[pid].kind
        d = _get(api, f"/api/intel/products/{pid}")
        assert d["pid"] == target and d["redirected_from"] == {"pid": pid, "kind": kind}, pid
        m = _get(api, f"/api/intel/products/{pid}/market")
        assert m["pid"] == target and m["redirected_from"] == {"pid": pid, "kind": kind}, pid
    # The target itself answers without a redirect.
    some = sorted(redirects.values())[0]
    assert _get(api, f"/api/intel/products/{some}")["redirected_from"] is None


def test_warnings_use_the_fixed_text_never_the_stored_why(api):
    records = expect.raw("FORMULA_COMBOS")
    gaps = sorted(p for p, r in records.items() if isinstance(r.get("pricing_gap"), dict))
    margins = sorted(p for p, r in records.items() if r.get("margin_status"))
    assert gaps and margins
    for pid in gaps:
        gap = records[pid]["pricing_gap"]
        d = _get(api, f"/api/intel/products/{pid}")
        want = {"kind": "pricing_gap", "text": ic.PRICING_GAP_TEXT.format(line=gap["line"])
                if gap.get("line") else ic.PRICING_GAP_TEXT_NO_LINE}
        assert want in d["warnings"], pid
        m = _get(api, f"/api/intel/products/{pid}/market")
        assert want in m["warnings"] and want in m["trust"]["warnings"], pid
        if gap.get("why"):
            assert gap["why"] not in str(d) and gap["why"] not in str(m), pid
    for pid in margins:
        d = _get(api, f"/api/intel/products/{pid}")
        assert {"kind": "margin_status", "text": ic.MARGIN_STATUS_TEXT} in d["warnings"], pid
    clean = next(p for p in expect.default_view_codes() if p not in gaps + margins
                 and expect.classify(p).kind == "product")
    assert _get(api, f"/api/intel/products/{clean}")["warnings"] == []


def test_a_card_whose_line_is_not_published_reads_the_fixed_text(api):
    off_axis = expect.off_axis_record_keys()
    assert off_axis
    for pid in sorted(off_axis):
        d = _get(api, f"/api/intel/products/{pid}")
        assert d["line"] is None and d["subfamily"] is None, pid
        assert d["line_label"] == ic.UNPUBLISHED_LINE_TEXT, pid
        assert d["reports"] == [] and d["report"] is None, pid
        assert d["family"]["name"] == expect.template_family(pid), pid
        for name in expect.unpublished_line_names():
            assert name not in str(d), pid


def test_bare_compliance_has_no_badge(api, db):
    code = db.execute(text(
        "SELECT b.subject_code FROM editorial_blocks b JOIN editorial_block_versions v "
        "ON v.id = b.current_version_id WHERE b.team_id IS NULL AND b.block_type = 'compliance' "
        "AND v.body_json::text LIKE '%\"bare\": true%' ORDER BY 1 LIMIT 1")).scalar()
    assert code is not None, "no bare compliance entry loaded"
    d = _get(api, f"/api/intel/products/{code}")
    bare = [c for c in d["compliance"] if c["bare"]]
    assert bare and all(c["flag"] is None and c["name"] for c in bare)


def test_applications_flagged_no_receiver_are_not_served(api):
    cards = expect.raw("CURATED_CONTENT")
    pid = next(p for p in expect.listed_codes()
               if any(isinstance(a, dict) and a.get("_no_receiver")
                      for a in (cards.get(p) or {}).get("applications") or [])
               and any(isinstance(a, dict) and not a.get("_no_receiver")
                       for a in (cards.get(p) or {}).get("applications") or []))
    authored = cards[pid]["applications"]
    kept = [a for a in authored if isinstance(a, dict) and not a.get("_no_receiver")]
    d = _get(api, f"/api/intel/products/{pid}")
    assert [a["industry"] for a in d["applications"]] == [a.get("industry") for a in kept]
    assert all(not k.startswith("_") for a in d["applications"] for k in a)


def test_the_endpoints_serve_the_stored_cleaned_text(api, db):
    note = db.execute(text(
        "SELECT v.body_text FROM editorial_blocks b JOIN editorial_block_versions v "
        "ON v.id = b.current_version_id WHERE b.team_id IS NULL AND b.subject_code = :c "
        "AND b.block_type = 'supplier_note'"), {"c": FERRIC}).scalar()
    d = _get(api, f"/api/intel/products/{FERRIC}")
    assert note and d["supplier_note"] == note.strip()
    assert not re.search(r"share\s*:\s*0", d["supplier_note"], re.IGNORECASE)
    m = _get(api, f"/api/intel/products/{FERRIC}/market")
    if m["outlook"]:
        assert m["outlook"]["text"] == d["current_events"]


def test_an_absorbed_variant_points_at_its_card(api, db):
    code, base = db.execute(text(
        "SELECT code, absorbed_into FROM formula_templates WHERE team_id IS NULL "
        "AND card_kind = 'absorbed' AND catalog_meta->>'absorbed_via' = 'VARIANT_OVERRIDES' "
        "ORDER BY code LIMIT 1")).one()
    d = _get(api, f"/api/intel/products/{code}")
    assert d["kind"] == "absorbed" and d["status"] is None and d["listed"] is False
    assert d["absorbed_into"] == base
    assert d["absorbed_into_card"]["via"] == "VARIANT_OVERRIDES"
    assert d["absorbed_into_card"]["exists"] is True
    rows = _get(api, f"/api/intel/products/{base}")["variants"]["rows"]
    assert code in {r["pid"] for r in rows}
    # A split between variants is a share value: never served.
    assert all("share" not in r for r in rows)


def test_a_group_lists_its_members_and_prices_from_a_member(api, db):
    groups = [p for p in expect.listed_codes() if expect.classify(p).kind == "group"]
    pid = groups[0]
    d = _get(api, f"/api/intel/products/{pid}")
    assert d["is_group"] is True and d["kind"] == "group"
    assert d["status"]["code"] == "not_audited" and d["in_default_view"] is False
    assert [m["pid"] for m in d["members"]] == d["group_members"]
    assert any(m["has_formula"] for m in d["members"])
    m = _get(api, f"/api/intel/products/{pid}/market")
    assert m["evaluable"] is True and m["source_pid"] in d["group_members"]
    atp = d["add_to_portfolio"]
    own = str(db.query(FormulaTemplate.id).filter(FormulaTemplate.team_id.is_(None),
                                                  FormulaTemplate.code == pid).scalar())
    # The group template has no cost lines: add from the member that prices it.
    assert d["template_id"] == own and atp["template_id"] != own


def test_a_card_with_no_formula_has_a_page_but_no_chart(api):
    statuses = expect.card_statuses()
    pid = next(p for p in expect.template_codes()
               if statuses[p].kind == "product" and not expect.has_coverage(p))
    d = _get(api, f"/api/intel/products/{pid}")
    assert d["has_formula"] is False and d["listed"] is False
    assert d["cost_formula"] is None and d["add_to_portfolio"] is None
    m = _get(api, f"/api/intel/products/{pid}/market")
    assert m["evaluable"] is False and m["reason"] and m["series_by_region"] == {}


def test_the_retracted_flag_is_not_a_line_badge(api, db):
    row = db.execute(text(
        "SELECT t.code FROM product_lines l JOIN formula_templates t ON t.product_line_id = l.id "
        "AND t.team_id IS NULL AND t.card_kind = 'product' "
        "WHERE l.flags ? 'retracted' ORDER BY t.code LIMIT 1")).scalar()
    assert row is not None, "no card on a line carrying the retracted key"
    d = _get(api, f"/api/intel/products/{row}")
    assert d["line"] is not None and "retracted" not in d["line"]["flags"]


def test_regions_accept_app_codes_and_any_case(api):
    for region in ("eu", "Europe"):
        body = _get(api, f"/api/intel/products/{FERRIC}/market", region=region)
        assert body["region"] == "EU" and body["app_region"] == "Europe"
    latam = _all(api, region="Latam")
    assert latam["total"] and all("LA" in i["regions"] for i in latam["items"])


def test_unknown_products_regions_and_variants_are_404(api):
    priced = {c["region"] for c in expect.combos(FERRIC)}
    other = next(r for r in ("LA", "GL", "IN", "MEA", "APAC") if r not in priced)
    assert api.get("/api/intel/products/NOPE-XYZ").status_code == 404
    assert api.get("/api/intel/products/NOPE-XYZ/market").status_code == 404
    assert api.get(f"/api/intel/products/{FERRIC}/market", params={"region": other}).status_code == 404
    assert api.get(f"/api/intel/products/{FERRIC}/market",
                   params={"region": "EU", "variant": "x"}).status_code == 404


# ── Add to portfolio ─────────────────────────────────────────────────────────
# The product page's button starts a team cost model the way the Cost Model
# Builder's catalogue link does: a team product with `formula_template_id`,
# then `/cost-models/new` with `state.productId`, where the builder resolves
# the template's recipe. So the contract pinned here is that resolve step.

def _template_id(db, code):
    return str(db.query(FormulaTemplate.id).filter(FormulaTemplate.team_id.is_(None),
                                                   FormulaTemplate.code == code).scalar())


def _resolved_lines(api, tenant, template_id, region):
    r = api.get(f"/api/formulas/{template_id}/resolve",
                params={"team_id": str(tenant["team_id"]), "region": region})
    assert r.status_code == 200, r.text
    return r.json()["lines"]


def test_a_product_page_carries_what_add_to_portfolio_needs(api, db, tenant_a):
    d = _get(api, f"/api/intel/products/{FERRIC}")
    tid = _template_id(db, FERRIC)
    assert d["template_id"] == tid
    assert d["add_to_portfolio"] == {
        "template_id": tid, "default_region": "Europe", "route": ic.ADD_TO_PORTFOLIO_ROUTE,
        "unit": ic.ADD_TO_PORTFOLIO_UNIT}
    # The builder gets the EU recipe, every cost line of it.
    assert len(_resolved_lines(api, tenant_a, tid, "Europe")) == len(expect.combo_lines(FERRIC, "EU"))


def test_a_card_priced_outside_europe_adds_in_its_own_region(api, tenant_a):
    code = next((i["pid"] for i in _all(api)["items"]
                 if "EU" not in i["regions"] and not i["is_group"]), None)
    assert code is not None, "every listed card is priced in EU"
    atp = _get(api, f"/api/intel/products/{code}")["add_to_portfolio"]
    assert atp["default_region"] != "Europe"
    assert _resolved_lines(api, tenant_a, atp["template_id"], atp["default_region"])


# ── The snapshot ─────────────────────────────────────────────────────────────

def test_the_snapshot_reads_the_templates_the_loader_writes():
    from app.services.content_drop.catalogue import SOURCES
    assert tuple(ic.CATALOGUE_SOURCES) == tuple(SOURCES)


def test_a_warm_snapshot_is_reused_and_a_new_version_rebuilds(content_loaded, db, monkeypatch):
    first = ic.get_catalogue(db)
    assert ic.get_catalogue(db) is first
    monkeypatch.setattr(ic, "load_version", lambda _db: first.version + "|moved")
    rebuilt = ic.get_catalogue(db)
    assert rebuilt is not first and rebuilt.version.endswith("|moved")
    assert len(rebuilt.items) == len(first.items)
    ic.invalidate()


def test_the_version_moves_with_platform_rows_and_ignores_team_rows(content_loaded, db, tenant_a):
    before = ic.load_version(db)
    assert ic.load_version(db) == before
    try:
        # A team's own template: not catalogue content, must not invalidate.
        db.add(FormulaTemplate(team_id=tenant_a["team_id"], created_by=tenant_a["user_id"],
                               name="team-only test template", code="TEAM-ONLY-TEST"))
        db.flush()
        assert ic.load_version(db) == before
        # A platform card touched: the key moves. Rolled back, never committed.
        db.execute(text("UPDATE formula_templates SET updated_at = now() + interval '1 day' "
                        "WHERE team_id IS NULL AND code = :c"), {"c": FERRIC})
        assert ic.load_version(db) != before
    finally:
        db.rollback()
    assert ic.load_version(db) == before
    try:
        # A new content load moves it too.
        db.execute(text("INSERT INTO content_loads (source_commit) VALUES ('test-only')"))
        assert ic.load_version(db) != before
    finally:
        db.rollback()
    assert ic.load_version(db) == before


def test_warm_builds_the_snapshot_and_never_raises(content_loaded, monkeypatch):
    def broken(_db):
        raise RuntimeError("made-up failure")

    monkeypatch.setattr(intel_warm, "SNAPSHOTS",
                        (("broken", broken), ("catalogue", intel_warm._catalogue)))
    ic.invalidate()
    out = intel_warm.warm()
    assert out["broken"] is None and out["catalogue"] is not None
    assert ic._cache is not None and len(ic._cache.items) == len(expect.listed_codes())


# ── Pure: status parsing, warnings, makers rank, line flags ──────────────────

@pytest.mark.parametrize("value, parsed", [
    (None, None), ("", None), ("all", None), ("ALL", None),
    ("live,supply_exception,supply_pending,not_audited", None),
    ("live", frozenset({"live"})),
    (" live , supply_exception ", frozenset({"live", "supply_exception"})),
])
def test_status_values_parse(value, parsed):
    assert ic.parse_statuses(value) == parsed


def test_an_unknown_status_is_refused():
    with pytest.raises(ValueError):
        ic.parse_statuses("live,verified")


def _made_up(meta: dict) -> ic.Product:
    return ic.Product(id=None, code="TEST-PID", name="Test", full_name=None, form=None, cas=None,
                      volatile=None, reference_grade=None, kind="product", supply_status="live",
                      redirect_to=None, family_id=None, family=None, line=None, is_group=False,
                      absorbed_into=None, group_members=[], catalog_meta=meta)


def test_warnings_are_built_from_structure_only():
    gap = _made_up({"pricing_gap": {"status": "x", "line": "Made-up line", "since": "2000-01-01"}})
    assert ic.warnings_for(gap) == [{"kind": "pricing_gap",
                                     "text": ic.PRICING_GAP_TEXT.format(line="Made-up line")}]
    assert ic.warnings_for(_made_up({"pricing_gap": {}})) == [
        {"kind": "pricing_gap", "text": ic.PRICING_GAP_TEXT_NO_LINE}]
    both = _made_up({"pricing_gap": {"line": "L"}, "margin_status": "to re-estimate"})
    assert [w["kind"] for w in ic.warnings_for(both, both)] == ["pricing_gap", "margin_status"]
    assert ic.warnings_for(_made_up({})) == []


@pytest.mark.parametrize("label, counts, rank", [
    ("verified", True, 0), ("not_audited", True, 1), ("not_audited", False, 1),
    ("verified", False, 2), ("weak", False, 2), ("unverified", False, 2),
    ("family_only", False, 2), ("not_counted", False, 2), ("distributor", False, 3),
])
def test_the_makers_screen_rank(label, counts, rank):
    assert ic._display_rank(label, counts) == rank


def test_line_flags_are_codes_not_prose():
    assert ic.line_flags(None) == []
    assert ic.line_flags({"status": "made-up; validation pending", "route_flag": "prose",
                          "retracted": "a naming rule"}) == ["pending"]
    assert ic.line_flags({"do_not_publish": True}) == ["do_not_publish"]


# ── Display clean-ups (content_drop/sanitize.py, applied again on read) ──────

# Made-up notes, one per branch of the cleaner. Drop text never goes in a test.
@pytest.mark.parametrize("raw, shown", [
    # the common ending, every quote style
    ('Maker A leads. Test shares are not published; '
     'treat share:0 as “not disclosed.”',
     'Maker A leads. Test shares are not published.'),
    ('Test shares are not published; treat share:0 as "not disclosed".',
     'Test shares are not published.'),
    ("Test shares are not published; treat share:0 as 'not disclosed.'",
     'Test shares are not published.'),
    ('Test shares are not published; treat share:0 as not disclosed.',
     'Test shares are not published.'),
    # other connectors
    ('Test shares are not published - treat share:0 as "not disclosed."',
     'Test shares are not published.'),
    ('Test capacities are estimates — treat share:0 as "not disclosed."',
     'Test capacities are estimates.'),
    ('No test maker publishes a share, so treat share:0 as "not disclosed."',
     'No test maker publishes a share.'),
    ('Nothing is published for this test, and share:0 means not disclosed. Maker B trades.',
     'Nothing is published for this test. Maker B trades.'),
    # a sentence of its own, at the start, in the middle, at the end
    ('share:0 = not disclosed. Maker C sets the test benchmark.',
     'Maker C sets the test benchmark.'),
    ('A test minority. share:0 = not disclosed. A test switching cost.',
     'A test minority. A test switching cost.'),
    ('Test positioning. Treat share:0 as "not disclosed," not "zero volume."',
     'Test positioning.'),
    # tails that belong to the instruction
    ('Not published in this test; treat share:0 as "not disclosed", not as zero.',
     'Not published in this test.'),
    ('Test figures past the top two are not published; treat share:0 as “not disclosed” for those entries.',
     'Test figures past the top two are not published.'),
    ('Test estimates; treat share:0 as “not disclosed” in the structured field whatever '
     'the note says.',
     'Test estimates.'),
    ("Test makers publish nothing, so treat share:0 as 'not disclosed' throughout. Maker D leads.",
     'Test makers publish nothing. Maker D leads.'),
    ('Test makers publish no figures, so share:0 stands for all of them. Maker E competes.',
     'Test makers publish no figures. Maker E competes.'),
    # a continuation becomes its own sentence
    ('Test shares are not published; treat share:0 as "not disclosed", and note that this is made up.',
     'Test shares are not published. Note that this is made up.'),
    # nothing to strip: unchanged, including other uses of "share"
    ('Maker F holds a 30% share: the leader.', 'Maker F holds a 30% share: the leader.'),
    (None, None),
])
def test_the_share0_instruction_is_removed_from_supplier_notes(raw, shown):
    assert sanitize.clean_supplier_note(raw) == shown
    # Idempotent: the API applies it again to the stored (already clean) text.
    assert sanitize.clean_supplier_note(shown) == shown


@pytest.mark.parametrize("raw, shown", [
    ("the sibling PID on this test line is listed",
     "the related product on this test line is listed"),
    ("unlike its sibling PIDs", "unlike its related products"),
    ("a sibling line in this pack", "a sibling line in this pack"),
    (None, None),
])
def test_sibling_pid_reads_related_product(raw, shown):
    assert sanitize.clean_current_events(raw) == shown


def _stored(db, block_type):
    return db.execute(text(
        "SELECT b.subject_code, v.body_text FROM editorial_blocks b "
        "JOIN editorial_block_versions v ON v.id = b.current_version_id "
        "WHERE b.team_id IS NULL AND b.subject_type = 'formula' AND b.block_type = :t "
        "AND v.body_text IS NOT NULL"), {"t": block_type}).all()


def test_no_stored_supplier_note_or_current_events_needs_cleaning(content_loaded, db):
    """The loader stores the served form; the API's second pass changes nothing."""
    share0 = re.compile(r"share\s*:\s*0", re.IGNORECASE)
    notes = _stored(db, "supplier_note")
    assert notes, "no supplier notes loaded"
    for code, raw in notes:
        assert not share0.search(raw), code
        assert sanitize.clean_supplier_note(raw) == raw.strip(), code
    for code, raw in _stored(db, "current_events"):
        assert "sibling PID" not in raw, code
