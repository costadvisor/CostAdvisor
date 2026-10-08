"""The supply taxonomy API (routers/taxonomy.py, services/taxonomy.py).

Runs on a test database holding a content load of the current drop
(`content_loaded`). Every expected number and name comes from the drop files
at run time (`tests/content_drop_expect.py`); no drop text lives here.

What is pinned (design §4.2, WP-7):

* auth: 401 without a session;
* the tree totals equal the drop: families, sub-families (every axis node,
  the unnamed ones and those with no line included), product lines;
* each line sits under its own family and sub-family, in the axis order;
* card counts per line equal the drop's listed and default-view sets, and the
  cards with no line are counted as `unplaced`;
* the families list and the line picker (filters, former names, limit);
* the team taxonomy fork endpoints are gone;
* the deliberately unnamed line names never appear.
"""
from __future__ import annotations

import json

import pytest

from app.services import intel_reference as ref
from app.services import taxonomy as svc
from tests import content_drop_expect as expect

pytestmark = pytest.mark.usefixtures("content_loaded")


@pytest.fixture
def api(user_factory, client_as):
    return client_as(user_factory())


@pytest.fixture(scope="module")
def tree_expect():
    listed = set(expect.listed_codes())
    default = set(expect.default_view_codes())
    per_line: dict[str, list[str]] = {}
    for pid in listed:
        key = expect.template_line(pid)
        if key:
            per_line.setdefault(key, []).append(pid)
    lines = expect.product_lines()
    return {
        "listed": listed, "default": default, "per_line": per_line, "lines": lines,
        "by_names": {(ln.family, ln.subfamily, ln.name): key for key, ln in lines.items()},
        "unplaced": {p for p in listed if expect.template_line(p) is None},
    }


def _no_unpublished_names(payload, where: str) -> None:
    blob = json.dumps(payload, ensure_ascii=False)
    for i, name in enumerate(expect.unpublished_line_names()):
        assert name not in blob, f"{where}: holds unpublished line name #{i}"


def test_endpoints_require_a_session(client):
    for url in ("/api/taxonomy", "/api/taxonomy/families", "/api/taxonomy/lines"):
        assert client.get(url).status_code == 401, url


def test_tree_totals_equal_the_drop(api):
    t = api.get("/api/taxonomy").json()
    _no_unpublished_names(t, "/api/taxonomy")
    fams = t["families"]
    assert [f["name"] for f in fams] == expect.families()
    subs = [s for f in fams for s in f["subfamilies"]]
    assert len(subs) == len(expect.subfamilies())
    assert sorted((f["name"], s["name"] or "") for f in fams for s in f["subfamilies"]) == sorted(
        (fam, name or "") for fam, name in expect.subfamilies())
    assert sum(1 for s in subs if s["name"] is None) == sum(
        1 for _f, n in expect.subfamilies() if n is None)
    assert sum(1 for s in subs if not s["lines"]) == len(
        expect.subfamilies_without_lines(named_only=True))
    lines = [ln for s in subs for ln in s["lines"]]
    assert len(lines) == len(expect.product_lines())
    assert t["counts"]["families"] == len(fams)
    assert t["counts"]["subfamilies"] == len(subs)
    assert t["counts"]["lines"] == len(lines)
    for f in fams:
        assert f["counts"]["subfamilies"] == len(f["subfamilies"])
        assert f["counts"]["lines"] == sum(len(s["lines"]) for s in f["subfamilies"])
        assert f["counts"]["listed"] == sum(s["counts"]["listed"] for s in f["subfamilies"])
        for s in f["subfamilies"]:
            assert s["counts"]["lines"] == len(s["lines"])
            assert s["counts"]["listed"] == sum(ln["counts"]["listed"] for ln in s["lines"])
            assert s["counts"]["default_view"] == sum(
                ln["counts"]["default_view"] for ln in s["lines"])


def test_each_line_sits_on_its_family_and_subfamily_with_drop_counts(api, tree_expect):
    t = api.get("/api/taxonomy").json()
    seen = set()
    for f in t["families"]:
        for s in f["subfamilies"]:
            for ln in s["lines"]:
                key = tree_expect["by_names"].get((f["name"], s["name"], ln["name"]))
                assert key, (f["id"], s["id"], ln["id"])
                seen.add(key)
                assert ln["platform"] == tree_expect["lines"][key].platform
                cards = set(tree_expect["per_line"].get(key, []))
                assert ln["counts"]["listed"] == len(cards)
                assert ln["counts"]["default_view"] == len(cards & tree_expect["default"])
                assert all(fl.keys() == {"code", "label"} for fl in ln["flags"])
    assert seen == set(tree_expect["lines"])


def test_tree_card_counts_and_unplaced(api, tree_expect):
    t = api.get("/api/taxonomy").json()
    assert t["unplaced"]["listed"] == len(tree_expect["unplaced"])
    assert t["unplaced"]["default_view"] == len(tree_expect["unplaced"] & tree_expect["default"])
    assert t["counts"]["listed"] == len(tree_expect["listed"])
    assert t["counts"]["default_view"] == len(tree_expect["default"])
    assert t["counts"]["listed"] == sum(f["counts"]["listed"] for f in t["families"]) + \
        t["unplaced"]["listed"]


def test_tree_agrees_with_the_lines_api(api):
    t = api.get("/api/taxonomy").json()
    tree_lines = {ln["id"]: ln for f in t["families"] for s in f["subfamilies"]
                  for ln in s["lines"]}
    items = {it["id"]: it for it in api.get("/api/intel/lines").json()["items"]}
    assert set(tree_lines) == set(items)
    for lid, ln in tree_lines.items():
        assert ln["has_report"] == items[lid]["has_report"]
        assert ln["counts"] == items[lid]["counts"]
        assert ln["in_v1_scope"] == items[lid]["in_v1_scope"]
        assert ln["flags"] == items[lid]["flags"]


def test_tree_is_cached_on_the_snapshot(db):
    first = svc.get_tree(db)
    assert svc.get_tree(db) is first
    ref.reset_cache()
    assert svc.get_tree(db) is not first


def test_families_list(api):
    t = api.get("/api/taxonomy").json()
    d = api.get("/api/taxonomy/families").json()
    assert d["total"] == len(d["items"]) == len(expect.families())
    names = [f["name"] for f in d["items"]]
    assert names == sorted(names, key=str.lower)
    by_id = {f["id"]: f for f in t["families"]}
    for f in d["items"]:
        assert f["counts"] == by_id[f["id"]]["counts"]


def test_line_picker(api, tree_expect):
    full = api.get("/api/taxonomy/lines").json()
    _no_unpublished_names(full, "/api/taxonomy/lines")
    assert full["total"] == len(full["items"]) == len(tree_expect["lines"])
    assert {it["line_key"] for it in full["items"]} == set(tree_expect["lines"])
    order = [(it["family"]["name"].lower(), it["subfamily"] is None,
              ((it["subfamily"] or {}).get("name") or "").lower(), it["name"].lower())
             for it in full["items"]]
    assert order == sorted(order)

    some = full["items"][0]
    fam = api.get("/api/taxonomy/lines", params={"family_id": some["family"]["id"]}).json()
    assert fam["total"] and all(it["family"]["id"] == some["family"]["id"] for it in fam["items"])
    sub = next(it["subfamily"] for it in full["items"] if it["subfamily"])
    by_sub = api.get("/api/taxonomy/lines", params={"subfamily_id": sub["id"]}).json()
    assert by_sub["total"] and all(it["subfamily"]["id"] == sub["id"] for it in by_sub["items"])

    named = api.get("/api/taxonomy/lines", params={"q": some["name"].upper()}).json()
    assert some["id"] in {it["id"] for it in named["items"]}

    # A former name finds the line it now is.
    renamed = next((it for it in full["items"] if it["former_names"]), None)
    assert renamed, "the drop has no renamed line"
    old = api.get("/api/taxonomy/lines", params={"q": renamed["former_names"][0]}).json()
    assert renamed["id"] in {it["id"] for it in old["items"]}

    two = api.get("/api/taxonomy/lines", params={"limit": 2}).json()
    assert two["total"] == full["total"] and len(two["items"]) == 2
    assert api.get("/api/taxonomy/lines", params={"limit": 0}).status_code == 422


def test_team_taxonomy_forks_are_gone(api):
    for url in ("/api/chemical-families", "/api/subfamilies"):
        assert api.get(url).status_code == 404, url
