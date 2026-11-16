"""Freshness by expected frequency (Jira 209 S4).

208 judged every series against one 6-month rule. A monthly series that has
missed three releases is late; an annual one that is 14 months old is not.
The threshold now comes from the series' stated frequency, and a content-drop
series is judged by the drop's own cadence:

* the drop's free-text frequencies are read by their leading word;
* monthly late after 2 months, quarterly after 6, annual after 15;
* a content-drop series is late after 6 whatever its frequency, unless the
  frequency allows longer (annual);
* no usable frequency keeps the 6-month default;
* the reason and the API both say which rule applied.

Same approach as test_series_health: each test builds its own series and ages
it against a fixed `as_of`.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.services.series_health import (
    CONTENT_DROP_STALE_AFTER_MONTHS, CURRENT, STALE, STALE_AFTER_MONTHS,
    frequency_interval_months, publication_lag, series_health,
)
from tests.test_series_health import _cleanup, _months, _quarters, _series

AS_OF = date(2026, 11, 2)


def _drop_series(db, **kw):
    # A commodity_key is what makes a series content-drop; unique per test.
    return _series(db, commodity_key=f"test-fresh-{uuid.uuid4().hex[:8]}", **kw)


@pytest.mark.parametrize("text,months", [
    ("Monthly", 1),
    ("Monthly (arithmetic mean of daily fixes)", 1),
    ("Daily", 1),
    ("Daily/Monthly", 1),
    ("Daily/Weekly news", 1),
    ("Weekly", 1),
    ("Quarterly", 3),
    ("Semi-annual", 6),
    ("Annual", 12),
    ("Annual, stepped monthly", 12),
    ("Unknown", None),
    ("", None),
    (None, None),
])
def test_free_text_frequencies_are_read_by_their_leading_word(text, months):
    assert frequency_interval_months(text) == months


def test_a_monthly_series_is_late_after_two_months(db):
    s = _series(db, frequency="Monthly")
    try:
        _months(db, s, [(2026, 9, 100, "actual")])          # 2 months old
        h = series_health(db, s.id, as_of=AS_OF)
        assert (h.state, h.stale_after_months, h.stale_rule) == (CURRENT, 2, "monthly")
        late = series_health(db, s.id, as_of=date(2026, 12, 1))   # 3 months old
        assert late.state == STALE
        assert "stale after 2: monthly" in late.reason
    finally:
        _cleanup(db, s)


def test_a_quarterly_series_is_late_after_two_quarters(db):
    s = _series(db, frequency="Quarterly")
    try:
        _quarters(db, s, [(2026, 2, 100)])                   # observed through 2026-06
        assert series_health(db, s.id, as_of=date(2026, 12, 1)).state == CURRENT   # 6 old
        assert series_health(db, s.id, as_of=date(2027, 1, 1)).state == STALE      # 7 old
    finally:
        _cleanup(db, s)


def test_an_annual_series_is_not_late_at_fourteen_months(db):
    # Under the single 208 rule this was stale.
    s = _series(db, frequency="Annual")
    try:
        _months(db, s, [(2025, 9, 100, "actual")])           # 14 months old
        h = series_health(db, s.id, as_of=AS_OF)
        assert h.state == CURRENT and h.stale_after_months == 15
        assert series_health(db, s.id, as_of=date(2027, 1, 1)).state == STALE      # 16 old
    finally:
        _cleanup(db, s)


def test_a_content_drop_series_is_judged_by_the_drop_not_its_frequency(db):
    # Monthly by frequency (late after 2), but it cannot be newer than the last
    # drop, so the drop's rule applies.
    s = _drop_series(db, frequency="Monthly")
    try:
        _months(db, s, [(2026, 6, 100, "actual")])           # 5 months old
        h = series_health(db, s.id, as_of=AS_OF)
        assert h.state == CURRENT
        assert (h.stale_after_months, h.stale_rule) == (CONTENT_DROP_STALE_AFTER_MONTHS, "content drop")
        assert series_health(db, s.id, as_of=date(2027, 1, 1)).state == STALE      # 7 old
    finally:
        _cleanup(db, s)


def test_an_annual_content_drop_series_keeps_its_longer_threshold(db):
    s = _drop_series(db, frequency="Annual")
    try:
        _months(db, s, [(2025, 12, 100, "actual")])          # 11 months old
        h = series_health(db, s.id, as_of=AS_OF)
        assert h.state == CURRENT
        assert (h.stale_after_months, h.stale_rule) == (15, "content drop, annual")
    finally:
        _cleanup(db, s)


def test_no_usable_frequency_keeps_the_default(db):
    s = _series(db, frequency="Unknown")
    try:
        _months(db, s, [(2026, 5, 100, "actual")])           # 6 months old
        h = series_health(db, s.id, as_of=AS_OF)
        assert (h.state, h.stale_after_months, h.stale_rule) == (CURRENT, STALE_AFTER_MONTHS, "default")
    finally:
        _cleanup(db, s)


def test_the_api_reports_each_series_threshold(db, tenant_a, client_as):
    s = _series(db, frequency="Monthly")
    try:
        _months(db, s, [(2026, 7, 100, "actual")])
        r = client_as(tenant_a).get(f"/api/series-health/{s.id}", params={"as_of": "2026-11-02"})
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == STALE
        assert body["stale_after_months"] == 2
        assert body["stale_rule"] == "monthly"
    finally:
        _cleanup(db, s)


# ── R1: each source's publication delay ──────────────────────────────────────
# Alexis's check of 10 Nov 2026: EU trade data, EIA and INEGI were at August,
# BLS / Census / Destatis / Japan customs at September, the World Bank at
# October. A slow source must not read as late when nothing failed.

R1_AS_OF = date(2026, 11, 10)


@pytest.mark.parametrize("provider,lag", [
    ("EIA industrial electricity", 3),
    ("Eurostat Comext (EU trade)", 3),
    ("INEGI (Mexico)", 3),
    ("US BLS PPI", 2),
    ("US Census Bureau imports", 2),
    ("Japan customs exports", 2),
    ("World Bank Pink Sheet", 1),
    ("Ceiba consulting", 1),        # whole words only: "ceiba" is not EIA
    (None, 1),
])
def test_each_source_has_its_usual_publication_lag(db, provider, lag):
    s = _series(db, frequency="Monthly", provider=provider)
    try:
        assert publication_lag(s)[0] == lag
    finally:
        _cleanup(db, s)


def test_a_slow_source_is_not_late_when_it_is_on_its_usual_delay(db):
    s = _series(db, frequency="Monthly", provider="EIA industrial electricity")
    try:
        _months(db, s, [(2026, 8, 100, "actual")])           # August on 10 Nov: EIA is on time
        h = series_health(db, s.id, as_of=R1_AS_OF)
        assert h.state == CURRENT and h.stale_after_months == 4 and h.publication_lag_months == 3
        assert h.stale_rule == "monthly, EIA publishes 3 months behind"
        assert series_health(db, s.id, as_of=date(2027, 1, 1)).state == STALE    # 5 old: missed releases
    finally:
        _cleanup(db, s)


def test_a_one_month_source_keeps_the_two_month_rule(db):
    s = _series(db, frequency="Monthly", provider="World Bank Pink Sheet")
    try:
        _months(db, s, [(2026, 8, 100, "actual")])           # the World Bank had October
        h = series_health(db, s.id, as_of=R1_AS_OF)
        assert (h.state, h.stale_after_months, h.stale_rule) == (STALE, 2, "monthly")
    finally:
        _cleanup(db, s)


def test_a_drop_series_the_app_fetches_is_judged_by_its_source_not_the_drop(db, monkeypatch):
    from app.services import scraper
    from app.services.refresh_route import SCRAPER, refresh_routes
    s = _drop_series(db, frequency="Monthly", provider="US BLS PPI")
    try:
        _months(db, s, [(2026, 6, 100, "actual")])           # 5 months old
        assert series_health(db, s.id, as_of=R1_AS_OF).stale_rule == "content drop"
        # The source switch: a scraper is registered and enabled for it.
        monkeypatch.setitem(scraper.SCRAPER_REGISTRY, s.name, scraper.BaseScraper)
        monkeypatch.setitem(scraper.SCRAPER_SOURCE_LABELS, s.name, "BLS")
        s.scrape_enabled = True
        db.commit()
        h = series_health(db, s.id, as_of=R1_AS_OF)
        assert (h.state, h.stale_after_months) == (STALE, 3)
        assert h.stale_rule == "monthly, BLS publishes 2 months behind"
        route = next(r for r in refresh_routes(db) if r.commodity_id == s.id)
        assert route.route == SCRAPER
    finally:
        _cleanup(db, s)
