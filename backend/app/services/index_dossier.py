"""Index dossier reads + the platform volatility calibration (Wave 3, DB-7).

The calibration is the load-bearing half. Three facts about the shipped ladder,
measured against the real series rather than taken on trust:

* it has **21 rungs**, so `100/(21-1)` is exactly 5 — which is why the mockup's
  hardcoded `x5` step is *accidentally* correct today and would break silently
  the moment anybody recalibrated to a different length. The step is derived
  from the ladder's own length here, never written down;
* its rungs deviate from the real 91-series distribution by up to **13.7**;
* its top rung is **21.57** while the real maximum dispersion is **35.28**, so
  the single most volatile series in the library would be pinned at 100 by a
  ladder that never saw it.

So the ladder is **regenerated, never imported** — and stored as a dated
vintage rather than overwritten, because a percentile that moved needs the old
ladder to explain why.

SCRUM-75 reads `volatility_percentile` and reports which calibration it read.
It does not recompute.
"""
from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.models.index_dossier import (
    IndexChainNode, IndexDossier, IndexDriver, IndexNegotiationPointer,
    IndexProducerRole, IndexRoleFlag, IndexSplit, VolatilityBreakpoint,
    VolatilityCalibration,
)
from app.models.index_layer import IndexMonthlyValue
from app.models.index_data import CommodityIndex

# Month-over-month percent change, standard deviation. Named on the calibration
# row so two vintages computed different ways are never compared.
METHOD_MOM_PCT_STDEV = "mom_pct_stdev"

# A series needs at least this many monthly actuals before its dispersion means
# anything. 13 points gives 12 changes — one year.
DEFAULT_MIN_POINTS = 13

# The shipped ladder's length, kept as the default so a regenerated ladder is
# directly comparable to it. Nothing derives the step from this constant.
DEFAULT_RUNGS = 21


# ── Dispersion ───────────────────────────────────────────────────────────────

def series_dispersion(
    db: Session, commodity_id: int, *, min_points: int = DEFAULT_MIN_POINTS
) -> float | None:
    """Month-over-month percent-change dispersion for one series, or None.

    None means "not measurable", which is a different answer from zero and must
    stay distinguishable: a series with two data points is not calm.
    """
    rows = (
        db.query(IndexMonthlyValue.year, IndexMonthlyValue.month, IndexMonthlyValue.value)
        .filter(IndexMonthlyValue.commodity_id == commodity_id,
                IndexMonthlyValue.kind == "actual")
        .order_by(IndexMonthlyValue.year, IndexMonthlyValue.month)
        .all()
    )
    if len(rows) < min_points:
        return None
    values = [float(v) for _, _, v in rows]
    changes = [(b - a) / a * 100 for a, b in zip(values, values[1:]) if a]
    if len(changes) < min_points - 1:
        return None
    return statistics.pstdev(changes)


def _all_dispersions(
    db: Session, *, min_points: int = DEFAULT_MIN_POINTS
) -> dict[int, float]:
    ids = [
        cid for (cid,) in
        db.query(IndexMonthlyValue.commodity_id)
        .filter(IndexMonthlyValue.kind == "actual")
        .group_by(IndexMonthlyValue.commodity_id)
        .having(func.count(IndexMonthlyValue.id) >= min_points)
        .all()
    ]
    out = {}
    for cid in ids:
        d = series_dispersion(db, cid, min_points=min_points)
        if d is not None:
            out[cid] = d
    return out


# ── The ladder ───────────────────────────────────────────────────────────────

def build_ladder(values: list[float], n_rungs: int = DEFAULT_RUNGS) -> list[float]:
    """A monotone ladder of `n_rungs` dispersion breakpoints over `values`.

    The first rung is the observed minimum and the last is the observed maximum,
    so nothing in the library can fall outside the scale — the shipped ladder's
    failure was exactly that, a top rung of 21.57 against a real maximum of
    35.28.
    """
    if n_rungs < 2:
        raise ValueError("a ladder needs at least 2 rungs")
    ordered = sorted(values)
    if not ordered:
        return []
    if len(ordered) == 1:
        return [ordered[0]] * n_rungs
    inner = statistics.quantiles(ordered, n=n_rungs - 1, method="inclusive")
    ladder = [ordered[0]] + list(inner[: n_rungs - 2]) + [ordered[-1]]
    # Quantiles over a tied distribution can repeat; a non-monotone ladder
    # would make `percentile_for` return the wrong rung.
    for i in range(1, len(ladder)):
        ladder[i] = max(ladder[i], ladder[i - 1])
    return ladder


def active_calibration(db: Session) -> VolatilityCalibration | None:
    return (
        db.query(VolatilityCalibration)
        .options(selectinload(VolatilityCalibration.breakpoints))
        .filter(VolatilityCalibration.is_active.is_(True))
        .first()
    )


def recompute_volatility_calibration(
    db: Session,
    *,
    n_rungs: int = DEFAULT_RUNGS,
    min_points: int = DEFAULT_MIN_POINTS,
    note: str | None = None,
) -> VolatilityCalibration:
    """Fit a fresh ladder over the whole library and make it the active one.

    A **new vintage** every time: the previous calibration is deactivated, not
    deleted, so a percentile that moved can still be explained. Does not commit.
    """
    dispersions = _all_dispersions(db, min_points=min_points)
    if len(dispersions) < 2:
        raise ValueError(
            f"only {len(dispersions)} series have {min_points}+ monthly actuals — "
            "not enough to fit a percentile ladder"
        )
    ladder = build_ladder(list(dispersions.values()), n_rungs=n_rungs)

    db.query(VolatilityCalibration).filter(
        VolatilityCalibration.is_active.is_(True)
    ).update({"is_active": False}, synchronize_session=False)
    db.flush()

    calibration = VolatilityCalibration(
        method=METHOD_MOM_PCT_STDEV, n_rungs=len(ladder),
        n_series=len(dispersions), min_points=min_points,
        is_active=True, note=note,
        computed_at=datetime.now(timezone.utc),
    )
    db.add(calibration)
    db.flush()
    for i, value in enumerate(ladder):
        db.add(VolatilityBreakpoint(
            calibration_id=calibration.id, rung=i, dispersion=round(value, 4)))
    db.flush()
    return calibration


def percentile_for(dispersion: float, calibration: VolatilityCalibration) -> int:
    """Place a dispersion on the ladder.

    The step comes from the ladder's own length (`100/(n-1)`), so a
    recalibration to a different number of rungs cannot silently mis-scale every
    percentile in the product.
    """
    rungs = sorted(calibration.breakpoints, key=lambda b: b.rung)
    if not rungs:
        raise ValueError("calibration has no breakpoints")
    step = calibration.step
    for b in rungs:
        if dispersion <= float(b.dispersion):
            return round(b.rung * step)
    return 100


@dataclass
class VolatilityReading:
    commodity_id: int
    dispersion: float | None
    percentile: int | None
    # Which calibration produced the number — the thing SCRUM-75 has to report.
    calibration_id: uuid.UUID | None
    calibration_computed_at: datetime | None
    method: str | None
    n_series: int | None
    # Set when there is no number, instead of returning a bare null.
    reason: str | None = None


def volatility_percentile(
    db: Session, commodity_id: int, calibration: VolatilityCalibration | None = None
) -> VolatilityReading:
    """A series' volatility percentile, with the calibration it was read from."""
    calibration = calibration or active_calibration(db)
    dispersion = series_dispersion(
        db, commodity_id,
        min_points=calibration.min_points if calibration else DEFAULT_MIN_POINTS,
    )
    if calibration is None:
        return VolatilityReading(
            commodity_id=commodity_id, dispersion=dispersion, percentile=None,
            calibration_id=None, calibration_computed_at=None, method=None,
            n_series=None,
            reason="no active volatility calibration — run a recompute",
        )
    if dispersion is None:
        return VolatilityReading(
            commodity_id=commodity_id, dispersion=None, percentile=None,
            calibration_id=calibration.id,
            calibration_computed_at=calibration.computed_at,
            method=calibration.method, n_series=calibration.n_series,
            reason=(f"fewer than {calibration.min_points} monthly actuals — "
                    "dispersion is not measurable, which is not the same as calm"),
        )
    return VolatilityReading(
        commodity_id=commodity_id, dispersion=round(dispersion, 4),
        percentile=percentile_for(dispersion, calibration),
        calibration_id=calibration.id,
        calibration_computed_at=calibration.computed_at,
        method=calibration.method, n_series=calibration.n_series,
    )


# ── Dossier reads ────────────────────────────────────────────────────────────

@dataclass
class ResolvedDossier:
    commodity_id: int
    commodity_key: str | None
    region: str | None
    # Which grain each part came from: "region" when a region-specific dossier
    # supplied it, "series" when the series-wide one did.
    resolved_from: str
    header: dict = field(default_factory=dict)
    drivers: list[dict] = field(default_factory=list)
    chain: list[dict] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    splits: list[dict] = field(default_factory=list)
    producer_roles: list[dict] = field(default_factory=list)
    pointers: list[dict] = field(default_factory=list)


def _load(db: Session, commodity_id: int, region: str | None) -> IndexDossier | None:
    q = (
        db.query(IndexDossier)
        .options(
            selectinload(IndexDossier.drivers),
            selectinload(IndexDossier.chain),
            selectinload(IndexDossier.flags),
            selectinload(IndexDossier.splits),
            selectinload(IndexDossier.producer_roles),
            selectinload(IndexDossier.pointers),
        )
        .filter(IndexDossier.commodity_id == commodity_id)
    )
    q = q.filter(IndexDossier.region.is_(None)) if region is None \
        else q.filter(IndexDossier.region == region)
    return q.first()


def dossier_for(
    db: Session, commodity_id: int, region: str | None = None
) -> ResolvedDossier | None:
    """The dossier for a series, preferring a region-specific row.

    16 of the 54 source entries carry per-region overrides and 20 fields differ
    inside them, so "per card where they differ by region" is a real
    requirement rather than a hypothetical. A region with no override falls back
    to the series-wide row, and the response says which it read.
    """
    row = _load(db, commodity_id, region) if region else None
    resolved_from = "region" if row is not None else "series"
    if row is None:
        row = _load(db, commodity_id, None)
    if row is None:
        return None

    series = db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first()
    return ResolvedDossier(
        commodity_id=commodity_id,
        commodity_key=(series.commodity_key or series.name) if series else None,
        region=region,
        resolved_from=resolved_from,
        header={
            "quote_type": row.quote_type,
            "formula_role": row.formula_role,
            "access_tier": row.access_tier,
            "anchor_correlation": float(row.anchor_correlation)
            if row.anchor_correlation is not None else None,
            "anchor_correlation_raw": row.anchor_correlation_raw,
        },
        drivers=[
            {
                "category": d.category, "provider": d.provider,
                "correlation": float(d.correlation) if d.correlation is not None else None,
                "lag_raw": d.lag_raw,
                "lag_days_min": d.lag_days_min, "lag_days_max": d.lag_days_max,
                "signal_raw": d.signal_raw, "signal_strength": d.signal_strength,
                "move_raw": d.move_raw, "move_up": d.move_up,
            }
            for d in sorted(row.drivers, key=lambda d: d.sort_order)
        ],
        chain=[
            {"position": c.position, "node_type": c.node_type,
             "label": c.label, "detail": c.detail}
            for c in sorted(row.chain, key=lambda c: c.position)
        ],
        flags=[
            {"flag_kind": f.flag_kind, "severity": f.severity,
             "label": f.label, "detail": f.detail}
            for f in sorted(row.flags, key=lambda f: (f.flag_kind, f.sort_order))
        ],
        splits=[
            {"split_type": s.split_type, "label": s.label,
             "pct": float(s.pct) if s.pct is not None else None, "note": s.note}
            for s in sorted(row.splits, key=lambda s: (s.split_type, s.sort_order))
        ],
        producer_roles=[
            {
                "producer_id": str(p.producer_id),
                "producer_name": p.producer.name if p.producer else None,
                "role": p.role,
                # Null whenever the share was not disclosed — never a real zero.
                "share_pct": float(p.share_pct) if p.share_pct is not None else None,
                "share_disclosed": p.share_disclosed,
                "location": p.location, "regions_raw": p.regions_raw,
                "tags": p.tags, "raw_name": p.raw_name,
            }
            for p in sorted(row.producer_roles, key=lambda p: (p.role, p.sort_order))
        ],
        pointers=[
            {"title": p.title, "body": p.body}
            for p in sorted(row.pointers, key=lambda p: p.sort_order)
        ],
    )
