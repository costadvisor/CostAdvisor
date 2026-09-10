"""Intelligence derivation service (Wave 3, SCRUM-75 / INT-1).

One engine deriving every number an Intelligence ID card shows, from the
weighted recipe plus index history, at **formula × region combo grain** — a
`FormulaRegionCoverage` row, not a product and not a `CostModel`. That is what
the two consumers need: the Intelligence library renders the platform formula
catalogue with region as a selector and has no CostModel behind its tiles at
all, while Portfolio gets here by resolving product → combo.

**The read-path decision, made before building it, as the ticket requires: a
denormalised endpoint with a bounded, documented query budget — not
materialised rows.**

Materialising would need invalidating on an index scrape, a recipe edit *and* a
seasonality recompute: three independent triggers, and this whole line of work
has been "generate, don't store" precisely because a stored derived value drifts
from its inputs. But the naive denormalised shape is worse: calling
`evaluate_weighted_template` once per quarter costs one flatten and one index
read per line **per period**. So the series is evaluated by flattening **once**
and reading each commodity's whole history in **one** query, giving a budget
that is constant in the number of periods:

    1 coverage resolve · 1 flatten (+ chain) · 1 bulk quarterly-value read
    · 1 bulk monthly-value read · 1 seasonal-factor read · 1 calibration read

The single-period maths is not re-derived from scratch — it is the same ratio
and rebasing rule as `evaluate_weighted_template`, and a test pins the two
equal at one period so the fast path can never quietly disagree with the
canonical one.

A batch endpoint exists for the same reason: the library today fires one
`/api/costing/evolution` POST per visible tile, which is tolerable against a
team's cost models and does not scale to the platform catalogue.

**Conventions carried, each because breaking it is silent:**

* **Margin sits inside the 100.** `coverage.margin_pct` is descriptive;
  applying it again double-counts. The level rebases on the recipe's own weight
  sum, so it is exactly 100 at the base period.
* **Un-indexed and margin lines are part of the answer, not noise.** For
  seasonality and volatility they contribute *flat*, normalised by the combo's
  own weight total. That damping is the signal — a combo that is largely fixed
  cost genuinely has a flatter profile than its feedstock does, and dropping
  those lines instead inflates the amplitude of exactly the combos that should
  look calmest.
* **Not everything has an answer, and that is a valid response.** A combo with
  no lines, no base-period anchor or no priceable lines returns nulls with a
  stated reason — never a 500, and never a fabricated zero.

**Read, not derived here:** the trust grade and its inputs are SCRUM-78's
stored field; the volatility ladder is DB-7's; the seasonal factors are
SCRUM-69's. This engine reads each and says which it read.
"""
from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.constants.trust import GRADE_CAVEATS, GRADE_UNRATED
from app.models.formula_template import FormulaRegionCoverage, FormulaTemplate
from app.models.index_data import CommodityIndex, IndexValue
from app.models.index_layer import IndexMonthlyValue
from app.models.index_seasonality import IndexSeasonalFactor
from app.services.formula_resolver import (
    FormulaChainError, flatten_components, resolve_coverage,
)
from app.services.index_dossier import active_calibration, percentile_for

# ── The one window constant ──────────────────────────────────────────────────
#
# The verdict text and the window label are both generated from this, which is
# the bug it exists to prevent: the frontend computes the percentile over
# whatever history it happens to have while hardcoding "24-month", and the
# mockup does the same. Change this and both move together.
CYCLE_WINDOW_QUARTERS = 8

# Short and long change windows, at the quarterly grain the index series has.
SHORT_WINDOW_QUARTERS = 1
LONG_WINDOW_QUARTERS = 8

# Three verdicts split at 70 and 40, plus a fourth case the percentile formula
# cannot express: a series that has not moved at all has no meaningful position
# in its own range.
CYCLE_HIGH = 70.0
CYCLE_LOW = 40.0
# Below this peak-to-trough spread (in index points) the series is treated as
# flat: a 0.3-point range does not have a "top" and a "bottom".
CYCLE_FLAT_SPREAD = 1.0

VERDICT_NEAR_TOP = "near_the_top"
VERDICT_MID = "mid_range"
VERDICT_NEAR_BOTTOM = "near_the_bottom"
VERDICT_FLAT = "flat"


def cycle_window_label() -> str:
    """The label, generated from the same constant as the verdict.

    Always in months, matching the wording the product already uses
    ("24-month") — switching to years at the 12-month boundary would be a
    gratuitous divergence, and the point of this function is that one constant
    drives both the label and the verdict.
    """
    return f"{CYCLE_WINDOW_QUARTERS * 3}-month"


def cycle_verdict(percentile: float | None, spread: float) -> tuple[str, str]:
    """`(verdict, sentence)` — both from the one window constant."""
    window = cycle_window_label()
    if percentile is None or spread < CYCLE_FLAT_SPREAD:
        return VERDICT_FLAT, (
            f"Flat over the {window} window — the series has barely moved, so "
            "there is no meaningful cycle position to read."
        )
    if percentile >= CYCLE_HIGH:
        return VERDICT_NEAR_TOP, (
            f"Near the top of its {window} range ({percentile:.0f}th percentile)."
        )
    if percentile <= CYCLE_LOW:
        return VERDICT_NEAR_BOTTOM, (
            f"Near the bottom of its {window} range ({percentile:.0f}th percentile)."
        )
    return VERDICT_MID, (
        f"Mid-range over the {window} window ({percentile:.0f}th percentile)."
    )


# ── Bulk history reads ───────────────────────────────────────────────────────

def _quarterly_history(
    db: Session, commodity_ids: set[int], region: str
) -> tuple[dict[int, dict[tuple[int, int], float]], dict[int, str]]:
    """Every quarterly value for the given commodities, in one query.

    Region handling mirrors `data_resolver`'s priority in spirit — the exact
    region wins, GLOBAL is the fallback — but is done here in memory rather than
    per (commodity, period), which is what keeps the budget constant in the
    number of periods.
    """
    if not commodity_ids:
        return {}, {}
    rows = (
        db.query(IndexValue.commodity_id, IndexValue.region, IndexValue.year,
                 IndexValue.quarter, IndexValue.value)
        .filter(IndexValue.commodity_id.in_(commodity_ids))
        .all()
    )
    exact: dict[int, dict] = defaultdict(dict)
    fallback: dict[int, dict] = defaultdict(dict)
    for cid, row_region, year, quarter, value in rows:
        target = exact if row_region == region else fallback
        target[cid][(int(year), int(quarter))] = float(value)
    out: dict[int, dict] = {}
    for cid in commodity_ids:
        merged = dict(fallback.get(cid, {}))
        merged.update(exact.get(cid, {}))
        if merged:
            out[cid] = merged

    # The drop's 121 series landed in the **monthly** layer (unit 2), not in the
    # legacy quarterly table — so without this the series would be empty for
    # nearly every catalog combo. Unit 2 verified the quarterly view derives
    # from monthly exactly (0.0000 max difference across all 1,516 quarterly
    # rows), so this is the same numbers at a coarser grain rather than a second
    # source of truth. Only consulted for commodities the quarterly table does
    # not cover, so a legacy series is never overridden.
    source = {cid: "index_values" for cid in out}
    missing = commodity_ids - set(out)
    if missing:
        for cid, points in _monthly_history(db, missing).items():
            by_quarter: dict[tuple[int, int], list[float]] = defaultdict(list)
            for year, month, value in points:
                by_quarter[(year, (month - 1) // 3 + 1)].append(value)
            if by_quarter:
                out[cid] = {p: sum(v) / len(v) for p, v in by_quarter.items()}
                source[cid] = "index_monthly_values"
    return out, source


def _monthly_history(
    db: Session, commodity_ids: set[int]
) -> dict[int, list[tuple[int, int, float]]]:
    """Monthly actuals, in one query.

    Volatility is measured monthly because the **ladder** is monthly (DB-7 fits
    it over month-over-month dispersion). Measuring a combo quarterly and
    placing it on a monthly ladder would report every combo as far more volatile
    than the library, which is a wrong number rather than a missing one.
    """
    if not commodity_ids:
        return {}
    rows = (
        db.query(IndexMonthlyValue.commodity_id, IndexMonthlyValue.year,
                 IndexMonthlyValue.month, IndexMonthlyValue.value)
        .filter(IndexMonthlyValue.commodity_id.in_(commodity_ids),
                IndexMonthlyValue.kind == "actual")
        .all()
    )
    out: dict[int, list] = defaultdict(list)
    for cid, year, month, value in rows:
        out[cid].append((int(year), int(month), float(value)))
    for cid in out:
        out[cid].sort()
    return dict(out)


def _seasonal_factors(
    db: Session, commodity_ids: set[int]
) -> dict[int, list[float]]:
    """SCRUM-69's generated factors, in one query. Never imported."""
    if not commodity_ids:
        return {}
    rows = (
        db.query(IndexSeasonalFactor.commodity_id, IndexSeasonalFactor.month,
                 IndexSeasonalFactor.factor)
        .filter(IndexSeasonalFactor.commodity_id.in_(commodity_ids),
                IndexSeasonalFactor.region.is_(None))
        .all()
    )
    by_series: dict[int, dict[int, float]] = defaultdict(dict)
    for cid, month, factor in rows:
        by_series[cid][int(month)] = float(factor)
    return {
        cid: [months[m] for m in range(1, 13)]
        for cid, months in by_series.items()
        if len(months) == 12
    }


# ── The payload ──────────────────────────────────────────────────────────────

@dataclass
class Intelligence:
    template_id: uuid.UUID
    template_code: str | None
    region_requested: str
    coverage_region: str | None
    evaluable: bool
    reason: str | None = None

    base_price: float | None = None
    currency: str | None = None
    base_year: int | None = None
    base_quarter: int | None = None

    # Base 100 at the combo's base period, over a pinned window.
    series: list[dict] = field(default_factory=list)
    components: list[dict] = field(default_factory=list)
    change: dict = field(default_factory=dict)
    cycle: dict = field(default_factory=dict)
    seasonality: dict = field(default_factory=dict)
    volatility: dict = field(default_factory=dict)
    # Read from SCRUM-78's stored field, never recomputed here.
    trust: dict = field(default_factory=dict)
    data_gaps: list[dict] = field(default_factory=list)
    # Which store the levels came from, and a note when that is not the store
    # the costing engine reads. Stated rather than hidden: this engine can see
    # the drop's monthly series and `data_resolver` cannot, so the two will
    # disagree on exactly those combos until that tier is added there.
    value_sources: dict = field(default_factory=dict)


def _level_at(lines, history, base_period, period) -> tuple[float | None, list[dict]]:
    """The rebased index level for one period, and the lines that had no data.

    The same rule as `evaluate_weighted_template`:
    `100 × Σ(weight × ratio) / Σ(weight)`. Rebasing on the recipe's own weight
    sum is what makes the level exactly 100 at the base period — the catalog
    legitimately sums to 100 *including* its margin line.
    """
    weight_sum = sum(l["effective_weight_pct"] for l in lines)
    if weight_sum <= 0:
        return None, []
    weighted = 0.0
    gaps = []
    for line in lines:
        ratio = 1.0
        if line["component_type"] == "index" and line["commodity_id"]:
            values = history.get(line["commodity_id"], {})
            base = values.get(base_period)
            current = values.get(period)
            if base and current:
                ratio = current / base
            else:
                gaps.append({"line": line["name"],
                             "commodity_id": line["commodity_id"]})
        weighted += line["effective_weight_pct"] * ratio
    return 100.0 * weighted / weight_sum, gaps


def _quarter_range(base: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    out = []
    year, quarter = base
    while (year, quarter) <= end:
        out.append((year, quarter))
        quarter += 1
        if quarter > 4:
            year, quarter = year + 1, 1
    return out


def _pct_change(series: list[dict], quarters: int) -> float | None:
    if len(series) <= quarters:
        return None
    latest = series[-1]["level"]
    earlier = series[-1 - quarters]["level"]
    if not earlier:
        return None
    return round((latest - earlier) / earlier * 100, 2)


def derive(
    db: Session,
    template_id: uuid.UUID,
    region: str,
    team_id: uuid.UUID | None = None,
) -> Intelligence:
    """Every derived number for one combo.

    Returns a payload with a stated reason rather than raising, for every shape
    the catalogue actually contains: no lines, no anchor, nothing priceable.
    """
    template = db.query(FormulaTemplate).filter(
        FormulaTemplate.id == template_id).first()
    out = Intelligence(
        template_id=template_id,
        template_code=template.code if template else None,
        region_requested=region, coverage_region=None, evaluable=False,
    )
    if template is None:
        out.reason = "unknown formula template"
        return out

    coverage, coverage_region = resolve_coverage(db, template_id, region)
    out.coverage_region = coverage_region
    if coverage is not None:
        out.base_price = float(coverage.base_price) if coverage.base_price is not None else None
        out.currency = coverage.currency
        out.base_year, out.base_quarter = coverage.base_year, coverage.base_quarter
        # SCRUM-78's stored grade, read not recomputed — and the payload echoes
        # which proxy-status column that derivation believed, rather than
        # picking its own.
        inputs = coverage.trust_inputs or {}
        out.trust = {
            "grade": coverage.trust_grade,
            # The customer-facing claim ships with the state that produced it,
            # the same way the editorial provenance badge does. A screen that
            # writes its own caveat can drift from the grade beside it, and the
            # mockup's unconditional "not reviewed by an expert" is what that
            # drift looks like: it caveats combos nobody questioned and vouches
            # for none of them.
            "caveat": GRADE_CAVEATS.get(coverage.trust_grade or GRADE_UNRATED),
            "needs_review": coverage.needs_review,
            "reviewed_at": coverage.reviewed_at,
            "inputs": inputs,
            "proxy_status_source": inputs.get("proxy_status_source"),
            "source": "formula_region_coverage.trust_grade (SCRUM-78)",
            # Named separately with a stated relationship, because
            # `coverage_tier` already holds a third vocabulary and
            # `data_confidence` a fourth axis about pricing provenance.
            "coverage_tier": coverage.coverage_tier,
            "proxy_density_tier": coverage.proxy_density_tier,
        }

    try:
        lines = flatten_components(db, template_id, region=region)
    except FormulaChainError as exc:
        out.reason = f"recipe chain is broken: {exc}"
        return out

    if not lines:
        out.reason = "no weighted lines"
        return out
    if coverage is None:
        out.reason = "no regional pricing (coverage) for this formula"
        return out
    if coverage.base_year is None or coverage.base_quarter is None:
        out.reason = "coverage has no base period anchor"
        return out

    commodity_ids = {l["commodity_id"] for l in lines if l["commodity_id"]}
    quarterly, value_source = _quarterly_history(db, commodity_ids, region)
    base_period = (coverage.base_year, coverage.base_quarter)

    # The window runs from the anchor to the latest period any line has.
    latest = max(
        (p for values in quarterly.values() for p in values), default=base_period)
    periods = _quarter_range(base_period, max(latest, base_period))

    gap_lines: dict[str, dict] = {}
    for period in periods:
        level, gaps = _level_at(lines, quarterly, base_period, period)
        if level is None:
            out.reason = "line weights sum to zero"
            return out
        out.series.append({"year": period[0], "quarter": period[1],
                           "level": round(level, 4)})
        for gap in gaps:
            gap_lines.setdefault(gap["line"], {
                **gap,
                "reason": "no index value at this period — line rides flat (ratio 1.0)",
            })
    out.data_gaps = list(gap_lines.values())
    out.evaluable = True

    # ── Components at the latest period ─────────────────────────────────────
    weight_sum = sum(l["effective_weight_pct"] for l in lines)
    current = periods[-1]
    names = {
        c.id: (c.commodity_key or c.name)
        for c in db.query(CommodityIndex).filter(
            CommodityIndex.id.in_(commodity_ids)).all()
    } if commodity_ids else {}
    for line in lines:
        ratio, base_value, current_value, has_data = 1.0, None, None, True
        if line["component_type"] == "index" and line["commodity_id"]:
            values = quarterly.get(line["commodity_id"], {})
            base_value = values.get(base_period)
            current_value = values.get(current)
            if base_value and current_value:
                ratio = current_value / base_value
            else:
                has_data = False
        contribution = 100.0 * line["effective_weight_pct"] * ratio / weight_sum
        out.components.append({
            "name": line["name"],
            "component_type": line["component_type"],
            "commodity_id": line["commodity_id"],
            "commodity_key": names.get(line["commodity_id"]),
            "weight_pct": round(line["effective_weight_pct"], 4),
            "is_proxy": line["is_proxy"],
            "depth": line["depth"],
            "line_region": line["line_region"],
            "base_value": base_value,
            "current_value": current_value,
            "ratio": round(ratio, 6),
            "has_data": has_data,
            # Which store this line's values came from. `index_monthly_values`
            # means the drop's series, which `data_resolver` cannot see yet —
            # see `value_sources` on the payload.
            "value_source": value_source.get(line["commodity_id"])
            if line["commodity_id"] else None,
            "contribution_pct": round(contribution, 4),
            "contribution_abs": (round(out.base_price * contribution / 100.0, 4)
                                 if out.base_price is not None else None),
        })

    if out.base_price is None:
        # Still a real answer: an index level with no money behind it. The
        # series, cycle and seasonality are all levels, so they stand.
        out.reason = "no base price anchor — index level only"

    counts: dict[str, int] = defaultdict(int)
    for src in value_source.values():
        counts[src] += 1
    # `data_resolver` gained a monthly tier (unit 12 follow-up), so reading the
    # monthly store is no longer a divergence from the costing engine — both
    # sides now see it, and both aggregate a quarter as the mean of its actual
    # months. The field stays rather than being deleted: it is the one place a
    # caller can see which store a level came from, and a future second store
    # would need exactly this seam again.
    out.value_sources = {
        "by_store": dict(counts),
        "matches_costing_engine": True,
        "note": (
            "some lines resolve through the drop's monthly series; the costing "
            "engine reads that store too, at the same quarter-mean of actual "
            "months, so the two agree"
            if "index_monthly_values" in counts else
            "every line resolves through the same store the costing engine reads"
        ),
    }

    out.change = {
        "short_window_quarters": SHORT_WINDOW_QUARTERS,
        "long_window_quarters": LONG_WINDOW_QUARTERS,
        "short_pct": _pct_change(out.series, SHORT_WINDOW_QUARTERS),
        "long_pct": _pct_change(out.series, LONG_WINDOW_QUARTERS),
    }
    out.cycle = _cycle(out.series)
    out.seasonality = _seasonality(db, lines, weight_sum, commodity_ids)
    out.volatility = _volatility(db, lines, weight_sum, commodity_ids)
    return out


def _cycle(series: list[dict]) -> dict:
    window = series[-CYCLE_WINDOW_QUARTERS:]
    levels = [p["level"] for p in window]
    low, high = (min(levels), max(levels)) if levels else (None, None)
    spread = (high - low) if levels else 0.0
    percentile = None
    if levels and spread > 0:
        percentile = (levels[-1] - low) / spread * 100
    verdict, sentence = cycle_verdict(percentile, spread)
    return {
        # The label and the verdict come from the one constant — that agreement
        # is the whole point, and a test asserts it.
        "window_quarters": CYCLE_WINDOW_QUARTERS,
        "window_label": cycle_window_label(),
        "periods_used": len(window),
        "low": round(low, 4) if low is not None else None,
        "high": round(high, 4) if high is not None else None,
        "spread": round(spread, 4),
        "percentile": round(percentile, 2) if percentile is not None else None,
        "verdict": verdict,
        "sentence": sentence,
    }


def _seasonality(db: Session, lines, weight_sum: float, commodity_ids) -> dict:
    """A 12-month profile for the combo, blended over **every** line.

    Lines with no seasonal factor — fixed costs, margin, anything unresolved —
    contribute flat 100 and are normalised by the combo's own weight total. That
    damping is the signal: a combo that is largely fixed cost genuinely has a
    flatter profile than its feedstock, and dropping those lines instead would
    inflate the amplitude of exactly the combos that should look calmest.
    """
    factors = _seasonal_factors(db, commodity_ids)
    blended = [0.0] * 12
    seasonal_weight = 0.0
    for line in lines:
        weight = line["effective_weight_pct"]
        series = factors.get(line["commodity_id"]) if line["commodity_id"] else None
        if series:
            seasonal_weight += weight
        for month in range(12):
            blended[month] += weight * (series[month] if series else 100.0)
    profile = [round(v / weight_sum, 3) for v in blended] if weight_sum else [100.0] * 12
    spread = round(max(profile) - min(profile), 3)
    return {
        "factors": profile,
        "peak_month": profile.index(max(profile)) + 1,
        "trough_month": profile.index(min(profile)) + 1,
        "spread": spread,
        # How much of the recipe actually carries a seasonal profile — the rest
        # is what damps the amplitude.
        "seasonal_weight_pct": round(100.0 * seasonal_weight / weight_sum, 2)
        if weight_sum else 0.0,
        "source": "index_seasonal_factors (SCRUM-69, generated not imported)",
    }


def _volatility(db: Session, lines, weight_sum: float, commodity_ids) -> dict:
    """The combo's monthly dispersion, placed on DB-7's stored ladder.

    Blended monthly rather than measured on the quarterly series: the ladder is
    fitted over month-over-month dispersion, so a quarterly measurement placed
    on it would report every combo as far more volatile than the library — a
    wrong number rather than a missing one. Lines with no monthly series ride
    flat, the same damping rule as seasonality.
    """
    monthly = _monthly_history(db, commodity_ids)
    calibration = active_calibration(db)
    out = {
        "dispersion": None,
        "percentile": None,
        "calibration_id": None,
        "calibration_computed_at": None,
        "method": None,
        "reason": None,
        "monthly_weight_pct": 0.0,
    }
    if not monthly:
        out["reason"] = ("no monthly history behind any line — dispersion is not "
                         "measurable, which is not the same as calm")
        return out

    # Blend the component monthly series into one combo series, on the months
    # every contributing line actually covers.
    covered = [l for l in lines if l["commodity_id"] in monthly]
    monthly_weight = sum(l["effective_weight_pct"] for l in covered)
    out["monthly_weight_pct"] = round(100.0 * monthly_weight / weight_sum, 2) \
        if weight_sum else 0.0

    by_month: dict[tuple[int, int], float] = {}
    shared = None
    per_line: dict[int, dict] = {}
    for line in covered:
        points = {(y, m): v for y, m, v in monthly[line["commodity_id"]]}
        per_line[line["component_id"]] = points
        shared = set(points) if shared is None else (shared & set(points))
    if not shared or len(shared) < 13:
        out["reason"] = ("fewer than 13 months shared across the recipe's lines — "
                         "not enough to measure dispersion")
        return out

    base_month = min(shared)
    for month in sorted(shared):
        weighted = 0.0
        for line in lines:
            points = per_line.get(line["component_id"])
            ratio = 1.0
            if points:
                base = points.get(base_month)
                value = points.get(month)
                if base and value:
                    ratio = value / base
            weighted += line["effective_weight_pct"] * ratio
        by_month[month] = 100.0 * weighted / weight_sum

    values = [by_month[m] for m in sorted(by_month)]
    changes = [(b - a) / a * 100 for a, b in zip(values, values[1:]) if a]
    if len(changes) < 12:
        out["reason"] = "not enough month-over-month changes to measure dispersion"
        return out
    dispersion = statistics.pstdev(changes)
    out["dispersion"] = round(dispersion, 4)

    if calibration is None:
        out["reason"] = ("no active volatility calibration — DB-7's recompute has "
                         "not been run")
        return out
    out["percentile"] = percentile_for(dispersion, calibration)
    out["calibration_id"] = calibration.id
    out["calibration_computed_at"] = calibration.computed_at
    out["method"] = calibration.method
    return out


# ── Product → combo ──────────────────────────────────────────────────────────

@dataclass
class ComboRef:
    template_id: uuid.UUID
    region: str
    # How the product got here, so a caller can see the resolution rather than
    # trusting it.
    via: str


def combo_for_cost_model(db: Session, cost_model) -> ComboRef | None:
    """Resolve a team's product to the combo the library derives.

    Two routes, in order: the cost model's own formula version may be linked to
    a coverage row (Scrum 28b's `source_coverage_id`), which names the combo
    exactly; otherwise the product's catalog template plus the cost model's
    region. The product is never the thing being derived.
    """
    version = cost_model.current_formula
    if version is not None and version.source_coverage_id:
        coverage = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.id == version.source_coverage_id).first()
        if coverage is not None:
            return ComboRef(template_id=coverage.template_id,
                            region=coverage.region,
                            via="formula_version.source_coverage_id")
    product = cost_model.product
    if product is not None and product.formula_template_id:
        return ComboRef(template_id=product.formula_template_id,
                        region=cost_model.region,
                        via="product.formula_template_id + cost_model.region")
    return None
