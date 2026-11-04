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
    frequency_interval_months, series_health,
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
