"""Scrum 70 (Part 1) — index projection service.

Fits (or explicitly declines to fit) a forward trajectory for one price
series. The series grain is (commodity_id, region) directly off IndexValue —
there is no separate PriceSeries entity in this repo, and a series is
exactly that pair, with no cross-region aggregation and no GLOBAL fallback
(fitting against a blended grain would double- or mis-count the regions that
share a commodity).

Method (OLS linear trend with a residual-based prediction interval) is
deliberately simple and fully deterministic — no numpy/scipy dependency, no
external forecasting library. The ticket is explicit the method itself is
not the contested part; what matters is that the output is a stored,
vintaged run with honest uncertainty, not a placeholder like
ForecastArea.jsx's flat ±1.5% band.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import sqrt
from statistics import mean

from sqlalchemy import asc
from sqlalchemy.orm import Session

from app.models.index_data import IndexValue
from app.models.index_projection import IndexProjectionRun, IndexProjectionPoint

# A fit needs at least 2 residual degrees of freedom (n - 2) to produce a
# meaningful residual variance; below this we hold rather than fabricate a fit.
MIN_HISTORY_FOR_FIT = 4
# 0 or 1 actual points is no anchor at all — not even a flat hold has evidence.
NO_HISTORY_MAX_POINTS = 1
# Fit on at most the trailing 3 years so an old, no-longer-relevant regime
# doesn't dominate a recent trend.
LOOKBACK_QUARTERS = 12
# How many of the most recent points to check for a dead-flat tail — this is
# the "last actual repeated forward" shape the ticket calls out explicitly.
FLAT_TAIL_K = 4
# Relative spread below this (on either the tail or the whole window) counts
# as degenerate/flat, not a real trend to extrapolate.
FLAT_CV_THRESHOLD = 0.005
DEFAULT_HORIZON_QUARTERS = 4
# ~80% two-sided prediction interval. A z-multiplier, not a t-critical value —
# this repo has no scipy dependency to look up a t-distribution quantile for
# small n; documented simplification, understates uncertainty slightly at
# low n (the fit already requires n>=4, so the understatement is bounded).
PREDICTION_Z = 1.2816


def _qidx(year: int, quarter: int) -> int:
    """Evenly-spaced integer time axis. Correct across quarter gaps — unlike
    the year*10+quarter key used elsewhere in the codebase for display
    ordering only, this is meant to be arithmetic (x+1 == next quarter)."""
    return year * 4 + (quarter - 1)


def _from_qidx(q: int) -> tuple[int, int]:
    return q // 4, (q % 4) + 1


def _load_history(db: Session, commodity_id: int, region: str) -> list[tuple[int, int, float]]:
    rows = (
        db.query(IndexValue)
        .filter(IndexValue.commodity_id == commodity_id, IndexValue.region == region)
        .order_by(asc(IndexValue.year), asc(IndexValue.quarter))
        .all()
    )
    return [(r.year, r.quarter, float(r.value)) for r in rows]


def _coefficient_of_variation(values: list[float]) -> float:
    m = mean(values)
    if m == 0:
        return 0.0 if all(v == 0 for v in values) else float("inf")
    if len(values) < 2:
        return 0.0
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return sqrt(var) / abs(m)


def _is_flat(values: list[float]) -> bool:
    """OR of two checks so either shape trips it: a dead-flat recent tail
    riding on otherwise-volatile older history (the tail check), or a series
    that simply has no variance at all across the whole lookback window (the
    whole-window check). A single whole-window CV would miss the first case."""
    tail = values[-FLAT_TAIL_K:]
    if len(tail) >= 2 and _coefficient_of_variation(tail) < FLAT_CV_THRESHOLD:
        return True
    return _coefficient_of_variation(values) < FLAT_CV_THRESHOLD


def _fit_ols(xs: list[int], ys: list[float]) -> tuple[float, float, float, float]:
    """Pure-Python simple linear regression. Returns (intercept, slope,
    residual_std, sxx) — sxx (sum of squared x-deviations) is needed again by
    the caller to build the per-step prediction interval."""
    n = len(xs)
    x_mean = mean(xs)
    y_mean = mean(ys)
    sxx = sum((x - x_mean) ** 2 for x in xs)
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = y_mean - b * x_mean
    sse = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    residual_std = sqrt(sse / (n - 2))
    return a, b, residual_std, sxx


def run_projection(
    db: Session,
    commodity_id: int,
    region: str,
    horizon_quarters: int = DEFAULT_HORIZON_QUARTERS,
) -> IndexProjectionRun:
    """Fit (or mark hold/no-history for) the (commodity_id, region) series.

    Always inserts a NEW IndexProjectionRun — re-running is a new vintage,
    never an overwrite of a prior one.
    """
    history = _load_history(db, commodity_id, region)
    n = len(history)
    now = datetime.now(timezone.utc)

    if n <= NO_HISTORY_MAX_POINTS:
        run = IndexProjectionRun(
            commodity_id=commodity_id,
            region=region,
            vintage_at=now,
            status="no_history",
            method="no_history",
            history_points_used=n,
            horizon_quarters=horizon_quarters,
        )
        db.add(run)
        db.commit()
        return run

    lookback = history[-LOOKBACK_QUARTERS:]
    values = [v for _, _, v in lookback]
    last_year, last_quarter, last_value = history[-1]
    from_year, from_quarter, _ = lookback[0]

    if n < MIN_HISTORY_FOR_FIT:
        status, method = "hold", "hold_insufficient_points"
    elif _is_flat(values):
        status, method = "hold", "hold_flat_variance"
    else:
        status, method = "fitted", "ols_linear_trend"

    run = IndexProjectionRun(
        commodity_id=commodity_id,
        region=region,
        vintage_at=now,
        status=status,
        method=method,
        history_from_year=from_year,
        history_from_quarter=from_quarter,
        history_to_year=last_year,
        history_to_quarter=last_quarter,
        history_points_used=len(lookback),
        horizon_quarters=horizon_quarters,
    )
    db.add(run)
    db.flush()

    last_x = _qidx(last_year, last_quarter)

    if status == "hold":
        # Flat-carry the last actual value — this is the "hold, not a fitted
        # view" shape the ticket requires to be distinguishable downstream
        # via `status`/`method`, not by looking identical to a real fit.
        for h in range(1, horizon_quarters + 1):
            y, q = _from_qidx(last_x + h)
            db.add(IndexProjectionPoint(run_id=run.id, year=y, quarter=q, value=last_value))
    else:
        xs = [_qidx(y, q) for y, q, _ in lookback]
        a, b, residual_std, sxx = _fit_ols(xs, values)
        run.residual_std = residual_std
        x_mean = mean(xs)
        for h in range(1, horizon_quarters + 1):
            x0 = last_x + h
            fitted = a + b * x0
            se = residual_std * sqrt(1 + 1 / len(xs) + ((x0 - x_mean) ** 2) / sxx)
            ci = PREDICTION_Z * se
            y, q = _from_qidx(x0)
            db.add(
                IndexProjectionPoint(
                    run_id=run.id, year=y, quarter=q,
                    value=fitted, ci_lo=fitted - ci, ci_hi=fitted + ci,
                )
            )

    db.commit()
    return run


def project_all_series(db: Session, horizon_quarters: int = DEFAULT_HORIZON_QUARTERS) -> dict:
    """Refresh the projection vintage for every (commodity, region) pair that
    has at least one IndexValue row. Shared by the on-demand admin endpoint
    and the weekly Celery task so there's one loop, not two."""
    pairs = db.query(IndexValue.commodity_id, IndexValue.region).distinct().all()
    counts = {"fitted": 0, "hold": 0, "no_history": 0}
    for commodity_id, region in pairs:
        run = run_projection(db, commodity_id, region, horizon_quarters=horizon_quarters)
        counts[run.status] = counts.get(run.status, 0) + 1
    return {"series_projected": len(pairs), **counts}


def latest_projection(db: Session, commodity_id: int, region: str) -> IndexProjectionRun | None:
    return (
        db.query(IndexProjectionRun)
        .filter(IndexProjectionRun.commodity_id == commodity_id, IndexProjectionRun.region == region)
        .order_by(IndexProjectionRun.vintage_at.desc())
        .first()
    )


def get_projection_point(db: Session, run_id: int, year: int, quarter: int) -> IndexProjectionPoint | None:
    return (
        db.query(IndexProjectionPoint)
        .filter(
            IndexProjectionPoint.run_id == run_id,
            IndexProjectionPoint.year == year,
            IndexProjectionPoint.quarter == quarter,
        )
        .first()
    )
