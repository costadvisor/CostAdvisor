"""Resolution + concentration queries (Wave 3, SCRUM-74).

The three-layer index model exists so that questions the app could not ask
become answerable from the database. This module is those questions.

Before this, the type-code -> series resolution happened in memory inside
`seed_combos.feed_code_map()` and was thrown away when the load finished. The
consequence was concrete: one series backs about a quarter of all indexed cost
weight, reached through dozens of separate type-codes, and nothing in the app
could see it — so a buyer reading a diversified-looking cost breakdown was
reading one number wearing many labels.

Deliberately **platform-grain**. `GET /indexes/{id}/impact` is the nearest
existing read, but it walks a team's CostModel/FormulaComponent rows and
answers "which of *my* cost models use this". These answer "what does the
platform library depend on" — a different question over a different join, so
they are a different surface rather than a flag on that one.

Two known consumers shape the response: SCRUM-80 layers swap-backlog ranking
and derivation provenance on top of these reads, and SCRUM-71 rolls up index
exposure through them rather than re-aggregating the chain itself. So each
answer carries the provenance those need — resolution state, both proxy
readings, and the weight behind every hop.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.formula_template import (
    FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexCard, IndexMonthlyValue, TypeCode

# Why a line cannot be costed. Kept distinct rather than collapsed into one
# "unpriceable" flag, because the remedies differ completely: `no_series`
# needs somebody to buy a feed, `ambiguous` needs somebody to decide which
# series a code means, and `no_history` needs a scrape to actually run.
BLOCKER_NO_SERIES = "no_series"
BLOCKER_AMBIGUOUS = "ambiguous"
BLOCKER_NO_HISTORY = "resolved_but_no_history"
BLOCKER_NO_TYPE_CODE = "no_type_code_link"


# ── Q1: what does this type-code resolve to, through what, and is it a proxy ──

def resolve_type_code(db: Session, code: str) -> dict | None:
    """The full chain for one code. None when the code is unknown."""
    tc = db.query(TypeCode).filter(TypeCode.code == code).first()
    if tc is None:
        return None

    series = None
    cards: list[dict] = []
    history = {"actual_points": 0, "forecast_points": 0, "first": None, "last": None}

    if tc.resolves_to_id is not None:
        series = db.query(CommodityIndex).filter(CommodityIndex.id == tc.resolves_to_id).first()

    if series is not None:
        cards = [
            {
                "feed_key": c.feed_key,
                "feed_slug": c.feed_slug,
                "region": c.region,
                "region_label": c.region_label,
                "is_default_region": c.is_default_region,
            }
            for c in db.query(IndexCard)
            .filter(IndexCard.commodity_id == series.id)
            .order_by(IndexCard.feed_key)
        ]
        counts = dict(
            db.query(IndexMonthlyValue.kind, func.count(IndexMonthlyValue.id))
            .filter(IndexMonthlyValue.commodity_id == series.id)
            .group_by(IndexMonthlyValue.kind)
            .all()
        )
        bounds = (
            db.query(
                func.min(IndexMonthlyValue.year), func.max(IndexMonthlyValue.year),
            )
            .filter(
                IndexMonthlyValue.commodity_id == series.id,
                IndexMonthlyValue.kind == "actual",
            )
            .one()
        )
        history = {
            "actual_points": counts.get("actual", 0),
            "forecast_points": counts.get("forecast", 0),
            "first_year": bounds[0],
            "last_year": bounds[1],
        }

    return {
        "code": tc.code,
        "label": tc.label,
        "resolution": tc.resolution,
        # The REGISTRY reading. The recipe line carries its own, and the two
        # disagree on a material slice of the library — neither is adjudicated
        # here (see services/drop/authority.py), so the consumer is told which
        # one it is looking at.
        "proxy_status": tc.proxy_status,
        "proxy_status_source": "type_code_registry",
        "swap_priority": tc.swap_priority,
        # Prose naming a series we do not have. Its presence is what makes this
        # code a sourcing candidate; SCRUM-80 ranks the backlog off it.
        "ideal_index": tc.ideal_index,
        "registry_note": tc.registry_note,
        "series": None if series is None else {
            "commodity_id": series.id,
            "commodity_key": series.commodity_key,
            "value_kind": series.value_kind,
            "base_period": series.base_period,
            "agency": series.provider,
            "unit": series.unit,
        },
        # Several cards can display one series, so the chain fans out here.
        "cards": cards,
        "history": history,
        "priceable": tc.resolution == "resolved" and history["actual_points"] > 0,
        "blocker": _code_blocker(tc, history),
    }


def _code_blocker(tc: TypeCode, history: dict) -> str | None:
    if tc.resolution == BLOCKER_AMBIGUOUS:
        return BLOCKER_AMBIGUOUS
    if tc.resolution == BLOCKER_NO_SERIES:
        return BLOCKER_NO_SERIES
    if not history.get("actual_points"):
        # Resolves to a real series that simply has no numbers yet — a
        # different problem from either of the above, and a different fix.
        return BLOCKER_NO_HISTORY
    return None


# ── Q2 + Q4: which codes resolve here, carrying what — and what breaks ────────
#
# The ticket states these as two questions ("which type-codes resolve to this
# series and what share of cost weight", and "what breaks if this series goes
# away or gets re-sourced"). They are the same join read two ways: the set of
# dependents IS the blast radius. Answering them together keeps one source of
# truth for the numbers rather than two endpoints that can disagree.

def series_dependents(db: Session, commodity_key: str) -> dict | None:
    series = (
        db.query(CommodityIndex)
        .filter(CommodityIndex.commodity_key == commodity_key)
        .first()
    )
    if series is None:
        return None

    codes = (
        db.query(TypeCode)
        .filter(TypeCode.resolves_to_id == series.id)
        .order_by(TypeCode.source_total_weight.desc().nullslast(), TypeCode.code)
        .all()
    )

    # Share of the library's total indexed weight, so the number means
    # something without the caller having to fetch every other series.
    library_weight = float(db.query(func.sum(TypeCode.source_total_weight)).scalar() or 0)
    series_weight = float(sum(tc.source_total_weight or 0 for tc in codes))

    # Catalog lines that would break. Reads through type_code_id, which the
    # catalog retarget populates — until then this is honestly empty rather
    # than silently wrong.
    affected = (
        db.query(
            FormulaTemplate.code, FormulaTemplate.name,
            FormulaTemplateComponent.region,
            func.count(FormulaTemplateComponent.id).label("lines"),
        )
        .join(TypeCode, TypeCode.id == FormulaTemplateComponent.type_code_id)
        .join(FormulaTemplate, FormulaTemplate.id == FormulaTemplateComponent.template_id)
        .filter(TypeCode.resolves_to_id == series.id)
        .group_by(FormulaTemplate.code, FormulaTemplate.name, FormulaTemplateComponent.region)
        .order_by(func.count(FormulaTemplateComponent.id).desc())
        .all()
    )

    return {
        "commodity_id": series.id,
        "commodity_key": series.commodity_key,
        "value_kind": series.value_kind,
        "base_period": series.base_period,
        "agency": series.provider,
        "cards": [
            {"feed_key": c.feed_key, "region": c.region, "region_label": c.region_label}
            for c in db.query(IndexCard)
            .filter(IndexCard.commodity_id == series.id)
            .order_by(IndexCard.feed_key)
        ],
        "type_codes": [
            {
                "code": tc.code,
                "label": tc.label,
                "resolution": tc.resolution,
                "proxy_status": tc.proxy_status,
                "swap_priority": tc.swap_priority,
                "source_total_weight": float(tc.source_total_weight or 0),
                "weight_share_of_series_pct": (
                    round(float(tc.source_total_weight or 0) / series_weight * 100, 2)
                    if series_weight else None
                ),
            }
            for tc in codes
        ],
        "totals": {
            "type_code_count": len(codes),
            "source_total_weight": series_weight,
            "weight_share_of_library_pct": (
                round(series_weight / library_weight * 100, 2) if library_weight else None
            ),
        },
        # The blast radius, same join read as an impact list.
        "affected_catalog_lines": [
            {"formula_code": a.code, "formula_name": a.name, "region": a.region, "lines": a.lines}
            for a in affected
        ],
    }


# ── The library-wide view ────────────────────────────────────────────────────

def concentration(db: Session, limit: int = 25) -> dict:
    """Series ranked by the indexed cost weight funnelling into them.

    This is the finding that motivated the whole layer: the top series carries
    roughly a quarter of all indexed cost weight through dozens of separate
    codes. SCRUM-71 rolls index exposure up through this rather than walking
    the chain itself.
    """
    library_weight = float(db.query(func.sum(TypeCode.source_total_weight)).scalar() or 0)

    rows = (
        db.query(
            CommodityIndex.commodity_key,
            CommodityIndex.id.label("commodity_id"),
            func.count(TypeCode.id).label("code_count"),
            func.sum(TypeCode.source_total_weight).label("weight"),
        )
        .join(TypeCode, TypeCode.resolves_to_id == CommodityIndex.id)
        .group_by(CommodityIndex.commodity_key, CommodityIndex.id)
        .order_by(func.sum(TypeCode.source_total_weight).desc().nullslast())
        .limit(limit)
        .all()
    )

    return {
        "library_total_weight": library_weight,
        "series": [
            {
                "commodity_id": r.commodity_id,
                "commodity_key": r.commodity_key,
                "type_code_count": r.code_count,
                "source_total_weight": float(r.weight or 0),
                "weight_share_of_library_pct": (
                    round(float(r.weight or 0) / library_weight * 100, 2)
                    if library_weight else None
                ),
            }
            for r in rows
        ],
    }


def unpriceable_type_codes(db: Session) -> dict:
    """Every code that cannot currently produce a number, grouped by why.

    The three reasons need three different actions — buy a feed, decide what a
    code means, or make a scrape run — so they are never collapsed into one
    count.
    """
    codes = db.query(TypeCode).order_by(TypeCode.source_total_weight.desc().nullslast()).all()
    with_history = {
        cid
        for (cid,) in db.query(IndexMonthlyValue.commodity_id)
        .filter(IndexMonthlyValue.kind == "actual")
        .distinct()
    }

    grouped: dict[str, list[dict]] = {
        BLOCKER_NO_SERIES: [], BLOCKER_AMBIGUOUS: [], BLOCKER_NO_HISTORY: [],
    }
    for tc in codes:
        history = {"actual_points": 1 if tc.resolves_to_id in with_history else 0}
        blocker = _code_blocker(tc, history)
        if blocker is None:
            continue
        grouped[blocker].append({
            "code": tc.code,
            "label": tc.label,
            "resolution": tc.resolution,
            "swap_priority": tc.swap_priority,
            "ideal_index": tc.ideal_index,
            "source_total_weight": float(tc.source_total_weight or 0),
        })

    library_weight = float(db.query(func.sum(TypeCode.source_total_weight)).scalar() or 0)
    return {
        "library_total_weight": library_weight,
        "blockers": {
            reason: {
                "code_count": len(entries),
                "source_total_weight": sum(e["source_total_weight"] for e in entries),
                "weight_share_of_library_pct": (
                    round(
                        sum(e["source_total_weight"] for e in entries) / library_weight * 100, 2
                    ) if library_weight else None
                ),
                "codes": entries,
            }
            for reason, entries in grouped.items()
        },
    }


# ── Q3: why can't this combo be costed ───────────────────────────────────────

@dataclass
class LineBlocker:
    line_name: str
    region: str | None
    weight_pct: float | None
    type_code: str | None
    reason: str
    detail: str
    ideal_index: str | None = None


@dataclass
class ComboDiagnosis:
    template_id: uuid.UUID
    template_code: str | None
    region: str
    coverage_exists: bool
    priceable: bool
    reason: str | None = None
    blocking_lines: list[LineBlocker] = field(default_factory=list)
    blocked_weight_pct: float = 0.0
    total_lines: int = 0
    type_coded_lines: int = 0


def diagnose_combo(db: Session, template_id: uuid.UUID, region: str) -> ComboDiagnosis:
    """Name the specific lines blocking a combo, and the specific reason each.

    Reads the line -> type code -> series chain, so a reason is one of: the
    code resolves to nothing (`ambiguous`), resolves to a series nobody has
    bought (`no_series`), or resolves to a real series with no numbers loaded
    (`resolved_but_no_history`). A bare "unpriceable" would leave a buyer with
    nothing to act on.

    Lines with no type-code link are reported separately rather than counted
    as healthy — that link arrives with the catalog retarget, and until then
    an empty blocker list means "not yet analysable", not "all fine".
    """
    template = db.query(FormulaTemplate).filter(FormulaTemplate.id == template_id).first()
    coverage = (
        db.query(FormulaRegionCoverage)
        .filter(
            FormulaRegionCoverage.template_id == template_id,
            FormulaRegionCoverage.region == region,
        )
        .first()
    )

    diagnosis = ComboDiagnosis(
        template_id=template_id,
        template_code=template.code if template else None,
        region=region,
        coverage_exists=coverage is not None,
        priceable=False,
    )
    if template is None:
        diagnosis.reason = "formula template not found"
        return diagnosis
    if coverage is None:
        diagnosis.reason = f"no coverage row for region {region!r}"
        return diagnosis

    # Region-specific lines, falling back to the template-level (region-NULL)
    # set — the same precedence the resolver already uses.
    lines = (
        db.query(FormulaTemplateComponent)
        .filter(
            FormulaTemplateComponent.template_id == template_id,
            FormulaTemplateComponent.region == region,
        )
        .order_by(FormulaTemplateComponent.sort_order)
        .all()
    )
    if not lines:
        lines = (
            db.query(FormulaTemplateComponent)
            .filter(
                FormulaTemplateComponent.template_id == template_id,
                FormulaTemplateComponent.region.is_(None),
            )
            .order_by(FormulaTemplateComponent.sort_order)
            .all()
        )
    diagnosis.total_lines = len(lines)
    if not lines:
        diagnosis.reason = "combo has no cost lines"
        return diagnosis

    with_history = {
        cid
        for (cid,) in db.query(IndexMonthlyValue.commodity_id)
        .filter(IndexMonthlyValue.kind == "actual")
        .distinct()
    }
    codes = {
        tc.id: tc
        for tc in db.query(TypeCode).filter(
            TypeCode.id.in_([l.type_code_id for l in lines if l.type_code_id])
        )
    } if any(l.type_code_id for l in lines) else {}

    for line in lines:
        # A fixed line has nothing to resolve — margin, conversion, "other".
        if line.component_type == "fixed":
            continue
        if line.type_code_id is None:
            continue  # counted below, not a costing blocker in itself
        diagnosis.type_coded_lines += 1

        tc = codes.get(line.type_code_id)
        if tc is None:
            continue
        history = {"actual_points": 1 if tc.resolves_to_id in with_history else 0}
        blocker = _code_blocker(tc, history)
        if blocker is None:
            continue

        detail = {
            BLOCKER_AMBIGUOUS: f"type code {tc.code!r} does not resolve to a single series",
            BLOCKER_NO_SERIES: f"type code {tc.code!r} resolves to a series with no numbers",
            BLOCKER_NO_HISTORY: f"type code {tc.code!r} resolves but no history is loaded",
        }[blocker]
        diagnosis.blocking_lines.append(LineBlocker(
            line_name=line.name,
            region=line.region,
            weight_pct=float(line.weight_pct) if line.weight_pct is not None else None,
            type_code=tc.code,
            reason=blocker,
            detail=detail,
            ideal_index=tc.ideal_index,
        ))

    diagnosis.blocked_weight_pct = round(
        sum(b.weight_pct or 0 for b in diagnosis.blocking_lines), 4
    )

    if diagnosis.type_coded_lines == 0:
        # Honest about the difference between "nothing wrong" and "nothing
        # linked yet" — the catalog retarget populates type_code_id.
        diagnosis.reason = "no lines carry a type-code link yet — not analysable"
        return diagnosis

    diagnosis.priceable = not diagnosis.blocking_lines
    if diagnosis.blocking_lines:
        diagnosis.reason = (
            f"{len(diagnosis.blocking_lines)} line(s) cannot be priced, "
            f"carrying {diagnosis.blocked_weight_pct}% of the recipe"
        )
    return diagnosis
