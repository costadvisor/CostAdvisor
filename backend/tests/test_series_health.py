"""Series health (Jira 208): one state per series, with the reason.

The states as tests:

* a recent actual value is `current`, an old one `stale`, with the age;
* the quarterly store counts — a quarterly-only series is not "no history"
  (the gap `resolution._code_blocker` has, reading monthly only);
* with both stores, the newer observation wins;
* forecast rows alone are `forecast_only`, not current;
* a series with nothing is `empty` and the reason says why;
* a composite is `composite`, never `empty`, though nothing is stored for it.

Every test builds its own series (no `commodity_key`: that is the drop's
namespace) and ages it against a fixed `as_of`, so nothing depends on the
loaded catalog or on today's date.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.index_data import CommodityIndex, IndexValue
from app.models.index_layer import IndexMonthlyValue
from app.services.series_health import (
    COMPOSITE, CURRENT, EMPTY, FORECAST_ONLY, STALE, STALE_AFTER_MONTHS, STATES,
    STORE_BOTH, STORE_MONTHLY, STORE_QUARTERLY, series_health, series_health_bulk,
    state_counts,
)

AS_OF = date(2026, 11, 2)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _series(db, **kw) -> CommodityIndex:
    ci = CommodityIndex(name=f"sh-{uuid.uuid4().hex[:8]}", scrape_enabled=False, **kw)
    db.add(ci)
    db.commit()
    return ci


def _months(db, series, points):
    """`points` is [(year, month, value, kind), ...]."""
    for year, month, value, kind in points:
        db.add(IndexMonthlyValue(
            commodity_id=series.id, year=year, month=month, value=value, kind=kind,
        ))
    db.commit()


def _quarters(db, series, points, region="Europe"):
    """`points` is [(year, quarter, value), ...]."""
    for year, quarter, value in points:
        db.add(IndexValue(
            commodity_id=series.id, region=region, year=year, quarter=quarter,
            value=value, source="seed",
        ))
    db.commit()


def _cleanup(db, *series):
    # Roll back first: a failed test leaves the session unusable otherwise.
    db.rollback()
    bypass_rls_var.set(True)
    for s in series:
        db.execute(text("DELETE FROM index_monthly_values WHERE commodity_id = :i"), {"i": s.id})
        db.execute(text("DELETE FROM index_values WHERE commodity_id = :i"), {"i": s.id})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": s.id})
    db.commit()


# ── States ───────────────────────────────────────────────────────────────────

def test_a_recent_actual_value_is_current_with_its_age(db):
    s = _series(db)
    try:
        _months(db, s, [(2026, 8, 100, "actual"), (2026, 9, 101, "actual")])
        h = series_health(db, s.id, as_of=AS_OF)
        assert h.state == CURRENT
        assert h.store == STORE_MONTHLY
        assert h.last_actual == "2026-09"
        assert h.months_old == 2
        assert h.usable
    finally:
        _cleanup(db, s)


def test_an_old_actual_value_is_stale_and_says_how_old(db):
    s = _series(db)
    try:
        _months(db, s, [(2025, 6, 100, "actual")])
        h = series_health(db, s.id, as_of=AS_OF)
        assert h.state == STALE
        assert h.months_old == 17
        assert "2025-06" in h.reason and "17 months" in h.reason
        assert h.usable   # stale is still priceable, just flagged
    finally:
        _cleanup(db, s)


def test_the_threshold_is_inclusive_of_its_last_month(db):
    s = _series(db)
    try:
        # Exactly STALE_AFTER_MONTHS old: still current, one more month: stale.
        _months(db, s, [(2026, 5, 100, "actual")])
        assert series_health(db, s.id, as_of=AS_OF).months_old == STALE_AFTER_MONTHS
        assert series_health(db, s.id, as_of=AS_OF).state == CURRENT
        assert series_health(db, s.id, as_of=date(2026, 12, 1)).state == STALE
    finally:
        _cleanup(db, s)


def test_a_quarterly_only_series_has_history(db):
    """The Resolution panel reads monthly only and calls this 'no history',
    though costing prices it. Here it is a dated, usable series."""
    s = _series(db)
    try:
        _quarters(db, s, [(2026, 2, 50), (2026, 3, 52)])
        h = series_health(db, s.id, as_of=AS_OF)
        assert h.store == STORE_QUARTERLY
        assert h.last_actual == "2026-09"   # Q3 is observed through September
        assert h.state == CURRENT
        assert h.quarterly_points == 2
    finally:
        _cleanup(db, s)


def test_with_both_stores_the_newer_observation_wins(db):
    s = _series(db)
    try:
        _months(db, s, [(2026, 6, 100, "actual")])
        _quarters(db, s, [(2026, 3, 52)])
        h = series_health(db, s.id, as_of=AS_OF)
        assert h.store == STORE_BOTH
        assert h.last_actual == "2026-09"
    finally:
        _cleanup(db, s)


def test_forecast_rows_alone_are_not_current(db):
    s = _series(db)
    try:
        _months(db, s, [(2026, 12, 100, "forecast"), (2027, 1, 101, "forecast")])
        h = series_health(db, s.id, as_of=AS_OF)
        assert h.state == FORECAST_ONLY
        assert h.last_actual is None
        assert h.forecast_points == 2
        assert not h.usable
    finally:
        _cleanup(db, s)


def test_an_empty_series_is_explained(db):
    """The ticket's 'no unexplained empty indexes': the reason names what is
    missing, not just that something is."""
    s = _series(db)
    try:
        h = series_health(db, s.id, as_of=AS_OF)
        assert h.state == EMPTY
        assert h.store is None
        assert "no values" in h.reason
        assert "starter reference index" in h.reason   # no commodity_key
        assert "no provider" in h.reason
        assert not h.usable
    finally:
        _cleanup(db, s)


def test_a_composite_is_composite_not_empty(db):
    s = _series(db, composite_expression="A * 1.1", composite_variables={"A": 1})
    try:
        h = series_health(db, s.id, as_of=AS_OF)
        assert h.state == COMPOSITE
        assert h.usable
    finally:
        _cleanup(db, s)


def test_unknown_series_is_none(db):
    assert series_health(db, -1, as_of=AS_OF) is None


# ── Bulk + counts ────────────────────────────────────────────────────────────

def test_bulk_matches_single_reads_and_counts_every_state(db):
    current, stale, empty = _series(db), _series(db), _series(db)
    try:
        _months(db, current, [(2026, 9, 1, "actual")])
        _months(db, stale, [(2024, 1, 1, "actual")])
        ids = [current.id, stale.id, empty.id]
        rows = series_health_bulk(db, as_of=AS_OF, commodity_ids=ids)
        assert {r.commodity_id: r.state for r in rows} == {
            current.id: CURRENT, stale.id: STALE, empty.id: EMPTY,
        }
        for r in rows:
            assert r == series_health(db, r.commodity_id, as_of=AS_OF)
        counts = state_counts(rows)
        assert set(counts) == set(STATES)
        assert counts[CURRENT] == counts[STALE] == counts[EMPTY] == 1
        assert counts[FORECAST_ONLY] == counts[COMPOSITE] == 0
    finally:
        _cleanup(db, current, stale, empty)


# ── API ──────────────────────────────────────────────────────────────────────

def test_api_requires_authentication(client):
    assert client.get("/api/series-health").status_code == 401


def test_api_one_series_and_404(db, tenant_a, client_as):
    s = _series(db)
    try:
        _months(db, s, [(2025, 6, 100, "actual")])
        c = client_as(tenant_a)
        r = c.get(f"/api/series-health/{s.id}", params={"as_of": AS_OF.isoformat()})
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == STALE and body["months_old"] == 17
        assert c.get("/api/series-health/-1").status_code == 404
    finally:
        _cleanup(db, s)


def test_api_list_filters_by_state_but_counts_the_library(db, tenant_a, client_as):
    s = _series(db)
    try:
        c = client_as(tenant_a)
        r = c.get("/api/series-health", params={"state": EMPTY, "as_of": AS_OF.isoformat()})
        assert r.status_code == 200
        body = r.json()
        assert body["stale_after_months"] == STALE_AFTER_MONTHS
        assert set(body["counts"]) == set(STATES)
        assert all(row["state"] == EMPTY for row in body["series"])
        assert s.id in {row["commodity_id"] for row in body["series"]}
        assert sum(body["counts"].values()) >= len(body["series"])
        assert c.get("/api/series-health", params={"state": "nope"}).status_code == 422
    finally:
        _cleanup(db, s)
