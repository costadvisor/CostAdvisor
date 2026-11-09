"""Resolving live-number tokens to data (Jira 210 S2).

* `last` / `asof` read the newest actual monthly value, `change12m` the same
  month a year earlier;
* late follows `series_health` (the app-wide per-frequency rule);
* a series with only forecasts shows a forecast point, marked;
* unknown series, a series with no values, no value a year back: missing;
* a recipe weight comes from the platform recipe line;
* a new month changes the answer with no edit (acceptance 2).

Each test builds its own series (a `test-live-` key) and ages it against a
fixed `as_of`. Names and numbers are made up.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.formula_template import FormulaTemplate, FormulaTemplateComponent
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexMonthlyValue
from app.services.live_resolver import resolve
from app.services.live_tokens import CURRENT, FORECAST, LATE, MISSING, parse_token

AS_OF = date(2026, 8, 3)


@pytest.fixture
def made(db):
    bypass_rls_var.set(True)
    created = {"series": [], "templates": []}
    yield created
    db.rollback()
    bypass_rls_var.set(True)
    for s in created["series"]:
        db.execute(text("DELETE FROM index_monthly_values WHERE commodity_id = :i"), {"i": s.id})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": s.id})
    for t in created["templates"]:
        db.execute(text("DELETE FROM formula_template_components WHERE template_id = :i"), {"i": t.id})
        db.execute(text("DELETE FROM formula_templates WHERE id = :i"), {"i": t.id})
    db.commit()


def _series(db, made, points, *, frequency="Monthly") -> CommodityIndex:
    """`points` is [(year, month, value, kind)]."""
    s = CommodityIndex(name=f"live-{uuid.uuid4().hex[:8]}", scrape_enabled=False,
                       commodity_key=f"test-live-{uuid.uuid4().hex[:8]}", frequency=frequency)
    db.add(s)
    db.flush()
    for year, month, value, kind in points:
        db.add(IndexMonthlyValue(commodity_id=s.id, year=year, month=month, value=value, kind=kind))
    db.commit()
    made["series"].append(s)
    return s


def _one(db, raw, as_of=AS_OF):
    t = parse_token(raw)
    return resolve(db, [t], as_of=as_of)[t]


# ── Index tokens ─────────────────────────────────────────────────────────────

def test_last_is_the_newest_actual_with_its_month(db, made):
    s = _series(db, made, [(2026, 5, 120, "actual"), (2026, 6, 129.9, "actual"),
                           (2026, 7, 131, "forecast")])
    r = _one(db, f"{{{{index:{s.commodity_key}|last}}}}")
    assert (r.status, r.value, r.year, r.month) == (CURRENT, pytest.approx(129.9), 2026, 6)


def test_a_new_month_changes_the_answer_with_no_edit(db, made):
    s = _series(db, made, [(2026, 6, 100, "actual")])
    raw = f"{{{{index:{s.commodity_key}|last}}}}"
    assert _one(db, raw).month == 6
    db.add(IndexMonthlyValue(commodity_id=s.id, year=2026, month=7, value=104, kind="actual"))
    db.commit()
    r = _one(db, raw)
    assert (r.value, r.month) == (pytest.approx(104), 7)


def test_change12m_compares_with_the_same_month_a_year_earlier(db, made):
    s = _series(db, made, [(2025, 6, 100, "actual"), (2026, 5, 140, "actual"),
                           (2026, 6, 125, "actual")])
    r = _one(db, f"{{{{index:{s.commodity_key}|change12m}}}}")
    assert r.status == CURRENT and r.value == pytest.approx(25.0) and r.month == 6


def test_change12m_without_a_year_of_history_is_missing(db, made):
    s = _series(db, made, [(2026, 6, 100, "actual")])
    r = _one(db, f"{{{{index:{s.commodity_key}|change12m}}}}")
    assert r.status == MISSING and "12 months earlier" in r.reason


def test_asof_is_the_month_of_the_newest_actual(db, made):
    s = _series(db, made, [(2026, 4, 100, "actual"), (2026, 6, 101, "actual")])
    r = _one(db, f"{{{{index:{s.commodity_key}|asof}}}}")
    assert (r.status, r.year, r.month) == (CURRENT, 2026, 6)


def test_late_follows_the_series_health_rule(db, made):
    # A keyed series is a content-drop series: late after 6 months (Jira 209
    # S4), whatever its frequency. June is current in December, late in January.
    s = _series(db, made, [(2026, 6, 100, "actual")])
    raw = f"{{{{index:{s.commodity_key}|last}}}}"
    assert _one(db, raw, as_of=date(2026, 12, 1)).status == CURRENT
    assert _one(db, raw, as_of=date(2027, 1, 1)).status == LATE


def test_a_forecast_only_series_shows_a_forecast_point(db, made):
    s = _series(db, made, [(2026, 7, 110, "forecast"), (2026, 9, 112, "forecast")])
    r = _one(db, f"{{{{index:{s.commodity_key}|last}}}}")
    assert (r.status, r.value, r.month) == (FORECAST, pytest.approx(110), 7)


def test_unknown_and_empty_series_are_missing(db, made):
    empty = _series(db, made, [])
    assert _one(db, "{{index:no-such-series|last}}").status == MISSING
    r = _one(db, f"{{{{index:{empty.commodity_key}|last}}}}")
    assert r.status == MISSING and "no values" in r.reason


def test_many_tokens_resolve_in_one_call(db, made):
    a = _series(db, made, [(2026, 6, 100, "actual")])
    b = _series(db, made, [(2026, 6, 200, "actual")])
    tokens = [parse_token(f"{{{{index:{a.commodity_key}|last}}}}"),
              parse_token(f"{{{{index:{b.commodity_key}|last}}}}"),
              parse_token("{{index:no-such-series|asof}}")]
    out = resolve(db, tokens, as_of=AS_OF)
    assert [out[t].status for t in tokens] == [CURRENT, CURRENT, MISSING]
    assert [out[t].value for t in tokens[:2]] == [pytest.approx(100), pytest.approx(200)]


# ── Recipe tokens ────────────────────────────────────────────────────────────

def test_a_recipe_weight_comes_from_the_platform_recipe(db, tenant_a, made):
    # tenant_a first: fixtures tear down in reverse, so the recipe goes before its author.
    t = FormulaTemplate(name=f"Live test {uuid.uuid4().hex[:6]}", code=f"TST-{uuid.uuid4().hex[:6]}",
                        team_id=None, created_by=tenant_a["user_id"])
    db.add(t)
    db.flush()
    db.add(FormulaTemplateComponent(template_id=t.id, name="Caustic soda feed",
                                    component_type="fixed", region="Europe", weight_pct=34))
    db.commit()
    made["templates"].append(t)

    r = _one(db, f"{{{{recipe:{t.code}|europe|caustic soda feed|weight}}}}")   # case-insensitive
    assert (r.status, r.value) == (CURRENT, pytest.approx(34))
    missing = _one(db, f"{{{{recipe:{t.code}|NA|Caustic soda feed|weight}}}}")
    assert missing.status == MISSING and "no line" in missing.reason
