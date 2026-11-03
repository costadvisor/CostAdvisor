"""Series health: one answer to "is this index usable, and why not" (Jira 208).

Four readers each answer that question their own way today, from different
slices of the data, so one series can look fine on one screen and broken on
another:

    costing          quarterly -> monthly -> carry-forward with no age limit
                     (`data_resolver._resolve_index_value_detailed`)
    resolution       monthly actuals only (`resolution._code_blocker`, until
                     it adopted this module)
    radar            type-code resolution for coverage, quarterly values for
                     moves (`trigger_radar`, until it adopted this module)
    Index Library    computed in the browser (`IndexLibraryArea.dataStatus`)

This module is the shared answer. It reads BOTH value stores — the drop's
`index_monthly_values` and the older quarterly `index_values` — and returns
one state per series with the evidence behind it and a plain-English reason,
so an empty or stale index is always explained rather than silently blank.

It changes no number. Costing keeps its carry-forward (see the module
docstring of `proxy_derivation`); this reports what that carry-forward hides.

Platform grain: no team overrides or team sources are read. Those are a
team's own numbers, not the health of the series.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.index_data import CommodityIndex, IndexValue
from app.models.index_layer import IndexMonthlyValue

CURRENT = "current"
STALE = "stale"
FORECAST_ONLY = "forecast_only"
EMPTY = "empty"
COMPOSITE = "composite"

STATES = (CURRENT, STALE, FORECAST_ONLY, EMPTY, COMPOSITE)

# PROPOSED, pending Alexis (Jira 208 open question). Every drop series ends at
# its last actual month, and a refresh cadence is not yet agreed (Jira 209), so
# this is a reporting threshold only: nothing is blocked on it.
STALE_AFTER_MONTHS = 6

STORE_MONTHLY = "monthly"
STORE_QUARTERLY = "quarterly"
STORE_BOTH = "both"


@dataclass
class SeriesHealth:
    commodity_id: int
    name: str
    commodity_key: str | None
    state: str
    reason: str
    store: str | None = None              # monthly | quarterly | both | None
    last_actual: str | None = None        # "YYYY-MM" of the newest actual value
    months_old: int | None = None
    monthly_actual_points: int = 0
    forecast_points: int = 0
    quarterly_points: int = 0

    @property
    def usable(self) -> bool:
        """Has an actual observation to price from, however old."""
        return self.state in (CURRENT, STALE, COMPOSITE)


def _month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def _ym(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _empty_reason(series: CommodityIndex) -> str:
    parts = ["no values in either store"]
    if series.commodity_key is None:
        parts.append("not part of the content drop (starter reference index)")
    if not series.provider:
        parts.append("no provider set")
    if not series.scrape_enabled:
        parts.append("no scraper enabled")
    return "; ".join(parts)


def _classify(series: CommodityIndex, monthly: tuple | None, quarterly: tuple | None,
              as_of: date) -> SeriesHealth:
    m_actual, m_last, m_forecast = monthly or (0, None, 0)
    q_points, q_last = quarterly or (0, None)

    health = SeriesHealth(
        commodity_id=series.id,
        name=series.name,
        commodity_key=series.commodity_key,
        state=EMPTY,
        reason="",
        monthly_actual_points=m_actual,
        forecast_points=m_forecast,
        quarterly_points=q_points,
    )

    if m_actual and q_points:
        health.store = STORE_BOTH
    elif m_actual:
        health.store = STORE_MONTHLY
    elif q_points:
        health.store = STORE_QUARTERLY

    if series.composite_expression:
        # Computed from other series at read time, so nothing is stored for it.
        health.state = COMPOSITE
        health.reason = "computed from other series; its health is theirs"
        return health

    # A quarter counts as observed through its last month.
    candidates = []
    if m_last is not None:
        candidates.append(m_last)
    if q_last is not None:
        candidates.append(_month_index(q_last // 10, (q_last % 10) * 3))
    if not candidates:
        if m_forecast:
            health.state = FORECAST_ONLY
            health.reason = f"only forecast values ({m_forecast}); no actual observation"
        else:
            health.state = EMPTY
            health.reason = _empty_reason(series)
        return health

    latest = max(candidates)
    age = max(0, _month_index(as_of.year, as_of.month) - latest)
    health.last_actual = _ym(latest)
    health.months_old = age
    if age > STALE_AFTER_MONTHS:
        health.state = STALE
        health.reason = (
            f"last actual value {health.last_actual} is {age} months old "
            f"(stale after {STALE_AFTER_MONTHS})"
        )
    else:
        health.state = CURRENT
        health.reason = f"last actual value {health.last_actual}, {age} months old"
    return health


def series_health_bulk(
    db: Session,
    *,
    as_of: date | None = None,
    commodity_ids: list[int] | None = None,
) -> list[SeriesHealth]:
    """Health for every series (or the given ones), ordered by name.

    Three grouped queries regardless of how many series there are, so the
    whole library is one cheap read.
    """
    as_of = as_of or date.today()

    series_q = db.query(CommodityIndex)
    monthly_q = db.query(
        IndexMonthlyValue.commodity_id,
        func.count(IndexMonthlyValue.id).filter(IndexMonthlyValue.kind == "actual"),
        func.max(IndexMonthlyValue.year * 12 + IndexMonthlyValue.month - 1)
        .filter(IndexMonthlyValue.kind == "actual"),
        func.count(IndexMonthlyValue.id).filter(IndexMonthlyValue.kind == "forecast"),
    ).group_by(IndexMonthlyValue.commodity_id)
    quarterly_q = db.query(
        IndexValue.commodity_id,
        func.count(IndexValue.id),
        func.max(IndexValue.year * 10 + IndexValue.quarter),
    ).group_by(IndexValue.commodity_id)

    if commodity_ids is not None:
        series_q = series_q.filter(CommodityIndex.id.in_(commodity_ids))
        monthly_q = monthly_q.filter(IndexMonthlyValue.commodity_id.in_(commodity_ids))
        quarterly_q = quarterly_q.filter(IndexValue.commodity_id.in_(commodity_ids))

    monthly = {cid: (int(n or 0), last, int(f or 0)) for cid, n, last, f in monthly_q}
    quarterly = {cid: (int(n or 0), last) for cid, n, last in quarterly_q}

    return [
        _classify(s, monthly.get(s.id), quarterly.get(s.id), as_of)
        for s in series_q.order_by(CommodityIndex.name)
    ]


def series_health(db: Session, commodity_id: int, *, as_of: date | None = None) -> SeriesHealth | None:
    """Health for one series; None when the series does not exist."""
    rows = series_health_bulk(db, as_of=as_of, commodity_ids=[commodity_id])
    return rows[0] if rows else None


def state_counts(rows: list[SeriesHealth]) -> dict[str, int]:
    """How many series sit in each state, every state present (zero included)."""
    counts = {state: 0 for state in STATES}
    for row in rows:
        counts[row.state] += 1
    return counts
