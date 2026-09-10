"""Supplier trust & margin grading (Scrum 32).

Scores a supplier's quoting behaviour against the should-cost line —
consistency, direction of drift, implied margin — at (supplier, product)
grain, falling back to (supplier, subfamily) pooling when a single product
doesn't have enough priced history on its own. Deterministic, no ML,
consistent with this codebase's costing-engine philosophy of verifiable,
reproducible output.

Deliberately NOT resolved through a canonical producer entity — that layer
(SCRUM-77: producer/producer_alias/producer_region) doesn't exist anywhere
in this repo. Scored by raw Supplier.id/name instead; callers must surface
the `resolution: "raw_supplier_name"` flag rather than hide the limitation.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean, pstdev

from sqlalchemy.orm import Session

from app.models.cost_model import CostModel
from app.models.price_data import ActualPrice
from app.models.product import Product
from app.models.supplier import Supplier
from app.models.supplier_trust import SupplierTrustScore
from app.services.costing_engine import should_cost_for_period
from app.services.index_projection import _fit_ols

MIN_QUARTERS = 4  # matches Scrum 22's buy-window insufficient-data threshold


def _grade_for(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _collect_gap_history(db: Session, models: list[CostModel]) -> list[tuple[int, int, float]]:
    """(year, quarter, gap_pct) for every priced quarter across the given
    models — gap_pct = (actual - should_cost) / should_cost * 100, the exact
    figure GET /suppliers/benchmark already computes."""
    model_ids = [m.id for m in models]
    if not model_ids:
        return []
    model_by_id = {m.id: m for m in models}
    prices = (
        db.query(ActualPrice)
        .filter(ActualPrice.cost_model_id.in_(model_ids))
        .order_by(ActualPrice.year, ActualPrice.quarter)
        .all()
    )
    sc_cache: dict[tuple, float | None] = {}
    history = []
    for p in prices:
        m = model_by_id.get(p.cost_model_id)
        if not m:
            continue
        key = (p.cost_model_id, p.year, p.quarter)
        if key not in sc_cache:
            sc_cache[key] = should_cost_for_period(db, m, p.year, p.quarter)
        sc = sc_cache[key]
        if not sc:
            continue
        gap_pct = (float(p.price) - sc) / sc * 100
        history.append((p.year, p.quarter, gap_pct))
    return sorted(history)


def _score_from_history(history: list[tuple[int, int, float]]) -> dict:
    """Pure scoring math over an already-sufficient gap-% history. Returns
    the full input breakdown alongside the composite score — this is what
    makes a disputed score explainable without re-derivation."""
    values = [g for _, _, g in history]
    n = len(values)
    avg_gap_pct = mean(values)
    avg_abs_gap_pct = mean(abs(v) for v in values)
    stdev_gap_pct = pstdev(values) if n > 1 else 0.0

    # Linear drift of gap% over sequential quarters (reuses the same
    # pure-Python OLS fit the index-projection service already validated).
    _intercept, slope, _residual_std, _sxx = _fit_ols(list(range(n)), values)

    magnitude_score = _clamp(100.0 - avg_abs_gap_pct * 2.0)
    consistency_score = _clamp(100.0 - stdev_gap_pct * 2.0)
    drift_score = 100.0 if slope <= 0 else _clamp(100.0 - slope * 10.0)

    score = round(0.5 * magnitude_score + 0.3 * consistency_score + 0.2 * drift_score, 1)
    return {
        "score": score,
        "grade": _grade_for(score),
        "inputs": {
            "avg_gap_pct": round(avg_gap_pct, 2),
            "avg_abs_gap_pct": round(avg_abs_gap_pct, 2),
            "stdev_gap_pct": round(stdev_gap_pct, 2),
            "slope_pct_per_quarter": round(slope, 4),
            "magnitude_score": round(magnitude_score, 1),
            "consistency_score": round(consistency_score, 1),
            "drift_score": round(drift_score, 1),
            "n_quarters": n,
        },
    }


def _insufficient_entry(n_quarters: int) -> dict:
    return {"score": None, "grade": None, "inputs": {"n_quarters": n_quarters}}


def compute_supplier_trust_scores(db: Session, team_id: uuid.UUID, supplier_id: int) -> list[SupplierTrustScore]:
    """Computes and upserts every (product|subfamily)-grain score for one
    supplier. Never mutates row identity across recomputes — matched by
    (supplier_id, grain, grain_key), so re-running updates in place."""
    models = (
        db.query(CostModel)
        .filter(CostModel.supplier_id == supplier_id, CostModel.team_id == team_id)
        .all()
    )
    by_product: dict[uuid.UUID, list[CostModel]] = defaultdict(list)
    for m in models:
        by_product[m.product_id].append(m)

    product_history = {pid: _collect_gap_history(db, ms) for pid, ms in by_product.items()}
    product_subfamily = {
        pid: subfamily_id
        for pid, subfamily_id in db.query(Product.id, Product.subfamily_id)
        .filter(Product.id.in_(list(by_product.keys())))
        .all()
    }

    entries: list[tuple[str, uuid.UUID | None, int | None, list]] = []
    handled: set[uuid.UUID] = set()

    for pid, history in product_history.items():
        if len(history) >= MIN_QUARTERS:
            entries.append(("product", pid, None, history))
            handled.add(pid)

    remaining = {pid: h for pid, h in product_history.items() if pid not in handled}
    by_subfamily: dict[int, list[uuid.UUID]] = defaultdict(list)
    for pid in remaining:
        sub_id = product_subfamily.get(pid)
        if sub_id is not None:
            by_subfamily[sub_id].append(pid)

    for sub_id, pids in by_subfamily.items():
        pooled = sorted(h for pid in pids for h in remaining[pid])
        if len(pooled) >= MIN_QUARTERS:
            entries.append(("subfamily", None, sub_id, pooled))
            handled.update(pids)

    for pid, history in product_history.items():
        if pid not in handled:
            entries.append(("product", pid, None, history))

    rows = []
    for grain, product_id, subfamily_id, history in entries:
        grain_key = str(product_id) if grain == "product" else str(subfamily_id)
        sufficient = len(history) >= MIN_QUARTERS
        payload = _score_from_history(history) if sufficient else _insufficient_entry(len(history))

        row = (
            db.query(SupplierTrustScore)
            .filter(
                SupplierTrustScore.supplier_id == supplier_id,
                SupplierTrustScore.grain == grain,
                SupplierTrustScore.grain_key == grain_key,
            )
            .first()
        )
        if not row:
            row = SupplierTrustScore(
                team_id=team_id, supplier_id=supplier_id, grain=grain,
                product_id=product_id, subfamily_id=subfamily_id, grain_key=grain_key,
            )
            db.add(row)

        row.insufficient_data = not sufficient
        row.score = payload["score"]
        row.grade = payload["grade"]
        row.inputs = payload["inputs"]
        row.computed_at = datetime.now(timezone.utc)
        rows.append(row)

    db.flush()
    return rows
