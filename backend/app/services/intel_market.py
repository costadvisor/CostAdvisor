"""Market & Costs maths for the Intelligence product page (design §4.2).

Pure functions over plain data — no ORM objects, no drop-file reads. The
catalogue cache (`services/intel_catalogue.py`) feeds them the stored recipe
lines and the stored monthly series; the router shapes the result.

**Grain: monthly.** `intelligence.derive()` is quarterly (quarter means rebased
to 2023Q1) and is what the portfolio side reads. The Intelligence product page
shows the mockup's numbers, which are monthly levels rebased to January 2023
(the monthly level and `derive()`'s quarter mean can differ by a couple of
points). The rule is the same as `derive()`'s — an index line scales by its
series ratio to the base month, a fixed or margin line rides flat at 100, and
the level is `Σ weight × level_line / Σ weight` — only the grain differs.

**Rebased, always.** The mockup multiplies raw FIDX values. Those equal the
rebased value for every series whose Jan 2023 point is 100; a few series do
not start at 100 (`base_period` NULL), so products using them differ from the
mockup here on purpose: this page says "base 100 = Jan 2023" and means it.

Where a number is the mockup's own method it says so, and the function that
computes it names the mockup function it copies (`computeFormulaSeries`,
`buildDynamicsFor`, `buildCyclePosFor`, `computeFormulaSeasonality`,
`computeTier`, `drawChart3`'s stack). The mockup's forecast cone
(`drawChart1`'s band) is not reproduced: it was a heuristic, not a fitted
interval, so the page draws the dashed forecast only. Two numbers use the
app's own calibrated method instead of the mockup's hard-coded one, because
the mockup's is what the app replaced: volatility (the active calibration
ladder, `index_dossier.percentile_for`, not `VOLATILITY_PERCENTILE_BREAKPOINTS`)
and seasonal factors (the generated `index_seasonal_factors`, not the drop's
`INDEX_SEASONALITY`, which the spec says never to import).
"""
from __future__ import annotations

import html
import math
import re
import statistics
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

# ── Vocabulary ───────────────────────────────────────────────────────────────

BASE_PERIOD = (2023, 1)

MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")

# Display order of the mockup's region dots (`buildAutoCard` allRegs), plus GL.
REGION_ORDER = ("NA", "LA", "EU", "MEA", "IN", "CN", "APAC", "GL")
REGION_NAMES = {
    "NA": "North America", "LA": "Latin America", "EU": "Europe",
    "MEA": "Middle East & Africa", "IN": "India", "CN": "China",
    "APAC": "Asia Pacific", "GL": "Global",
}
# Spec §7 (from the mockup's RC map).
REGION_COLORS = {
    "EU": "#0F6E56", "CN": "#A53030", "NA": "#1B2B4B", "APAC": "#6B6560",
    "IN": "#0B6E6E", "MEA": "#BA7517", "LA": "#534AB7", "GL": "#7B8794",
}

MARGIN = "margin"
INDEX = "index"

# Card trend badge thresholds (`buildAutoCard`: change > 2 up, < -2 down).
TREND_THRESHOLD = 2.0
# `buildDynamicsFor`'s dir(): a move under 0.05 reads as flat.
DIR_EPSILON = 0.05

FORECAST_MODEL = "model"
FORECAST_FLAT = "flat_carry_forward"
FORECAST_NONE = "none"

FLAT_EPS = 1e-9


# ── JavaScript-compatible formatting ─────────────────────────────────────────
# The narratives reproduce the mockup's template strings, so the numbers in
# them are formatted the way JavaScript formats them.

def js_fixed(x: float, digits: int = 1) -> str:
    """`Number.prototype.toFixed`: round half away from zero on the exact
    binary value (Python's `%.1f` rounds half to even)."""
    q = Decimal(1).scaleb(-digits)
    d = Decimal(x).quantize(q, rounding=ROUND_HALF_UP)
    if d == 0:
        d = abs(d)
    return f"{d:.{digits}f}"


def js_round(x: float) -> int:
    """`Math.round`: floor(x + 0.5)."""
    return math.floor(x + 0.5)


def js_num(x: float) -> str:
    """How a template literal prints a number: 32 → "32", 2.5 → "2.5"."""
    if x is None:
        return ""
    if float(x).is_integer():
        return str(int(x))
    return repr(float(x))


def ordinal(n: int) -> str:
    """The mockup's `ordinal()`."""
    s = ("th", "st", "nd", "rd")
    v = n % 100
    # JavaScript's `%` keeps the dividend's sign: (13 - 20) % 10 is -7, an
    # undefined index, so 13 falls through to "th".
    idx = int(math.fmod(v - 20, 10))
    if 0 <= idx < 4:
        suffix = s[idx]
    elif 0 <= v < 4:
        suffix = s[v]
    else:
        suffix = s[0]
    return f"{n}{suffix}"


# ── Timeline ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Timeline:
    """The months the index layer carries: actuals, then the forecast."""
    actual: tuple[tuple[int, int], ...]
    forecast: tuple[tuple[int, int], ...]

    @property
    def months(self) -> tuple[tuple[int, int], ...]:
        return self.actual + self.forecast

    @property
    def last_actual(self) -> int:
        return len(self.actual) - 1


def month_label(period: tuple[int, int]) -> str:
    """`Jan 2023` style."""
    return f"{MONTH_ABBR[period[1] - 1]} {period[0]}"


def quarter_label(period: tuple[int, int]) -> str:
    """The mockup's `monthLabel()` — despite its name it prints a quarter."""
    return f"Q{(period[1] - 1) // 3 + 1} {period[0]}"


def period_key(period: tuple[int, int]) -> str:
    return f"{period[0]:04d}-{period[1]:02d}"


# ── Series data ──────────────────────────────────────────────────────────────

@dataclass
class SeriesData:
    """One `commodity_indexes` series and its monthly values."""
    id: int
    key: str | None
    name: str | None
    agency: str | None
    freq: str | None
    base_period: str | None
    actual: dict[tuple[int, int], float] = field(default_factory=dict)
    forecast: dict[tuple[int, int], float] = field(default_factory=dict)

    def forecast_kind(self, timeline: Timeline) -> str:
        """`model`, `flat_carry_forward` (every forecast point equals the last
        actual: a carry-forward, not a model) or `none` (no forecast)."""
        if not self.forecast:
            return FORECAST_NONE
        last = self.actual.get(timeline.actual[-1]) if timeline.actual else None
        points = [self.forecast.get(p) for p in timeline.forecast]
        if last is not None and all(v is not None and abs(v - last) < FLAT_EPS for v in points):
            return FORECAST_FLAT
        return FORECAST_MODEL


# ── The monthly should-cost series (mockup computeFormulaSeries) ─────────────

@dataclass
class ComboSeries:
    levels: list[float]                 # len(timeline.months)
    per_line: list[list[float]]         # each line's rebased level, same length
    weight_sum: float
    line_forecast: list[str | None]     # forecast kind per index line, None for flat lines
    gaps: list[dict]


def line_levels(line: dict, series: dict[int, SeriesData], timeline: Timeline,
                base_period: tuple[int, int]) -> tuple[list[float], str | None, dict | None]:
    """One line's level (base 100 at the base month) over the timeline.

    An index line scales by its series' ratio to the base month; a missing
    month carries the previous value; a series with no forecast carries its
    last actual (the mockup's `fSeriesFor` does the same). A fixed or margin
    line — or an index line whose series has no base value — rides flat at 100.
    Returns (levels, forecast_kind, gap).
    """
    n = len(timeline.months)
    cid = line.get("commodity_id")
    if line.get("component_type") != INDEX or not cid:
        return [100.0] * n, None, None
    s = series.get(cid)
    base = s.actual.get(base_period) if s else None
    if not base:
        return [100.0] * n, None, {
            "line": line.get("label"), "series_key": s.key if s else None,
            "reason": "no value at the base month — line rides flat at 100",
        }
    out: list[float] = []
    last = base
    missing = 0
    for p in timeline.actual:
        v = s.actual.get(p)
        if v is None:
            v = last
            missing += 1
        last = v
        out.append(100.0 * v / base)
    for p in timeline.forecast:
        v = s.forecast.get(p)
        if v is None:
            v = last
        last = v
        out.append(100.0 * v / base)
    gap = None
    if missing:
        gap = {"line": line.get("label"), "series_key": s.key,
               "reason": f"{missing} actual month(s) missing — previous value carried"}
    return out, s.forecast_kind(timeline), gap


def combo_series(lines: list[dict], series: dict[int, SeriesData], timeline: Timeline,
                 base_period: tuple[int, int] = BASE_PERIOD) -> ComboSeries | None:
    """`Σ weight × line level / Σ weight` per month. None when the recipe has
    no lines or its weights sum to zero."""
    if not lines:
        return None
    weight_sum = sum(float(l["weight"]) for l in lines)
    if weight_sum <= 0:
        return None
    per_line, kinds, gaps = [], [], []
    for line in lines:
        lv, kind, gap = line_levels(line, series, timeline, base_period)
        per_line.append(lv)
        kinds.append(kind)
        if gap:
            gaps.append(gap)
    n = len(timeline.months)
    levels = [
        sum(float(l["weight"]) * per_line[i][t] for i, l in enumerate(lines)) / weight_sum
        for t in range(n)
    ]
    return ComboSeries(levels=levels, per_line=per_line, weight_sum=weight_sum,
                       line_forecast=kinds, gaps=gaps)


def is_flat_forecast(cs: ComboSeries, timeline: Timeline) -> bool:
    """True when every forecast level equals the last actual (a flat
    carry-forward, not a model)."""
    last = cs.levels[timeline.last_actual]
    return all(abs(v - last) < 1e-9 for v in cs.levels[timeline.last_actual + 1:])


def series_points(cs: ComboSeries, timeline: Timeline, digits: int = 2) -> list[dict]:
    n_actual = len(timeline.actual)
    return [
        {"year": p[0], "month": p[1], "period": period_key(p),
         "level": round(cs.levels[i], digits),
         "kind": "actual" if i < n_actual else "forecast"}
        for i, p in enumerate(timeline.months)
    ]


# ── Card numbers (mockup buildAutoCard / renderAutoTiles) ────────────────────

def card_numbers(cs: ComboSeries | None, timeline: Timeline) -> dict:
    """Sparkline = the last 12 actual months; current = last actual; trend =
    current − 100, i.e. the move since January 2023 (what the card badge
    shows), with the ±2 badge thresholds."""
    if cs is None:
        return {"current_index": None, "trend_pct": None, "trend_dir": None,
                "sparkline": [], "sparkline_from": None, "sparkline_to": None}
    last = timeline.last_actual
    start = max(0, last - 11)
    current = cs.levels[last]
    change = current - 100.0
    return {
        "current_index": round(current, 2),
        "trend_pct": round(change, 2),
        "trend_dir": "up" if change > TREND_THRESHOLD else "down" if change < -TREND_THRESHOLD else "flat",
        "sparkline": [round(v, 2) for v in cs.levels[start:last + 1]],
        "sparkline_from": period_key(timeline.months[start]),
        "sparkline_to": period_key(timeline.months[last]),
    }


def is_margin(line: dict) -> bool:
    return line.get("cost_category") == MARGIN


def top_lines(lines: list[dict], n: int = 2) -> list[str]:
    """The heaviest non-margin lines (stable on ties, like the mockup's sort)."""
    ranked = sorted((l for l in lines if not is_margin(l)),
                    key=lambda l: -float(l["weight"]))
    return [l["label"] for l in ranked[:n]]


def tier(lines: list[dict]) -> str | None:
    """Mockup `computeTier`: the share of non-margin lines with no index —
    0 → P1, under 0.34 → P2, else P3. Margin is recognised by its cost
    category (the drop's `kind`), not by the label."""
    if not lines:
        return None
    cost = [l for l in lines if not is_margin(l)]
    total = len(cost) or 1
    unresolved = sum(1 for l in cost if l.get("component_type") != INDEX)
    ratio = unresolved / total
    if ratio == 0:
        return "P1"
    if ratio < 0.34:
        return "P2"
    return "P3"


# ── Components table (mockup regionIndexRows) ────────────────────────────────

def source_label(line: dict, s: SeriesData | None) -> str:
    """The mockup's `src`: `agency (series_key)`, `Fixed cost line`, or `No
    public index`."""
    if line.get("component_type") != INDEX:
        return "Fixed cost line"
    if s is None:
        return "No public index"
    if s.agency:
        return f"{s.agency} ({s.key})"
    return s.key or "No public index"


def components(lines: list[dict], cs: ComboSeries, series: dict[int, SeriesData],
               timeline: Timeline, digits: int = 2) -> list[dict]:
    last = timeline.last_actual
    out = []
    for i, line in enumerate(lines):
        s = series.get(line.get("commodity_id")) if line.get("component_type") == INDEX else None
        level = cs.per_line[i][last]
        weight = float(line["weight"])
        out.append({
            "label": line["label"],
            # `derive()`'s field names, kept alongside for callers written against it.
            "name": line["label"],
            "commodity_id": line.get("commodity_id") if s else None,
            "commodity_key": s.key if s else None,
            "cost_category": line.get("cost_category"),
            "component_type": line.get("component_type"),
            "indexed": line.get("component_type") == INDEX,
            "is_margin": is_margin(line),
            "tag": line.get("tag"),
            "series_key": s.key if s else None,
            "series_name": s.name if s else None,
            "agency": s.agency if s else None,
            "freq": s.freq if s else None,
            "source_label": source_label(line, s),
            "weight_pct": round(weight, 4),
            "current_level": round(level, digits),
            "vs_base_pct": round(level - 100.0, digits),
            "weighted_impact_pct": round(weight * (level - 100.0) / cs.weight_sum, digits),
            "forecast_kind": cs.line_forecast[i],
        })
    return out


# ── Stacked build-up (mockup drawChart3 / computeLineContributions) ─────────

def unique_labels(lines: list[dict]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for line in lines:
        label = line["label"]
        seen[label] = seen.get(label, 0) + 1
        out.append(label if seen[label] == 1 else f"{label} ({seen[label]})")
    return out


def stack(lines: list[dict], cs: ComboSeries, timeline: Timeline, digits: int = 3) -> dict:
    """Per month, each line's `weight × level / Σ weight` — margin included,
    so the stack top equals the index level at every month."""
    labels = unique_labels(lines)
    n_actual = len(timeline.actual)
    periods = []
    for t, p in enumerate(timeline.months):
        periods.append({
            "period": period_key(p), "year": p[0], "month": p[1],
            "kind": "actual" if t < n_actual else "forecast",
            "by_line": {
                labels[i]: round(float(l["weight"]) * cs.per_line[i][t] / cs.weight_sum, digits)
                for i, l in enumerate(lines)
            },
            "total": round(cs.levels[t], digits),
        })
    legend = [{
        "label": labels[i],
        "cost_category": l.get("cost_category"),
        "indexed": l.get("component_type") == INDEX,
        # drawChart3's colouring roles: margin neutral, un-indexed grey.
        "role": "margin" if is_margin(l) else ("indexed" if l.get("component_type") == INDEX
                                                else "no_index"),
    } for i, l in enumerate(lines)]
    return {"lines": legend, "periods": periods}


# ── Cycle position (mockup buildCyclePosFor) ────────────────────────────────

def cycle(cs: ComboSeries, timeline: Timeline) -> dict:
    """Where the last actual month sits in the whole actual history. The
    mockup's panel label says "24-month range"; its computation and verdict
    text use the whole actual window, which is what this returns."""
    hist = cs.levels[:timeline.last_actual + 1]
    low, high, current = min(hist), max(hist), hist[-1]
    pct = js_round((current - low) / (high - low) * 100) if high > low else 50
    window = f"{len(hist)}-month"
    if high == low:
        verdict = "No historical movement in this window — index has held flat since Jan 2023."
        position = "flat"
    elif pct >= 70:
        verdict = (f"Near the top of its {window} range ({ordinal(pct)} percentile) — limited "
                   "room to negotiate down purely on timing; an index-linked clause may protect "
                   "against further upside.")
        position = "high"
    elif pct >= 40:
        verdict = (f"Mid-range of its {window} window ({ordinal(pct)} percentile) — neither a "
                   "clear high nor low point for timing a negotiation.")
        position = "mid"
    else:
        verdict = (f"Near the bottom of its {window} range ({ordinal(pct)} percentile) — a "
                   "relatively favourable window to lock in pricing or extend contract duration.")
        position = "low"
    return {
        "percentile": pct,
        "low": round(low, 1),
        "high": round(high, 1),
        "current": round(current, 2),
        "position": position,
        "verdict": verdict,
        "window_months": len(hist),
        "window_label": window,
        "from": period_key(timeline.actual[0]),
        "to": period_key(timeline.actual[-1]),
        "method": "mockup buildCyclePosFor: (current − low) / (high − low) over the actual "
                  "history, rounded; 50 when flat",
    }


# ── Dynamics (mockup buildDynamicsFor) ───────────────────────────────────────

def _dir(c: float) -> str:
    return "up" if c > DIR_EPSILON else "down" if c < -DIR_EPSILON else "flat"


def _cap(text: str | None) -> str | None:
    return text[:1].upper() + text[1:] if text else None


def _strip_html(markup: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def _lvl1(x: float) -> Decimal:
    """An index level as the page prints it, kept exact.

    Every other level in the market payload (series points, snapshot,
    components) is returned at 2 dp and printed by the page with
    `toFixed(1)`; this is the same two steps, so the narrative's levels are
    the snapshot's and the chart's (a level of 66.6497 → 66.65 → "66.7"
    everywhere, where one rounding step gives 66.6).

    The dynamics narrative and the numbers returned beside it are built from
    these values, so they always agree: `points = to − from` of the printed
    levels. The mockup printed each rounded level but the move from the
    unrounded ones, so in about a quarter of recipes its own sentence did not
    add up ("105.6 → 110.5 … up 4.8 points")."""
    return Decimal(js_fixed(round(x, 2)))


def _signed1(d: Decimal) -> str:
    return f"{'+' if d >= 0 else ''}{d:.1f}"


def dynamics(lines: list[dict], cs: ComboSeries, series: dict[int, SeriesData],
             timeline: Timeline, narratives: dict[str, dict]) -> dict:
    """The 3-month and 24-month views, with the mockup's narrative templates.

    Three driver notions, each named:
    * `top_weight_line` — the heaviest non-margin line (the 3M narrative's
      "largest cost driver").
    * `mover_24m` — the non-margin line whose own level moved most (points,
      unweighted) over the 24-month window (the 24M narrative's "biggest mover").
    * `top_driver` — the non-margin line with the largest absolute weighted
      move since January 2023 (`weight × (level − 100) / Σ weight`, the
      components table's weighted impact).

    `narratives` maps a series key to its `index_narrative` body
    (`{why3m, why24m}`).

    The index levels (`short_*`, `long_*`, low, high) are the 1-dp values the
    sentences print, and each move is the difference of those printed levels,
    so the numbers on the page and the narrative beside them always agree.
    """
    last = timeline.last_actual
    months = timeline.months
    e = html.escape

    rows = []
    for i, line in enumerate(lines):
        if is_margin(line):
            continue
        s = series.get(line.get("commodity_id")) if line.get("component_type") == INDEX else None
        rows.append({
            "i": i, "label": line["label"], "wt": float(line["weight"]),
            "chg": cs.per_line[i][last] - 100.0,
            "resolved": line.get("component_type") == INDEX,
            "idx": line.get("tag") or "fixed",
            "series_key": s.key if s else None,
            "series": cs.per_line[i],
        })
    ranked = sorted(rows, key=lambda r: -r["wt"])
    top = ranked[:3]

    # ── 3-month view ─────────────────────────────────────────────────────────
    # Levels at 1 dp as printed; the move is taken between the printed levels.
    prev3 = _lvl1(cs.levels[last - 3] if last >= 3 else cs.levels[0])
    cur3 = _lvl1(cs.levels[last])
    chg3_d = cur3 - prev3
    chg3 = float(chg3_d)
    if top:
        t0 = top[0]
        n0 = _cap((narratives.get(t0["series_key"]) or {}).get("why3m")) if t0["series_key"] else None
        moved = "only slightly" if _dir(chg3) == "flat" else f"{_dir(chg3)} {abs(chg3_d):.1f} points"
        t0_move = ("flat vs Jan 2023" if _dir(t0["chg"]) == "flat"
                   else f"{_dir(t0['chg'])} {js_fixed(abs(t0['chg']))}% since Jan 2023")
        html3 = (
            f"Over the last 3 months the should-cost index moved {moved} "
            f"({prev3:.1f} → {cur3:.1f}). The largest cost driver is "
            f"<strong>{e(t0['label'])}</strong> ({js_num(t0['wt'])}% weight), {t0_move}"
            f"{'' if t0['resolved'] else ' — this line has no direct public index and relies on a proxy or fixed estimate'}."
            f"{(' ' + e(n0)) if n0 else ''}"
        )
        if len(top) > 1:
            t1 = top[1]
            html3 += (f" Next is <strong>{e(t1['label'])}</strong> ({js_num(t1['wt'])}%), "
                      f"{_dir(t1['chg'])} {js_fixed(abs(t1['chg']))}%.")
    else:
        html3 = "No weighted cost lines available for this combination."
    signals3 = [{
        "direction": _dir(r["chg"]),
        "label": r["label"],
        "text": (f"{r['label']} ({js_num(r['wt'])}%): "
                 f"{'flat' if _dir(r['chg']) == 'flat' else _dir(r['chg']) + ' ' + js_fixed(abs(r['chg'])) + '%'}"
                 f" vs Jan 2023{'' if r['resolved'] else ' · proxy/estimated'}"),
    } for r in top]

    # ── 24-month view ────────────────────────────────────────────────────────
    w0 = max(0, last - 23)
    hist24 = cs.levels[w0:last + 1]
    start_v, end_v = hist24[0], hist24[-1]
    low_i = high_i = 0
    for i, v in enumerate(hist24):
        if v < hist24[low_i]:
            low_i = i
        if v > hist24[high_i]:
            high_i = i
    # Printed levels again (see `_lvl1`): the net move is end − start at 1 dp.
    start_v, end_v = _lvl1(start_v), _lvl1(end_v)
    low_v, high_v = _lvl1(hist24[low_i]), _lvl1(hist24[high_i])
    low_m, high_m = quarter_label(months[w0 + low_i]), quarter_label(months[w0 + high_i])
    net = end_v - start_v
    if low_i < high_i:
        seq = (f"troughing at {low_v:.1f} in {low_m} before rising to a "
               f"{high_v:.1f} peak in {high_m}")
    else:
        seq = (f"peaking at {high_v:.1f} in {high_m} before falling to a "
               f"{low_v:.1f} low in {low_m}")
    moved24 = sorted(
        ({**r, "move24": r["series"][last] - r["series"][w0]} for r in ranked),
        key=lambda r: -abs(r["move24"]),
    )
    mover = moved24[0] if moved24 else None
    open_q, close_q = quarter_label(months[w0]), quarter_label(months[last])
    if top and mover:
        nm = _cap((narratives.get(mover["series_key"]) or {}).get("why24m")) if mover["series_key"] else None
        same = mover["idx"] == top[0]["idx"]
        html24 = (
            f"Over the 24-month window ({open_q}–{close_q}), the should-cost index ran from "
            f"{start_v:.1f} to {end_v:.1f} ({_signed1(net)} "
            f"points net), {seq}. The single biggest mover over this window was "
            f"<strong>{e(mover['label'])}</strong> ({js_num(mover['wt'])}% weight), which shifted "
            f"{'+' if mover['move24'] >= 0 else ''}{js_fixed(mover['move24'])} points"
            f"{'' if mover['resolved'] else ' (proxy/estimated line)'}"
            f"{' — consistent with its current top-weight position' if same else ', a different line than the current largest weighted driver, worth watching for a change in what is actually setting cost'}."
            f"{(' ' + e(nm)) if nm else ''} Fixed-cost and margin lines are excluded from this "
            "comparison since they are structurally slow-moving by design."
        )
    else:
        html24 = "No weighted cost lines available for this combination."
    first_is_low = low_i < high_i
    signals24 = []
    if top and mover:
        signals24 = [
            {"kind": "open", "period": period_key(months[w0]),
             "text": f"{open_q}: window opens at {start_v:.1f}"},
            {"kind": "trough" if first_is_low else "peak",
             "period": period_key(months[w0 + (low_i if first_is_low else high_i)]),
             "text": (f"{low_m}: trough at {low_v:.1f}" if first_is_low
                      else f"{high_m}: peak at {high_v:.1f}")},
            {"kind": "peak" if first_is_low else "trough",
             "period": period_key(months[w0 + (high_i if first_is_low else low_i)]),
             "text": (f"{high_m}: peak at {high_v:.1f}" if first_is_low
                      else f"{low_m}: trough at {low_v:.1f}")},
            {"kind": "mover", "period": None,
             "text": (f"Biggest 24-month mover: {mover['label']} ({js_num(mover['wt'])}% weight), "
                      f"{'+' if mover['move24'] >= 0 else ''}{js_fixed(mover['move24'])} points")},
            {"kind": "close", "period": period_key(months[last]),
             "text": (f"{close_q}: window closes at {end_v:.1f}, net "
                      f"{_signed1(net)} vs window start")},
        ]

    # ── top_driver: largest |weighted move since Jan 2023| ───────────────────
    driver = None
    if rows:
        best = max(rows, key=lambda r: abs(r["wt"] * r["chg"] / cs.weight_sum))
        driver = {
            "label": best["label"], "series_key": best["series_key"],
            "weight_pct": round(best["wt"], 4),
            "vs_base_pct": round(best["chg"], 2),
            "weighted_impact_pct": round(best["wt"] * best["chg"] / cs.weight_sum, 2),
            "indexed": best["resolved"],
        }

    def _line(r: dict | None, extra: dict | None = None) -> dict | None:
        if r is None:
            return None
        return {"label": r["label"], "series_key": r["series_key"],
                "weight_pct": round(r["wt"], 4), "vs_base_pct": round(r["chg"], 2),
                "indexed": r["resolved"], **(extra or {})}

    # Every level and move below is the 1-dp value the narrative prints.
    return {
        "short_window_months": 3,
        "short_from": float(prev3),
        "short_to": float(cur3),
        "short_points": chg3,
        "short_pct": round(float(chg3_d / prev3 * 100), 2) if prev3 else None,
        "short_direction": _dir(chg3),
        "long_window_months": len(hist24),
        "long_window": {"from": period_key(months[w0]), "to": period_key(months[last]),
                        "label": f"{open_q}–{close_q}"},
        "long_from": float(start_v),
        "long_to": float(end_v),
        "long_points": float(net),
        "long_pct": round(float(net / start_v * 100), 2) if start_v else None,
        "long_low": {"level": float(low_v), "period": period_key(months[w0 + low_i])},
        "long_high": {"level": float(high_v), "period": period_key(months[w0 + high_i])},
        "top_driver": driver,
        "top_weight_line": _line(top[0] if top else None),
        "mover_24m": _line(mover, {"move_points": round(mover["move24"], 2)} if mover else None),
        "narrative_3m": _strip_html(html3),
        "narrative_3m_html": html3,
        "narrative_24m": _strip_html(html24),
        "narrative_24m_html": html24,
        "signals_3m": signals3,
        "signals_24m": signals24,
        "method": "mockup buildDynamicsFor: 3M = last actual vs 3 months earlier; 24M = the "
                  "last 24 actual months; levels rounded to 1 dp as printed, moves = to − from "
                  "of those levels in index points, _pct = points / from × 100",
    }


# ── Seasonality (mockup computeFormulaSeasonality, stored factors) ───────────

LOW_SEASONALITY_NOTE = (
    "Low seasonality — no cost line in this formula carries a strong seasonal price pattern "
    "(or seasonal lines are too small a share of total weight to move the blended index "
    "materially)."
)


def seasonality(lines: list[dict], factors: dict[int, list[float]], weight_sum: float) -> dict:
    """The recipe's 12-month profile: each line's stored seasonal factors
    weighted by its share, 100 for lines with none (fixed, margin, a series
    with no factors), over the recipe's own weight total. The mockup's blend,
    over the generated `index_seasonal_factors` rather than the drop's
    `INDEX_SEASONALITY`."""
    monthly = [0.0] * 12
    contributors = []
    seasonal_weight = 0.0
    for line in lines:
        wt = float(line["weight"])
        arr = factors.get(line.get("commodity_id")) if line.get("component_type") == INDEX else None
        for m in range(12):
            monthly[m] += wt * (arr[m] if arr else 100.0)
        if arr:
            seasonal_weight += wt
            if not is_margin(line):
                contributors.append({"label": line["label"], "weight_pct": wt,
                                     "range": max(arr) - min(arr)})
    profile = [js_round(v / weight_sum * 10) / 10 for v in monthly] if weight_sum else [100.0] * 12
    contributors.sort(key=lambda c: -(c["weight_pct"] * c["range"]))
    overall = max(profile) - min(profile)
    if not contributors or overall < 3:
        note = LOW_SEASONALITY_NOTE
    else:
        topc = contributors[0]
        note = (f"Seasonality is primarily inherited from {topc['label']} "
                f"({js_num(topc['weight_pct'])}% weight).")
    return {
        "factors": profile,
        "months": list(MONTH_ABBR),
        "peak_month": profile.index(max(profile)) + 1,
        "trough_month": profile.index(min(profile)) + 1,
        "spread": round(overall, 2),
        "seasonal_weight_pct": round(100.0 * seasonal_weight / weight_sum, 2) if weight_sum else 0.0,
        "top_contributors": [
            {"label": c["label"], "weight_pct": round(c["weight_pct"], 4),
             "factor_range": round(c["range"], 3)} for c in contributors[:3]],
        "note": note,
        "method": "mockup computeFormulaSeasonality blend (weight-averaged, flat 100 for lines "
                  "without factors), over the stored index_seasonal_factors "
                  "(ratio_to_centred_ma12, generated — not the drop's INDEX_SEASONALITY)",
    }


# ── Volatility (active calibration ladder) ───────────────────────────────────

def dispersion(cs: ComboSeries, timeline: Timeline) -> float | None:
    """Population standard deviation of the month-over-month percent changes
    of the actual levels — `index_dossier.series_dispersion`'s statistic, so
    the value sits on the same ladder the library is calibrated with."""
    hist = cs.levels[:timeline.last_actual + 1]
    changes = [(b - a) / a * 100 for a, b in zip(hist, hist[1:]) if a]
    if len(changes) < 12:
        return None
    return statistics.pstdev(changes)


def volatility_note(pct: int, d: float, months: int) -> str:
    """The mockup's `computeFormulaVolatility` sentences; the statistic is a
    percent, and the ladder is the index library's."""
    base = (f"computed directly from the formula's {months}-month cost history "
            f"(month-over-month standard deviation {js_fixed(d)}%)")
    if pct < 20:
        return f"Low volatility — {base}, placing it in the calmest fifth of the index library's range."
    if pct < 45:
        return f"Low-to-moderate volatility — {base}."
    if pct < 65:
        return f"Moderate volatility — {base}, around the middle of the index library's range."
    if pct < 85:
        return f"Moderate-high volatility — {base}."
    return f"High volatility — {base}, among the most volatile in the index library."


# ── Helpers for callers ──────────────────────────────────────────────────────

def weight_total(lines: Iterable[dict]) -> float:
    return sum(float(l["weight"]) for l in lines)


def pick(d: dict, keys: Iterable[str]) -> dict[str, Any]:
    return {k: d.get(k) for k in keys}
