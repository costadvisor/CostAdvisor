"""Seasonal-factor computation + note rendering (Wave 3, SCRUM-69).

The method, confirmed against the drop rather than assumed: **ratio to a centred
12-month moving average, averaged by calendar month, normalised so the twelve
factors mean 100.** The drop's own notes name it, and it reproduces the
published factors within 0.05 on 46 of the 48 series that have actual history.

The season note is **rendered from the factors**, never stored. That is the
strongest available form of "text and number cannot disagree": there is no
second copy to drift. The rendered wording follows the drop's own three tiers,
whose boundaries were measured off the published data — Low 0.0–2.6, Modest
3.0–7.7, Meaningful 8.0–55.9 point spreads — so the tier cut sits at 3 and 8.
"""
from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.index_layer import IndexMonthlyValue
from app.models.index_seasonality import (
    METHOD_RATIO_TO_CENTRED_MA12, MONTH_NAMES, IndexSeasonalFactor,
)

# Half-width of the centred moving average. 6 either side of the point gives the
# classic 13-term average with half weights on the ends.
MA_HALF_WIDTH = 6

# A centred 12-month average needs a year of context on each side, so a fit
# needs at least two full years before every calendar month has an interior
# observation. Below that the "seasonal" pattern is one year of noise.
MIN_MONTHS = 24

# Tier boundaries on the peak-to-trough spread, measured off the drop's own
# published factors and its own tier wording.
TIER_LOW_MAX = 3.0
TIER_MODEST_MAX = 8.0
# The claim the "Low" note makes. Kept beside the boundary it depends on, so a
# change to one cannot silently falsify the other.
LOW_TIER_DEVIATION_CLAIM = 3


def compute_factors(points: list[tuple[int, int, float]]) -> list[float] | None:
    """Twelve seasonal factors from monthly observations, or None.

    `points` is `[(year, month, value), ...]`. None means the series cannot
    support a seasonal fit — which is a different answer from "no seasonality"
    and has to stay distinguishable, because a flat 100 for every month is a
    real finding and an unfittable series is not.
    """
    ordered = sorted(points)
    values = [v for _, _, v in ordered]
    months = [m for _, m, _ in ordered]
    if len(values) < MIN_MONTHS:
        return None

    ratios: dict[int, list[float]] = defaultdict(list)
    for i in range(MA_HALF_WIDTH, len(values) - MA_HALF_WIDTH):
        window = values[i - MA_HALF_WIDTH: i + MA_HALF_WIDTH + 1]
        # 13-term centred average with half weight on the two ends — the
        # standard way to centre an even-length window on a single month.
        moving_average = (
            sum(window[1:-1]) + (window[0] + window[-1]) / 2
        ) / (2 * MA_HALF_WIDTH)
        if moving_average:
            ratios[months[i]].append(values[i] / moving_average)

    # Every calendar month needs at least one interior observation, or the
    # missing months would have to be filled with 100 and the profile would
    # claim a flat month it never measured.
    if len(ratios) < 12:
        return None

    averages = [statistics.fmean(ratios[m]) for m in range(1, 13)]
    grand = statistics.fmean(averages)
    if not grand:
        return None
    # Deliberately unrounded. The column is Numeric(7,3), so storage rounds
    # once; rounding here as well made a comparison against the drop's
    # 1-decimal factors double-round, and four series drifted by exactly one
    # tick for no better reason than that.
    return [a / grand * 100 for a in averages]


# ── The rendered note ────────────────────────────────────────────────────────

@dataclass
class SeasonProfile:
    commodity_id: int
    region: str | None
    factors: list[float]
    method: str
    window_months: int
    computed_at: datetime | None
    # All derived from the twelve factors — never stored, so text and number
    # cannot disagree.
    peak_month: int
    trough_month: int
    spread: float
    tier: str
    note: str


def tier_for(spread: float) -> str:
    if spread < TIER_LOW_MAX:
        return "low"
    if spread < TIER_MODEST_MAX:
        return "modest"
    return "meaningful"


def render_season_note(factors: list[float], window_months: int) -> str:
    """The prose, rendered from the numbers it describes.

    Deliberately does not repeat the drop's "42 months of real index history"
    claim as a constant: it states the window the fit actually used, which for
    30 of the drop's 78 published series would have been zero.
    """
    spread = round(max(factors) - min(factors), 1)
    tier = tier_for(spread)
    peak = MONTH_NAMES[factors.index(max(factors))]
    trough = MONTH_NAMES[factors.index(min(factors))]
    basis = (
        f"computed from {window_months} months of index history "
        "(ratio-to-moving-average method)"
    )
    if tier == "low":
        return (
            f"Low seasonality — {basis}; no calendar month deviates more than "
            f"{LOW_TIER_DEVIATION_CLAIM} points from the annual average "
            f"(a {spread}-point peak-to-trough spread)."
        )
    label = "Modest" if tier == "modest" else "Meaningful"
    return (
        f"{label} seasonality — {basis}. Typically highest around {peak}, "
        f"lowest around {trough} (a {spread}-point spread)."
    )


def _profile(rows: list[IndexSeasonalFactor]) -> SeasonProfile | None:
    if len(rows) != 12:
        return None
    ordered = sorted(rows, key=lambda r: r.month)
    factors = [float(r.factor) for r in ordered]
    spread = round(max(factors) - min(factors), 1)
    return SeasonProfile(
        commodity_id=ordered[0].commodity_id,
        region=ordered[0].region,
        factors=factors,
        method=ordered[0].method,
        window_months=ordered[0].window_months,
        computed_at=ordered[0].computed_at,
        peak_month=factors.index(max(factors)) + 1,
        trough_month=factors.index(min(factors)) + 1,
        spread=spread,
        tier=tier_for(spread),
        note=render_season_note(factors, ordered[0].window_months),
    )


def profile_for(
    db: Session, commodity_id: int, region: str | None = None
) -> SeasonProfile | None:
    """The stored factors for a series, plus everything derivable from them."""
    q = db.query(IndexSeasonalFactor).filter(
        IndexSeasonalFactor.commodity_id == commodity_id)
    q = q.filter(IndexSeasonalFactor.region.is_(None)) if region is None \
        else q.filter(IndexSeasonalFactor.region == region)
    rows = q.all()
    if len(rows) != 12 and region is not None:
        # A region with no override of its own falls back to the series-wide
        # profile, the same convention as the dossier read.
        return profile_for(db, commodity_id, None)
    return _profile(rows)


# ── Recompute ────────────────────────────────────────────────────────────────

@dataclass
class SeriesResult:
    commodity_id: int
    status: str            # "computed" | "unchanged" | "insufficient"
    window_months: int = 0
    reason: str | None = None


@dataclass
class RecomputeReport:
    computed: int = 0
    unchanged: int = 0
    insufficient: int = 0
    results: list[SeriesResult] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return self.computed

    def render(self) -> str:
        lines = [
            "Seasonal factors",
            "",
            f"  computed     {self.computed:5d}",
            f"  unchanged    {self.unchanged:5d}",
            # Reported, never filled with a flat 100: a series that cannot
            # support a fit is not a series with no seasonality.
            f"  insufficient {self.insufficient:5d}  (fewer than "
            f"{MIN_MONTHS} monthly actuals, or a calendar month with no "
            "interior observation)",
        ]
        return "\n".join(lines)


def _monthly_actuals(db: Session, commodity_id: int) -> list[tuple[int, int, float]]:
    return [
        (int(y), int(m), float(v))
        for y, m, v in db.query(
            IndexMonthlyValue.year, IndexMonthlyValue.month, IndexMonthlyValue.value
        ).filter(
            IndexMonthlyValue.commodity_id == commodity_id,
            IndexMonthlyValue.kind == "actual",
        ).all()
    ]


def recompute_series(
    db: Session, commodity_id: int, region: str | None = None
) -> SeriesResult:
    """Upsert the twelve factors for one series. Does not commit.

    Idempotent by `(commodity_id, region, month)`: a re-run with unchanged
    inputs rewrites nothing and reports `unchanged`, which is what makes it safe
    to wire into a nightly job.
    """
    points = _monthly_actuals(db, commodity_id)
    factors = compute_factors(points)
    existing = {
        r.month: r for r in db.query(IndexSeasonalFactor).filter(
            IndexSeasonalFactor.commodity_id == commodity_id,
            IndexSeasonalFactor.region.is_(None) if region is None
            else IndexSeasonalFactor.region == region,
        ).all()
    }

    if factors is None:
        # Stale rows from a previous fit are removed rather than left behind: a
        # series whose history was corrected downward should stop claiming a
        # seasonal profile it can no longer support.
        for row in existing.values():
            db.delete(row)
        if existing:
            db.flush()
        return SeriesResult(
            commodity_id=commodity_id, status="insufficient",
            reason=(f"{len(points)} monthly actuals — a centred 12-month average "
                    f"needs at least {MIN_MONTHS} with every calendar month "
                    "represented in the interior"),
        )

    window = len(points)
    unchanged = (
        len(existing) == 12
        and all(
            existing[m].method == METHOD_RATIO_TO_CENTRED_MA12
            and existing[m].window_months == window
            # Compared at the column's own precision (Numeric(7,3)): the
            # computed value is unrounded, so an exact equality check would
            # report every re-run as changed.
            and abs(float(existing[m].factor) - round(factors[m - 1], 3)) < 5e-4
            for m in range(1, 13)
        )
    )
    if unchanged:
        return SeriesResult(commodity_id=commodity_id, status="unchanged",
                            window_months=window)

    now = datetime.now(timezone.utc)
    for month in range(1, 13):
        row = existing.get(month)
        if row is None:
            db.add(IndexSeasonalFactor(
                commodity_id=commodity_id, region=region, month=month,
                factor=factors[month - 1], method=METHOD_RATIO_TO_CENTRED_MA12,
                window_months=window, computed_at=now,
            ))
        else:
            row.factor = factors[month - 1]
            row.method = METHOD_RATIO_TO_CENTRED_MA12
            row.window_months = window
            row.computed_at = now
    db.flush()
    return SeriesResult(commodity_id=commodity_id, status="computed",
                        window_months=window)


def recompute_all(db: Session) -> RecomputeReport:
    """Recompute every series that has monthly actuals. Does not commit."""
    ids = [
        cid for (cid,) in
        db.query(IndexMonthlyValue.commodity_id)
        .filter(IndexMonthlyValue.kind == "actual")
        .group_by(IndexMonthlyValue.commodity_id)
        .all()
    ]
    report = RecomputeReport()
    for cid in ids:
        result = recompute_series(db, cid)
        report.results.append(result)
        if result.status == "computed":
            report.computed += 1
        elif result.status == "unchanged":
            report.unchanged += 1
        else:
            report.insufficient += 1
    return report
