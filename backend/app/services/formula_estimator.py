"""Cost-structure estimator (Scrum 33).

Proposes a weighted line set for a catalog combo (FormulaTemplate x region)
that has none, or only a CONF-LOW proportional-scaling placeholder. Never
mutates FormulaTemplateComponent/FormulaRegionCoverage directly — every
proposal is a draft (EstimatorProposal/EstimatorProposalLine) until a human
explicitly approves it via approve_proposal.

Primary evidence: sibling-region recipe inheritance. The same template's
OTHER regions often already carry a trustworthy (CONF-HIGH/CONF-MED, never
CONF-LOW) human recipe — the same commodities almost certainly belong in
the target region too, just possibly under a different data-availability
situation. This substitutes for the ticket's named-but-absent "synthesis
routes" data source (a feedstock-label dataset that does not exist
anywhere in this repo), using data that genuinely exists in this catalog.

Secondary evidence: priced history (ActualPrice, via a CostModel whose
FormulaVersion.source_coverage_id points at this exact combo) correlated
against candidate commodity indexes. Confirms/adjusts a sibling-derived
proposal when real linked history exists, and is the sole fallback when a
template has no sibling regions at all.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from math import sqrt
from statistics import mean

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.constants.trust import GRADE_HIGH, GRADE_MEDIUM

from app.models.cost_model import FormulaVersion
from app.models.formula_estimator import EstimatorProposal, EstimatorProposalLine
from app.models.formula_template import FormulaRegionCoverage, FormulaTemplateComponent
from app.models.index_data import CommodityIndex
from app.models.price_data import ActualPrice
from app.services.data_resolver import get_single_index_value_detailed

MIN_QUARTERS = 4          # matches Scrum 22/32's existing insufficient-data threshold
MIN_CORRELATION = 0.3     # |r| below this is too weak to propose blind
MAX_INDEX_WEIGHT_PCT = 70.0  # leaves room for an explicit margin/residual line


# ── Evidence gathering ──────────────────────────────────────────────────────

def _find_sibling_recipe(
    db: Session, template_id: uuid.UUID, exclude_region: str,
) -> tuple[list[FormulaTemplateComponent], str] | tuple[None, None]:
    """The best trustworthy sibling region's line set for this template, if
    any. Never CONF-LOW (that would propagate one bad guess into a second
    region) and never the excluded (target) region itself."""
    coverages = (
        db.query(FormulaRegionCoverage)
        .filter(
            FormulaRegionCoverage.template_id == template_id,
            FormulaRegionCoverage.region != exclude_region,
            # SCRUM-78: `data_confidence` is None on everything the July drop
            # loaded, so this filter alone now matches nothing. The derived
            # trust grade is the live equivalent — a sibling counts as
            # trustworthy if either the legacy confidence says so or the grade
            # does.
            or_(
                or_(
            FormulaRegionCoverage.data_confidence.in_(["CONF-HIGH", "CONF-MED"]),
            FormulaRegionCoverage.trust_grade.in_([GRADE_HIGH, GRADE_MEDIUM]),
        ),
                FormulaRegionCoverage.trust_grade.in_([GRADE_HIGH, GRADE_MEDIUM]),
            ),
        )
        .order_by(FormulaRegionCoverage.region)
        .all()
    )
    # CONF-HIGH siblings first, then CONF-MED — a deterministic tie-break.
    coverages.sort(key=lambda c: (
        0 if c.data_confidence == "CONF-HIGH" or c.trust_grade == GRADE_HIGH else 1))

    for cov in coverages:
        lines = (
            db.query(FormulaTemplateComponent)
            .filter(
                FormulaTemplateComponent.template_id == template_id,
                FormulaTemplateComponent.region == cov.region,
                FormulaTemplateComponent.component_type != "formula",
            )
            .order_by(FormulaTemplateComponent.sort_order)
            .all()
        )
        if lines and sum(float(l.weight_pct) for l in lines) > 0:
            return lines, cov.region
    return None, None


def _linked_price_history(
    db: Session, template_id: uuid.UUID, region: str,
) -> list[tuple[int, int, float]] | None:
    """ActualPrice history for any CostModel tracking this exact combo via
    FormulaVersion.source_coverage_id. None unless >= MIN_QUARTERS."""
    coverage = (
        db.query(FormulaRegionCoverage)
        .filter(FormulaRegionCoverage.template_id == template_id, FormulaRegionCoverage.region == region)
        .first()
    )
    if not coverage:
        return None
    cost_model_ids = {
        v.cost_model_id
        for v in db.query(FormulaVersion.cost_model_id)
        .filter(FormulaVersion.source_coverage_id == coverage.id)
        .all()
    }
    if not cost_model_ids:
        return None
    prices = (
        db.query(ActualPrice)
        .filter(ActualPrice.cost_model_id.in_(cost_model_ids))
        .order_by(ActualPrice.year, ActualPrice.quarter)
        .all()
    )
    history = [(p.year, p.quarter, float(p.price)) for p in prices]
    return history if len(history) >= MIN_QUARTERS else None


def _has_usable_series(db: Session, commodity_id: int, region: str) -> bool:
    """Structurally usable — not 'has a value for a specific period' (that's
    an ordinary, expected data_gap elsewhere), but 'could ever resolve at
    all'. A composite computes live and never carries its own IndexValue
    rows, so it's checked separately, never conflated with blocked."""
    ci = db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first()
    if not ci:
        return False
    if ci.composite_expression:
        return True
    if ci.retrieval_status == "blocked":
        return False
    from app.models.index_data import IndexValue
    return db.query(IndexValue.id).filter(IndexValue.commodity_id == commodity_id).first() is not None


def _correlation_for_commodity(
    db: Session, commodity_id: int, region: str, price_history: list[tuple[int, int, float]],
) -> float | None:
    """Pearson r between price_history and this commodity's resolved series
    over the same periods — None if the series doesn't cover every period."""
    series = []
    for (y, q, _p) in price_history:
        val, _source = get_single_index_value_detailed(db, None, commodity_id, region, y, q)
        if val is None:
            return None
        series.append(val)
    return _pearson([p for (_y, _q, p) in price_history], series)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = mean(xs), mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    var_x = sum((x - mx) ** 2 for x in xs)
    var_y = sum((y - my) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / sqrt(var_x * var_y)


def _correlate_candidates(db: Session, region: str, price_history: list[tuple[int, int, float]]) -> list[dict]:
    """Blind search, restricted to feedstock/energy roles (Scrum 57
    metadata) — never a labor/PPI/freight index masquerading as a raw
    material. A candidate missing data at any requested period is skipped
    entirely (there are many candidates; an unusable one just doesn't rank,
    no need to flag it the way a specifically-inherited one would be)."""
    prices = [p for (_y, _q, p) in price_history]
    results = []
    for ci in db.query(CommodityIndex).filter(CommodityIndex.role.in_(["feedstock", "energy"])).all():
        if ci.retrieval_status == "blocked":
            continue
        series = []
        for (y, q, _p) in price_history:
            val, _source = get_single_index_value_detailed(db, None, ci.id, region, y, q)
            if val is None:
                series = None
                break
            series.append(val)
        if series is None:
            continue
        r = _pearson(prices, series)
        if r is None or abs(r) < MIN_CORRELATION:
            continue
        results.append({"commodity_id": ci.id, "name": ci.name, "r": r, "r2": r * r})
    results.sort(key=lambda c: c["r2"], reverse=True)
    return results


# ── The callable service (AC1) ──────────────────────────────────────────────

def propose_recipe(db: Session, template_id: uuid.UUID, region: str) -> dict:
    """Never mutates the live recipe. Returns
    {"evaluable": bool, "reason": str|None, "lines": [...], "evidence_summary": {...}}."""
    sibling_lines, source_region = _find_sibling_recipe(db, template_id, region)
    price_history = _linked_price_history(db, template_id, region)

    if sibling_lines:
        total_weight = sum(float(l.weight_pct) for l in sibling_lines)
        correlations: dict[int, float | None] = {}
        if price_history:
            for l in sibling_lines:
                if l.commodity_id and l.commodity_id not in correlations:
                    correlations[l.commodity_id] = _correlation_for_commodity(db, l.commodity_id, region, price_history)

        proposed = []
        for l in sibling_lines:
            available = _has_usable_series(db, l.commodity_id, region) if l.commodity_id else True
            reason = f"Inherited from {source_region}'s recipe for this same formula"
            r = correlations.get(l.commodity_id)
            if r is not None:
                reason += f"; correlation r={r:.2f} against {len(price_history)}q of linked price history"
            if not available:
                reason += " (no usable series in this region — flagged, not excluded)"
            proposed.append({
                "name": l.name, "component_type": l.component_type, "commodity_id": l.commodity_id,
                "weight_pct": round(float(l.weight_pct) / total_weight * 100.0, 4),
                "is_proxy": l.is_proxy, "series_available": available, "candidate_reason": reason,
            })
        return {
            "evaluable": True, "reason": None, "lines": proposed,
            "evidence_summary": {
                "method": "sibling_region", "source_region": source_region,
                "priced_history_quarters": len(price_history) if price_history else None,
            },
        }

    if price_history:
        candidates = _correlate_candidates(db, region, price_history)
        if candidates:
            top = candidates[:2]
            r2_sum = sum(c["r2"] for c in top)
            proposed = []
            for c in top:
                w = round(c["r2"] / r2_sum * MAX_INDEX_WEIGHT_PCT, 4)
                proposed.append({
                    "name": c["name"], "component_type": "index", "commodity_id": c["commodity_id"],
                    "weight_pct": w, "is_proxy": False, "series_available": True,
                    "candidate_reason": (
                        f"r={c['r']:.2f} (r²={c['r2']:.2f}) against {len(price_history)}q of "
                        f"linked price history in {region} — no sibling region recipe existed to inherit from"
                    ),
                })
            residual = round(100.0 - sum(p["weight_pct"] for p in proposed), 4)
            proposed.append({
                "name": "Margin / unexplained", "component_type": "fixed", "commodity_id": None,
                "weight_pct": residual, "is_proxy": False, "series_available": True,
                "candidate_reason": (
                    "Residual after allocating weight to correlated candidates — margin is a line "
                    "inside the 100% total in this catalog, never added on top of it."
                ),
            })
            return {
                "evaluable": True, "reason": None, "lines": proposed,
                "evidence_summary": {
                    "method": "correlation", "source_region": None,
                    "priced_history_quarters": len(price_history),
                },
            }

    return {
        "evaluable": False,
        "reason": "no sibling region recipe and no priced history available for this combo",
        "lines": [],
        "evidence_summary": {"method": None, "source_region": None, "priced_history_quarters": None},
    }


# ── Persistence — draft, approve, reject (AC2, AC5) ────────────────────────

def create_or_update_proposal(db: Session, template_id: uuid.UUID, region: str) -> EstimatorProposal:
    result = propose_recipe(db, template_id, region)
    if not result["evaluable"]:
        raise ValueError(result["reason"])

    existing = (
        db.query(EstimatorProposal)
        .filter(EstimatorProposal.template_id == template_id, EstimatorProposal.region == region)
        .first()
    )
    if existing and existing.status == "human_approved":
        return existing  # no-op — this combo already has a real, approved decomposition

    if existing:
        db.query(EstimatorProposalLine).filter(EstimatorProposalLine.proposal_id == existing.id).delete()
        proposal = existing
        proposal.status = "ai_draft"
        proposal.evidence_summary = result["evidence_summary"]
        proposal.created_at = datetime.now(timezone.utc)
    else:
        proposal = EstimatorProposal(
            template_id=template_id, region=region, status="ai_draft",
            evidence_summary=result["evidence_summary"],
        )
        db.add(proposal)
        db.flush()

    for i, line in enumerate(result["lines"]):
        db.add(EstimatorProposalLine(proposal_id=proposal.id, sort_order=i, **line))
    db.flush()
    return proposal


def approve_proposal(db: Session, proposal: EstimatorProposal, approved_by_id: uuid.UUID, approved_by_email: str) -> FormulaRegionCoverage:
    """Writes the real, region-specific FormulaTemplateComponent rows and
    flips the coverage's review state — the exact mutation
    mark_coverage_reviewed already performs, extended with provenance.
    This is what makes the combo priceable (AC2)."""
    if proposal.status != "ai_draft":
        raise ValueError(f"Proposal is already {proposal.status}")

    db.query(FormulaTemplateComponent).filter(
        FormulaTemplateComponent.template_id == proposal.template_id,
        FormulaTemplateComponent.region == proposal.region,
    ).delete(synchronize_session=False)

    for line in proposal.lines:
        db.add(FormulaTemplateComponent(
            template_id=proposal.template_id, name=line.name, component_type=line.component_type,
            commodity_id=line.commodity_id, region=proposal.region, weight_pct=line.weight_pct,
            is_proxy=line.is_proxy, sort_order=line.sort_order,
        ))

    coverage = (
        db.query(FormulaRegionCoverage)
        .filter(FormulaRegionCoverage.template_id == proposal.template_id, FormulaRegionCoverage.region == proposal.region)
        .first()
    )
    if not coverage:
        coverage = FormulaRegionCoverage(template_id=proposal.template_id, region=proposal.region)
        db.add(coverage)

    coverage.needs_review = False
    coverage.reviewed_by = approved_by_email
    coverage.reviewed_at = datetime.now(timezone.utc)
    coverage.provenance = "human_approved"

    proposal.status = "human_approved"
    proposal.approved_by = approved_by_id
    proposal.approved_at = datetime.now(timezone.utc)
    db.flush()
    return coverage


def reject_proposal(db: Session, proposal: EstimatorProposal) -> None:
    if proposal.status != "ai_draft":
        raise ValueError(f"Proposal is already {proposal.status}")
    proposal.status = "rejected"
    db.flush()


# ── Backtest (AC4) — non-circular by construction: sibling search always
# excludes the target's own region, so a combo's own real lines are never
# their own evidence. ──────────────────────────────────────────────────────

def run_backtest(db: Session, template_id: uuid.UUID | None = None) -> dict:
    """template_id scopes the run to one template (fast, targeted — e.g.
    while iterating on a specific formula) instead of the whole catalog."""
    query = db.query(FormulaRegionCoverage).filter(
        FormulaRegionCoverage.data_confidence.in_(["CONF-HIGH", "CONF-MED"])
    )
    if template_id is not None:
        query = query.filter(FormulaRegionCoverage.template_id == template_id)
    coverages = query.all()
    per_combo = []
    for cov in coverages:
        real_lines = (
            db.query(FormulaTemplateComponent)
            .filter(
                FormulaTemplateComponent.template_id == cov.template_id,
                FormulaTemplateComponent.region == cov.region,
                FormulaTemplateComponent.component_type != "formula",
            )
            .all()
        )
        if not real_lines:
            continue
        real_by_commodity = {l.commodity_id: float(l.weight_pct) for l in real_lines if l.commodity_id}
        if not real_by_commodity:
            continue

        result = propose_recipe(db, cov.template_id, cov.region)
        if not result["evaluable"]:
            per_combo.append({
                "template_id": str(cov.template_id), "region": cov.region,
                "evaluable": False, "match_fraction": 0.0, "mean_weight_error": None,
            })
            continue

        proposed_by_commodity = {l["commodity_id"]: l["weight_pct"] for l in result["lines"] if l["commodity_id"]}
        matched = set(real_by_commodity) & set(proposed_by_commodity)
        match_fraction = round(len(matched) / len(real_by_commodity), 4)
        weight_errors = [abs(real_by_commodity[cid] - proposed_by_commodity[cid]) for cid in matched]
        per_combo.append({
            "template_id": str(cov.template_id), "region": cov.region,
            "evaluable": True, "method": result["evidence_summary"]["method"],
            "match_fraction": match_fraction,
            "mean_weight_error": round(mean(weight_errors), 4) if weight_errors else None,
        })

    evaluable = [c for c in per_combo if c["evaluable"]]
    fractions = [c["match_fraction"] for c in evaluable if c["match_fraction"] is not None]
    return {
        "summary": {
            "combos_tested": len(per_combo),
            "combos_evaluable": len(evaluable),
            "avg_match_fraction": round(mean(fractions), 4) if fractions else None,
        },
        "combos": per_combo,
    }
