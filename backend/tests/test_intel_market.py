"""The Market & Costs maths (app/services/intel_market.py) and its endpoint.

Two halves. The pure functions are tested on synthetic series, so the rules
(rebasing, flat carry-forward, the cycle, the JS-compatible formatting the
narratives depend on) hold whatever is loaded. The regression half reads a
test database holding a content load of the current drop (`content_loaded`:
it fails, never skips, without one) and checks the numbers the walk shows for
ferric chloride in Europe against values computed from the drop files at test
time by `tests/content_drop_expect.py` (recipe from FORMULA_COMBOS, series
from FCOVERED, levels from FIDX). No drop figure or label is written here.

Read-only against the database: nothing here writes.
"""
from __future__ import annotations

import statistics

import pytest

from app.constants.trust import TRUST_GRADES
from app.database import SessionLocal, bypass_rls_var
from app.services import intel_catalogue as ic
from app.services import intel_market as mk
from tests import content_drop_expect as expect

# The card the design's walk opens (an identifier, not drop text).
FERRIC = "BCI-FECL3-LIQ"


def _ferric_eu(month: int) -> list[tuple[str, str | None, float, float]]:
    """(label, series key, weight, level) of the ferric chloride EU recipe at
    `month` (an index into FIDX, which starts in January 2023), read from the
    drop. A tag FCOVERED does not carry (the margin's `fixed`) sits at 100."""
    covered, fidx = expect.raw("FCOVERED"), expect.raw("FIDX")
    rows = []
    for weight, label, tag, _category in expect.combo_lines(FERRIC, "EU"):
        key = covered.get(tag)
        rows.append((label, key, weight, float(fidx[key][month]) if key else 100.0))
    return rows


def _index(rows) -> float:
    return sum(w * level for _l, _k, w, level in rows) / sum(r[2] for r in rows)


# ── Synthetic fixtures ───────────────────────────────────────────────────────

def _timeline(n_actual: int = 42, n_forecast: int = 6) -> mk.Timeline:
    months = []
    y, m = 2023, 1
    for _ in range(n_actual + n_forecast):
        months.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return mk.Timeline(actual=tuple(months[:n_actual]), forecast=tuple(months[n_actual:]))


def _series(cid: int, actual: list[float], forecast: list[float] | None, tl: mk.Timeline,
            key: str = "s") -> mk.SeriesData:
    return mk.SeriesData(
        id=cid, key=key, name=key, agency="Agency", freq="Monthly", base_period="2023-01",
        actual=dict(zip(tl.actual, actual)),
        forecast=dict(zip(tl.forecast, forecast)) if forecast is not None else {},
    )


def _line(label, weight, cid=None, category="feedstock", tag=None):
    return {"label": label, "weight": weight, "commodity_id": cid,
            "component_type": "index" if cid else "fixed",
            "cost_category": category, "tag": tag or (f"T{cid}" if cid else None)}


# ── Formatting the narratives depend on ──────────────────────────────────────

@pytest.mark.parametrize("x,expected", [
    (0.25, "0.3"), (-0.25, "-0.3"), (1.45, "1.4"), (99.1982, "99.2"), (-0.04, "0.0"),
    (13.0, "13.0"), (2.35, "2.4"),
])
def test_js_fixed_rounds_like_javascript(x, expected):
    # 1.45 is 1.4499999… in binary, so JS prints 1.4; 0.25 is exact and
    # rounds away from zero.
    assert mk.js_fixed(x) == expected


@pytest.mark.parametrize("n,expected", [
    (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"),
    (13, "13th"), (21, "21st"), (43, "43rd"), (100, "100th"), (111, "111th"),
])
def test_ordinal_matches_the_mockup(n, expected):
    assert mk.ordinal(n) == expected


def test_js_num_prints_integers_without_a_decimal():
    assert mk.js_num(32.0) == "32"
    assert mk.js_num(2.5) == "2.5"


# ── The series ───────────────────────────────────────────────────────────────

def test_an_index_line_is_rebased_to_its_base_month_and_fixed_lines_ride_flat():
    tl = _timeline()
    # A series whose January 2023 point is 110 (like ilmenite), ending at 121.
    actual = [110.0] * 41 + [121.0]
    series = {1: _series(1, actual, [121.0] * 6, tl)}
    lines = [_line("Feed", 60, 1), _line("Margin", 40, category="margin")]
    cs = mk.combo_series(lines, series, tl)
    assert cs.levels[0] == pytest.approx(100.0)
    # 60 × 110 + 40 × 100, over 100 → the feed is +10% → index 106.
    assert cs.levels[tl.last_actual] == pytest.approx(106.0)
    assert cs.per_line[1] == [100.0] * 48


def test_a_series_with_no_forecast_carries_its_last_actual():
    tl = _timeline()
    series = {1: _series(1, [100.0] * 41 + [120.0], None, tl)}
    cs = mk.combo_series([_line("Feed", 100, 1)], series, tl)
    assert cs.levels[tl.last_actual + 1:] == [pytest.approx(120.0)] * 6
    assert cs.line_forecast == [mk.FORECAST_NONE]
    assert mk.is_flat_forecast(cs, tl)


def test_the_forecast_kind_says_model_flat_or_none():
    tl = _timeline()
    actual = [100.0 + (i % 5) for i in range(42)]
    lines = [_line("Feed", 60, 1), _line("Margin", 40, category="margin")]

    flat = {1: _series(1, actual, [actual[-1]] * 6, tl)}
    cs = mk.combo_series(lines, flat, tl)
    assert flat[1].forecast_kind(tl) == mk.FORECAST_FLAT
    assert ic._forecast(cs, tl, lines)["kind"] == mk.FORECAST_FLAT

    moving = {1: _series(1, actual, [actual[-1] + 1 + i for i in range(6)], tl)}
    cs = mk.combo_series(lines, moving, tl)
    f = ic._forecast(cs, tl, lines)
    assert f["kind"] == mk.FORECAST_MODEL and f["flat_weight_pct"] == 0.0
    assert f["vintage"] and f["vintage_label"]

    # A fixed-only recipe, or a series with no forecast at all: no forecast.
    cs = mk.combo_series([_line("Fixed", 100)], {}, tl)
    assert ic._forecast(cs, tl, [_line("Fixed", 100)])["kind"] == mk.FORECAST_NONE
    bare = {1: _series(1, actual, None, tl)}
    cs = mk.combo_series(lines, bare, tl)
    f = ic._forecast(cs, tl, lines)
    assert f["kind"] == mk.FORECAST_NONE and f["vintage"] is None


def test_the_forecast_vintage_is_the_one_the_index_loader_records():
    from app.services.content_drop.indexes import FORECAST_VINTAGE
    vintage, label = ic._vintage()
    assert vintage == mk.period_key(FORECAST_VINTAGE)
    assert label == f"{mk.MONTH_NAMES[FORECAST_VINTAGE[1] - 1]} {FORECAST_VINTAGE[0]}"


def test_the_stack_top_is_the_index_level_every_month():
    tl = _timeline()
    series = {1: _series(1, [100 + i * 0.5 for i in range(42)], [130.0] * 6, tl),
              2: _series(2, [100 - i * 0.2 for i in range(42)], [90.0 + i for i in range(6)], tl)}
    lines = [_line("A", 50, 1), _line("B", 30, 2), _line("Margin", 20, category="margin")]
    cs = mk.combo_series(lines, series, tl)
    st = mk.stack(lines, cs, tl)
    assert len(st["periods"]) == 48
    for t, period in enumerate(st["periods"]):
        assert sum(period["by_line"].values()) == pytest.approx(cs.levels[t], abs=0.01)


def test_duplicate_labels_stay_separate_in_the_stack():
    tl = _timeline()
    series = {1: _series(1, [100.0] * 42, [100.0] * 6, tl)}
    lines = [_line("Same", 50, 1), _line("Same", 50, 1)]
    cs = mk.combo_series(lines, series, tl)
    assert list(mk.stack(lines, cs, tl)["periods"][0]["by_line"]) == ["Same", "Same (2)"]


# ── Dynamics: the numbers and the narrative agree ───────────────────────────

def _dyn(actual: list[float]) -> dict:
    tl = _timeline()
    series = {1: _series(1, actual, [actual[-1]] * 6, tl)}
    lines = [_line("Feed", 100, 1)]
    cs = mk.combo_series(lines, series, tl)
    return mk.dynamics(lines, cs, series, tl, {})


def test_dynamics_levels_are_the_printed_values_and_the_move_is_their_difference():
    # March 2026 = 105.64, June 2026 = 110.46. Printed: 105.6 → 110.5. The raw
    # move is 4.82 (4.8); the move between the printed levels is 4.9.
    actual = [100.0] * 38 + [105.64, 107.0, 108.0, 110.46]
    d = _dyn(actual)
    assert (d["short_from"], d["short_to"], d["short_points"]) == (105.6, 110.5, 4.9)
    assert "moved up 4.9 points (105.6 → 110.5)" in d["narrative_3m"]
    assert d["short_pct"] == round(4.9 / 105.6 * 100, 2)
    # 24M: the window opens at Jul 2024 (100.0) and closes at 110.46 → 110.5.
    assert (d["long_from"], d["long_to"], d["long_points"]) == (100.0, 110.5, 10.5)
    assert "ran from 100.0 to 110.5 (+10.5 points net)" in d["narrative_24m"]
    assert d["long_high"]["level"] == 110.5 and d["long_low"]["level"] == 100.0
    close = next(s for s in d["signals_24m"] if s["kind"] == "close")
    assert close["text"].endswith("window closes at 110.5, net +10.5 vs window start")


def test_dynamics_levels_round_like_the_rest_of_the_page():
    # 66.6497 is returned as 66.65 everywhere else (snapshot, series, table)
    # and the page prints that as 66.7; the narrative must print 66.7 too.
    actual = [100.0] * 38 + [71.0, 70.0, 68.0, 66.6497]
    d = _dyn(actual)
    assert d["short_to"] == 66.7 and d["long_to"] == 66.7
    assert "(71.0 → 66.7)" in d["narrative_3m"]


def test_a_move_that_rounds_to_zero_is_flat_in_both_places():
    actual = [100.0] * 38 + [99.24, 99.2, 99.2, 99.18]   # 99.2 → 99.2
    d = _dyn(actual)
    assert d["short_points"] == 0.0 and d["short_direction"] == "flat"
    assert "moved only slightly (99.2 → 99.2)" in d["narrative_3m"]


# ── Card, tier, cycle ────────────────────────────────────────────────────────

def test_card_numbers_are_the_last_twelve_actuals_and_the_move_since_base():
    tl = _timeline()
    series = {1: _series(1, [100.0 + i for i in range(42)], [150.0] * 6, tl)}
    cs = mk.combo_series([_line("Feed", 100, 1)], series, tl)
    card = mk.card_numbers(cs, tl)
    assert card["sparkline"] == [float(100 + i) for i in range(30, 42)]
    assert card["current_index"] == 141.0
    assert card["trend_pct"] == 41.0 and card["trend_dir"] == "up"
    assert card["sparkline_from"] == "2025-07" and card["sparkline_to"] == "2026-06"


def test_tier_counts_unindexed_non_margin_lines():
    idx, fixed, margin = _line("A", 50, 1), _line("B", 20), _line("M", 30, category="margin")
    assert mk.tier([idx, margin]) == "P1"
    assert mk.tier([idx, idx, idx, fixed, margin]) == "P2"   # 1 of 4
    assert mk.tier([idx, fixed, margin]) == "P3"             # 1 of 2
    assert mk.tier([]) is None


def test_a_flat_history_sits_at_the_fiftieth_percentile():
    tl = _timeline()
    cs = mk.combo_series([_line("Fixed", 100)], {}, tl)
    c = mk.cycle(cs, tl)
    assert c["percentile"] == 50 and c["position"] == "flat"
    assert c["window_months"] == 42


def test_seasonality_without_factors_is_flat_and_says_so():
    s = mk.seasonality([_line("Fixed", 100)], {}, 100.0)
    assert s["factors"] == [100.0] * 12
    assert s["note"] == mk.LOW_SEASONALITY_NOTE


def test_dispersion_is_the_libraries_statistic():
    tl = _timeline()
    actual = [100.0 * (1.01 ** (i % 7)) for i in range(42)]
    series = {1: _series(1, actual, None, tl)}
    cs = mk.combo_series([_line("Feed", 100, 1)], series, tl)
    changes = [(b - a) / a * 100 for a, b in zip(actual, actual[1:])]
    assert mk.dispersion(cs, tl) == pytest.approx(statistics.pstdev(changes))


# ── Regression on the loaded drop ────────────────────────────────────────────

@pytest.fixture(scope="module")
def catalogue(content_loaded):
    prior = bypass_rls_var.get()
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        yield ic.build_catalogue(db)
    finally:
        db.rollback()
        db.close()
        bypass_rls_var.set(prior)


def test_the_timeline_ends_where_the_drop_ends(catalogue):
    from app.services.content_drop.indexes import DATA_AS_OF
    tl = catalogue.timeline
    assert tl.actual[-1] == expect.last_actual_month() == tuple(DATA_AS_OF)
    assert tl.actual[0] == expect.FIDX_START


def test_ferric_chloride_eu_matches_the_drop_at_the_last_actual_month(catalogue):
    p = catalogue.products[FERRIC]
    combo = p.combo_for("EU")
    cs = mk.combo_series(combo.lines, catalogue.series, catalogue.timeline)
    last = catalogue.timeline.last_actual
    assert last == expect.month_index(*expect.last_actual_month())
    expected = _ferric_eu(last)
    assert cs.levels[last] == pytest.approx(_index(expected), abs=0.05)
    rows = mk.components(combo.lines, cs, catalogue.series, catalogue.timeline)
    assert [(r["label"], r["series_key"], r["weight_pct"]) for r in rows] == \
        [(label, key, weight) for label, key, weight, _level in expected]
    for row, (_l, _k, _w, level) in zip(rows, expected):
        assert row["current_level"] == pytest.approx(level, abs=0.05)
    # Weighted impacts close the gap between the index and 100.
    assert sum(r["weighted_impact_pct"] for r in rows) == \
        pytest.approx(_index(expected) - 100, abs=0.05)


def test_the_market_endpoint_serves_the_same_numbers(tenant_a, client_as, catalogue):
    r = client_as(tenant_a).get(f"/api/intel/products/{FERRIC}/market", params={"region": "EU"})
    assert r.status_code == 200, r.text
    body = r.json()
    tl = catalogue.timeline
    expected = _ferric_eu(tl.last_actual)
    latest = _index(expected)
    last_period = mk.period_key(tl.actual[-1])
    regions = list(dict.fromkeys(c["region"] for c in expect.combos(FERRIC)))
    eu = body["series_by_region"]["EU"]
    assert len(eu) == len(tl.months) and eu[0]["level"] == 100.0
    point = next(p for p in eu if p["period"] == last_period)
    assert point["level"] == pytest.approx(latest, abs=0.05) and point["kind"] == "actual"
    assert eu[-1]["kind"] == "forecast" and eu[-1]["period"] == mk.period_key(tl.forecast[-1])
    # The last actual month is when the source data ends, not "today".
    assert body["data_as_of"] == last_period and "today" not in body["timeline"]
    assert body["snapshot"]["index_latest"] == pytest.approx(latest, abs=0.05)
    assert body["snapshot"]["index_latest_period"] == last_period
    assert body["snapshot"]["cost_lines"] == len(expected)
    assert body["snapshot"]["regions"] == len(regions)
    assert body["snapshot"]["review_status"] in ("pending", "signed_off")
    assert [c["weight_pct"] for c in body["components"]] == [r[2] for r in expected]
    assert [c["current_level"] for c in body["components"]] == \
        pytest.approx([r[3] for r in expected], abs=0.05)
    cycle = body["cycle"]
    assert cycle["low"] <= round(latest, 1) <= cycle["high"]
    assert 0 <= cycle["percentile"] <= 100
    assert body["trust"]["grade"] in TRUST_GRADES and "tier" not in body
    # The 3M sentence prints the returned levels; the top driver is the heaviest line.
    dyn = body["dynamics"]
    assert dyn["short_to"] == pytest.approx(round(latest, 1), abs=0.05)
    assert dyn["narrative_3m_html"].startswith(
        "Over the last 3 months the should-cost index moved ")
    assert f"({dyn['short_from']:.1f} → {dyn['short_to']:.1f})" in dyn["narrative_3m_html"]
    assert f"from {dyn['long_from']:.1f} to {dyn['long_to']:.1f}" in dyn["narrative_24m"]
    top = max(expected, key=lambda r: r[2])
    assert dyn["top_driver"]["label"] == top[0]
    assert f"<strong>{top[0]}</strong> ({top[2]:g}% weight)" in dyn["narrative_3m_html"]
    # No cone, anywhere: the demo's band was a heuristic, not a fitted interval.
    assert body["band"] is None and set(body["band_by_region"].values()) == {None}
    assert body["forecast"]["kind"] in (mk.FORECAST_MODEL, mk.FORECAST_FLAT, mk.FORECAST_NONE)
    assert body["forecast"]["kind"] == (mk.FORECAST_FLAT if body["flat_forecast"]
                                        else body["forecast"]["kind"])
    from app.services.content_drop.indexes import FORECAST_VINTAGE
    if body["forecast"]["kind"] != mk.FORECAST_NONE:
        assert body["forecast"]["vintage"] == mk.period_key(FORECAST_VINTAGE)
    assert len(body["stack"]) == len(tl.months) and len(body["stack_lines"]) == len(expected)
    for point, period in zip(eu, body["stack"]):
        assert sum(period["by_line"].values()) == pytest.approx(point["level"], abs=0.02)
    assert body["regions"] == regions
    assert set(body["series_by_region"]) == set(regions)
    assert body["volatility"]["percentile"] is not None or body["volatility"]["reason"]


def test_the_volatility_dispersion_agrees_with_the_engine(db, catalogue):
    from app.services.intelligence import derive
    p = catalogue.products[FERRIC]
    combo = p.combo_for("EU")
    cs = mk.combo_series(combo.lines, catalogue.series, catalogue.timeline)
    engine = derive(db, p.id, "Europe")
    assert engine.volatility["dispersion"] == pytest.approx(mk.dispersion(cs, catalogue.timeline),
                                                            abs=1e-3)


def test_a_flat_carry_forward_product_is_labelled_as_such(tenant_a, client_as, catalogue):
    flat = None
    for item in catalogue.items:
        p = catalogue.products[item["pid"]]
        combo = p.combo_for(p.default_region())
        cs = mk.combo_series(combo.lines, catalogue.series, catalogue.timeline) if combo else None
        if cs and any(l["component_type"] == "index" for l in combo.lines) \
                and mk.is_flat_forecast(cs, catalogue.timeline):
            flat = (p.code, p.default_region())
            break
    assert flat is not None, "no fully flat-forecast product in the loaded data"
    r = client_as(tenant_a).get(f"/api/intel/products/{flat[0]}/market",
                                params={"region": flat[1]})
    body = r.json()
    assert body["flat_forecast"] is True and body["band"] is None
    assert body["forecast"]["kind"] == mk.FORECAST_FLAT


def _a_region_priced_twice() -> tuple[str, str, list[str]]:
    """(pid, region, variants) of the first listed card with one region
    priced by two recipe variants, from the drop."""
    for pid in expect.listed_codes():
        by_region: dict[str, list[str]] = {}
        for combo in expect.combos(pid):
            if combo.get("variant"):
                by_region.setdefault(combo["region"], []).append(combo["variant"])
        for region, variants in by_region.items():
            if len(variants) > 1:
                return pid, region, variants
    raise LookupError("no listed card prices one region twice")


def test_region_variants_are_kept_apart(tenant_a, client_as, catalogue):
    """A region priced by two recipe variants: filter the recipe by region
    AND variant."""
    pid, region, variants = _a_region_priced_twice()
    c = client_as(tenant_a)
    first = c.get(f"/api/intel/products/{pid}/market", params={"region": region}).json()
    assert first["variant"] == variants[0]
    assert sorted(first["variants"]) == sorted(variants)
    bodies = [c.get(f"/api/intel/products/{pid}/market",
                    params={"region": region, "variant": v}).json() for v in variants]
    assert len({b["combo_id"] for b in bodies}) == len(variants)
    # Each variant is its own recipe. Filtering by region alone (what
    # derive() does) would blend them into one.
    for v, body in zip(variants, bodies):
        lines = expect.combo_lines(pid, region, v)
        assert body["variant"] == v
        assert len(body["components"]) == len(lines)
        assert body["total_weight_pct"] == pytest.approx(sum(ln[0] for ln in lines))
    assert c.get(f"/api/intel/products/{pid}/market",
                 params={"region": region, "variant": "nope"}).status_code == 404
