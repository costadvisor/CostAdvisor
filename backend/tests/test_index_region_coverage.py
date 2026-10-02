"""Per-region sourcing coverage (Scrum 57 follow-up).

Synthetic series and cards throughout, never the loaded drop: a test that reads
the catalog passes or fails on whatever the last data load happened to contain,
and the point of these is the comparison rules.

`commodity_indexes` and `index_cards` are platform-level with no RLS and no
team to CASCADE from, so every fixture is torn down explicitly — a leaked
platform row becomes live data in the next test, which has bitten this repo
twice already.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexCard
from app.services.index_region_coverage import coverage, map_region


def _mk_series(db, key, **kw):
    ci = CommodityIndex(
        name=f"test-{key}", commodity_key=key, unit="t", scrape_enabled=False, **kw,
    )
    db.add(ci)
    db.flush()
    return ci


def _mk_card(db, ci, region, **kw):
    card = IndexCard(
        feed_key=f"{ci.commodity_key}|{region or 'none'}|{uuid.uuid4().hex[:6]}",
        feed_slug=ci.commodity_key, commodity_id=ci.id, region=region, **kw,
    )
    db.add(card)
    db.flush()
    return card


def _cleanup(db, keys):
    bypass_rls_var.set(True)
    for k in keys:
        db.execute(text(
            "DELETE FROM index_cards WHERE commodity_id IN "
            "(SELECT id FROM commodity_indexes WHERE commodity_key = :k)"), {"k": k})
        db.execute(text("DELETE FROM commodity_indexes WHERE commodity_key = :k"), {"k": k})
    db.commit()


def _entry(result, key):
    return next(e for e in result["entries"] if e["commodity_key"] == key)


# ── region mapping ────────────────────────────────────────────────────────

def test_region_mapping_covers_the_drop_vocabulary():
    assert map_region("EU") == "Europe"
    assert map_region("NA") == "NA"
    assert map_region("CN") == "China"
    # The drop spells the sentinel out where REGION_MAP keys it as GL. Aliased
    # locally rather than widening the shared map, which the resolver reads.
    assert map_region("Global") == "GLOBAL"
    assert map_region("GL") == "GLOBAL"


def test_multi_and_blank_are_not_regions_and_not_mapping_failures():
    # A card spanning several regions is not a region that failed to map.
    assert map_region("multi") is None
    assert map_region("") is None
    assert map_region(None) is None
    assert map_region("ZZ") is None, "a genuinely unknown code maps to nothing"


# ── comparison rules ──────────────────────────────────────────────────────

def test_a_silent_series_field_is_not_a_disagreement(db):
    """The rule that matters most on the real data: every drop-loaded series
    has a null access_tier, so comparing a card against it and calling that a
    conflict would report a phantom for almost every card in the library and
    bury the handful of real ones."""
    key = f"t-silent-{uuid.uuid4().hex[:8]}"
    try:
        ci = _mk_series(db, key)  # access_tier / frequency / provider all None
        _mk_card(db, ci, "EU", access="Free", frequency="Monthly", agency="Eurostat")
        db.commit()

        e = _entry(coverage(db), key)
        assert e["cards"][0]["disagrees_with_series"] == []
        assert set(e["series_silent_fields"]) == {"access_tier", "frequency", "provider"}
    finally:
        _cleanup(db, [key])


def test_a_stated_series_field_that_differs_is_a_disagreement(db):
    key = f"t-differ-{uuid.uuid4().hex[:8]}"
    try:
        ci = _mk_series(db, key, access_tier="Subscription", provider="ICIS")
        _mk_card(db, ci, "EU", access="Free", agency="Eurostat")
        db.commit()

        card = _entry(coverage(db), key)["cards"][0]
        assert set(card["disagrees_with_series"]) == {"access", "agency"}
        # frequency is silent on both sides, so it is neither.
        assert "frequency" not in card["disagrees_with_series"]
    finally:
        _cleanup(db, [key])


def test_case_and_whitespace_are_not_a_disagreement(db):
    key = f"t-case-{uuid.uuid4().hex[:8]}"
    try:
        ci = _mk_series(db, key, access_tier="Free")
        _mk_card(db, ci, "EU", access="  free ")
        db.commit()
        assert _entry(coverage(db), key)["cards"][0]["disagrees_with_series"] == []
    finally:
        _cleanup(db, [key])


def test_cards_disagreeing_with_each_other_is_its_own_finding(db):
    """Distinct from disagreeing with the series: whenever the series states a
    value at all it is itself one of the cards, so sibling conflict is what
    says the representative choice actually loses information."""
    key = f"t-sibling-{uuid.uuid4().hex[:8]}"
    try:
        ci = _mk_series(db, key)
        _mk_card(db, ci, "EU", access="Free", frequency="Quarterly")
        _mk_card(db, ci, "EU", access="Proxy", frequency="Quarterly")
        db.commit()

        e = _entry(coverage(db), key)
        assert e["sibling_conflicts"] == ["access"]
        assert "frequency" not in e["sibling_conflicts"], "agreeing cards are not a conflict"
    finally:
        _cleanup(db, [key])


def test_a_null_on_one_sibling_is_not_a_conflict(db):
    key = f"t-sibnull-{uuid.uuid4().hex[:8]}"
    try:
        ci = _mk_series(db, key)
        _mk_card(db, ci, "EU", access="Free")
        _mk_card(db, ci, "NA", access=None)
        db.commit()
        assert _entry(coverage(db), key)["sibling_conflicts"] == []
    finally:
        _cleanup(db, [key])


def test_duplicate_default_regions_are_flagged(db):
    """`is_default_region` is deliberately not unique — the shipped data has
    slugs carrying several defaults — so it has to be reported instead."""
    key = f"t-dupdef-{uuid.uuid4().hex[:8]}"
    try:
        ci = _mk_series(db, key)
        _mk_card(db, ci, "EU", is_default_region=True)
        _mk_card(db, ci, "NA", is_default_region=True)
        db.commit()

        e = _entry(coverage(db), key)
        assert e["duplicate_default_regions"] is True
        assert e["n_cards"] == 2
    finally:
        _cleanup(db, [key])


def test_one_default_region_is_not_flagged(db):
    key = f"t-onedef-{uuid.uuid4().hex[:8]}"
    try:
        ci = _mk_series(db, key)
        _mk_card(db, ci, "EU", is_default_region=True)
        _mk_card(db, ci, "NA", is_default_region=False)
        db.commit()
        assert _entry(coverage(db), key)["duplicate_default_regions"] is False
    finally:
        _cleanup(db, [key])


def test_span_and_unmapped_are_counted_differently(db):
    key_span = f"t-span-{uuid.uuid4().hex[:8]}"
    key_bad = f"t-bad-{uuid.uuid4().hex[:8]}"
    try:
        ci1 = _mk_series(db, key_span)
        _mk_card(db, ci1, "multi", access="Free")
        ci2 = _mk_series(db, key_bad)
        _mk_card(db, ci2, "ZZ", access="Free")
        db.commit()

        result = coverage(db)
        span_card = _entry(result, key_span)["cards"][0]
        assert span_card["mapped_region"] is None and span_card["region_is_span"] is True

        bad_card = _entry(result, key_bad)["cards"][0]
        assert bad_card["mapped_region"] is None and bad_card["region_is_span"] is False
        # Only the genuinely unknown code counts as unmapped; a span is not a
        # mapping failure.
        assert result["summary"]["cards_with_unmapped_region"] >= 1
    finally:
        _cleanup(db, [key_span, key_bad])


def test_a_series_with_no_cards_still_appears(db):
    """Absent is reportable. A series nothing describes must not silently drop
    out of a report about coverage."""
    key = f"t-nocards-{uuid.uuid4().hex[:8]}"
    try:
        _mk_series(db, key)
        db.commit()
        e = _entry(coverage(db), key)
        assert e["n_cards"] == 0 and e["cards"] == []
        assert e["sibling_conflicts"] == []
    finally:
        _cleanup(db, [key])


# ── API ───────────────────────────────────────────────────────────────────

def test_endpoint_returns_summary_and_entries(db, tenant_a, client_as):
    r = client_as(tenant_a).get("/api/indexes/region-coverage")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "summary" in body and "entries" in body
    # Platform metadata, so the caller is told it cannot build a per-region
    # trust chip from this — the cards carry no retrieval_status.
    assert body["summary"]["retrieval_status_is_series_level"] is True


def test_endpoint_requires_authentication(client):
    assert client.get("/api/indexes/region-coverage").status_code == 401
