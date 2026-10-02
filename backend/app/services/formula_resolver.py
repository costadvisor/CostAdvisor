"""Formula x region resolver (Scrum 58).

Two jobs:

1. Coverage resolution — given a template and a requested region, find the
   "combo" (per-region pricing row) to use. Fallback order: exact region →
   the region's parent chain (a subregion like NWE prices closer to Europe
   than to a world number) → GLOBAL → Europe.

2. Chain flattening — expand components of type "formula" (a template used as
   an input of another template) into effective index/fixed lines, scaling
   weights multiplicatively (60% of a sub-formula's 50% line = 30%). Tiered
   with a hard depth cap and cycle detection; the same walk doubles as the
   write-time guard when a chained component is saved.

All queries run through the caller's RLS-scoped session, so a team resolving
a formula can only ever see platform templates and its own.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from app.models.formula_template import (
    FormulaRegionCoverage,
    FormulaTemplate,
    FormulaTemplateComponent,
)
from app.models.region import Region

# Maximum number of formula-as-input hops the resolver will follow
# (product → intermediate → raw covers real tiering; the cap is a guard
# against runaway nesting, not a modelling target).
MAX_CHAIN_DEPTH = 3

# Terminal fallbacks after the requested region and its ancestors: the GLOBAL
# sentinel first, then Europe (the catalog's most complete region).
_TERMINAL_FALLBACKS = ("GLOBAL", "Europe")


class FormulaChainError(ValueError):
    """A formula chain is circular or deeper than MAX_CHAIN_DEPTH."""


def region_fallback_chain(db: Session, region: str) -> list[str]:
    """Ordered region codes to try: exact → ancestors → GLOBAL → Europe."""
    chain = [region]
    seen = {region}
    node = db.query(Region).filter(Region.code == region).first()
    while node and node.parent_id:
        node = db.get(Region, node.parent_id)
        if node and node.code not in seen:
            chain.append(node.code)
            seen.add(node.code)
    for code in _TERMINAL_FALLBACKS:
        if code not in seen:
            chain.append(code)
            seen.add(code)
    return chain


def resolve_coverage(
    db: Session, template_id: uuid.UUID, region: str
) -> tuple[FormulaRegionCoverage | None, str | None]:
    """Return (coverage row, region it was resolved at), or (None, None)."""
    rows = {
        c.region: c
        for c in db.query(FormulaRegionCoverage)
        .filter(FormulaRegionCoverage.template_id == template_id)
        .all()
    }
    for code in region_fallback_chain(db, region):
        if code in rows:
            return rows[code], code
    return None, None


def _select_line_set(
    db: Session, template_id: uuid.UUID, chain: list[str] | None
) -> tuple[list[FormulaTemplateComponent], str | None]:
    """Pick the line set for a template: the first region in the fallback
    chain that has seeded per-region rows, else the region-NULL (template-
    level / API-authored) rows. Returns (rows, matched region or None)."""
    rows = (
        db.query(FormulaTemplateComponent)
        .filter(FormulaTemplateComponent.template_id == template_id)
        .order_by(FormulaTemplateComponent.sort_order)
        .all()
    )
    if chain:
        by_region: dict[str | None, list] = {}
        for r in rows:
            by_region.setdefault(r.region, []).append(r)
        for code in chain:
            if code in by_region:
                return by_region[code], code
        return by_region.get(None, []), None
    return [r for r in rows if r.region is None], None


def flatten_components(
    db: Session,
    template_id: uuid.UUID,
    region: str | None = None,
    _depth: int = 0,
    _path: tuple[uuid.UUID, ...] = (),
    _scale: float = 1.0,
    _chain: list[str] | None = None,
) -> list[dict]:
    """Expand a template's components into effective index/fixed lines.

    With a region, each template level uses its region-specific line set
    (resolved through the same fallback chain as coverage) and falls back to
    the region-NULL set. Each returned dict carries the line's own weight,
    its effective weight after chain scaling, which template it came from,
    and which region its line set matched (for trust display).
    Raises FormulaChainError on a cycle or a chain deeper than MAX_CHAIN_DEPTH.
    """
    if template_id in _path:
        raise FormulaChainError("Circular formula chain detected")
    if _depth > MAX_CHAIN_DEPTH:
        raise FormulaChainError(
            f"Formula chain exceeds the maximum depth of {MAX_CHAIN_DEPTH}"
        )
    if region is not None and _chain is None:
        _chain = region_fallback_chain(db, region)

    components, matched_region = _select_line_set(db, template_id, _chain)

    lines: list[dict] = []
    for c in components:
        if c.component_type == "formula":
            lines.extend(
                flatten_components(
                    db,
                    c.input_template_id,
                    region=region,
                    _depth=_depth + 1,
                    _path=_path + (template_id,),
                    _scale=_scale * float(c.weight_pct) / 100.0,
                    _chain=_chain,
                )
            )
        else:
            lines.append({
                "component_id": c.id,
                "name": c.name,
                "component_type": c.component_type,
                "commodity_id": c.commodity_id,
                # SCRUM-79 reads the resolution chain from the type-code side;
                # additive, so nothing that already consumes this dict changes.
                "type_code_id": c.type_code_id,
                "weight_pct": float(c.weight_pct),
                "effective_weight_pct": float(c.weight_pct) * _scale,
                "is_proxy": c.is_proxy,
                "depth": _depth,
                "via_template_id": template_id,
                "line_region": matched_region,
            })
    return lines


def assert_valid_chain_input(
    db: Session, parent_template_id: uuid.UUID, input_template_id: uuid.UUID
) -> None:
    """Write-time guard for a component that uses another formula as input.

    Walks the input's chain as if it already hung under the parent (depth 1,
    path seeded with the parent), so both cycles back to the parent and chains
    that would exceed MAX_CHAIN_DEPTH raise before anything is saved.
    """
    flatten_components(
        db, input_template_id, _depth=1, _path=(parent_template_id,)
    )


def evaluate_weighted_template(
    db: Session,
    team_id: uuid.UUID,
    template_id: uuid.UUID,
    region: str,
    year: int,
    quarter: int,
) -> dict:
    """Deterministically evaluate a weighted template at a period.

    index_level_pct = 100 × Σ(effective_weight × ratio) / Σ(effective_weight),
    where ratio is the resolved index value at (year, quarter) over its value
    at the combo's base period. Rebasing to the recipe's own weight sum (the
    catalog legitimately runs 99.9–110, margin lines included) makes the level
    exactly 100.0 at the base period, so should_cost = base_price ×
    index_level/100 evaluates to the anchored price at base by construction.

    The catalog convention: margin is already a fixed line INSIDE the recipe,
    so coverage.margin_pct is descriptive — applying it again here would
    double-count it.

    Index values resolve through get_single_index_value (team overrides →
    exact region → GLOBAL → any → temporal carry-forward). A line whose index
    has no value at all rides flat (ratio 1.0) and is reported in data_gaps —
    explicit, never silent.
    """
    # Imported here: data_resolver pulls in the scraper registry, which the
    # pure resolve/flatten callers (and their tests) shouldn't need to load.
    from app.services.data_resolver import get_single_index_value

    coverage, cov_region = resolve_coverage(db, template_id, region)
    lines = flatten_components(db, template_id, region=region)

    result = {
        "region_requested": region,
        "coverage_region": cov_region,
        "year": year,
        "quarter": quarter,
        "evaluable": False,
        "reason": None,
        "base_price": float(coverage.base_price) if coverage and coverage.base_price is not None else None,
        "currency": coverage.currency if coverage else None,
        "base_year": coverage.base_year if coverage else None,
        "base_quarter": coverage.base_quarter if coverage else None,
        "margin_pct": float(coverage.margin_pct) if coverage and coverage.margin_pct is not None else None,
        "index_level_pct": None,
        "should_cost": None,
        "lines": [],
        "data_gaps": [],
    }

    if not lines:
        result["reason"] = "no weighted lines"
        return result
    if coverage is None:
        result["reason"] = "no regional pricing (coverage) for this formula"
        return result
    if coverage.base_year is None or coverage.base_quarter is None:
        # Ratios need a reference period; the anchor is part of the combo's
        # pricing definition, not something to guess.
        result["reason"] = "coverage has no base period anchor"
        return result

    base_y, base_q = coverage.base_year, coverage.base_quarter
    base_sum = sum(l["effective_weight_pct"] for l in lines)
    if base_sum <= 0:
        result["reason"] = "line weights sum to zero"
        return result

    weighted = 0.0
    for line in lines:
        eff = line["effective_weight_pct"]
        entry = {
            **{k: line[k] for k in ("component_id", "name", "component_type",
                                    "commodity_id", "weight_pct",
                                    "effective_weight_pct", "is_proxy", "depth",
                                    "via_template_id", "line_region")},
            "base_value": None, "current_value": None,
            "ratio": 1.0, "has_data": line["component_type"] != "index",
        }
        if line["component_type"] == "index":
            ref_val = get_single_index_value(db, team_id, line["commodity_id"], region, base_y, base_q)
            cur_val = get_single_index_value(db, team_id, line["commodity_id"], region, year, quarter)
            if ref_val and cur_val:
                entry.update(base_value=ref_val, current_value=cur_val,
                             ratio=cur_val / ref_val, has_data=True)
            else:
                result["data_gaps"].append({
                    "line": line["name"],
                    "commodity_id": line["commodity_id"],
                    "reason": "no index value found — line rides flat (ratio 1.0)",
                })
        # Share of the (rebased) index level this line explains; abs
        # contributions therefore sum exactly to the should-cost.
        contribution_pct = 100.0 * eff * entry["ratio"] / base_sum
        entry["contribution_pct"] = round(contribution_pct, 4)
        entry["contribution_abs"] = (
            round(result["base_price"] * contribution_pct / 100.0, 4)
            if result["base_price"] is not None else None
        )
        result["lines"].append(entry)
        weighted += eff * entry["ratio"]

    result["evaluable"] = True
    result["index_level_pct"] = round(100.0 * weighted / base_sum, 4)
    if result["base_price"] is not None:
        result["should_cost"] = round(result["base_price"] * weighted / base_sum, 4)
    else:
        result["reason"] = "no base price anchor — index level only"
    return result


# ── Visibility (Scrum 28b) ──────────────────────────────────────────────────
# Extracted from routers/formulas.py's private _get_visible_template so
# cost_models.py can validate a source_coverage_id without a router-to-router
# import — services are the shared layer per the repo's folder convention.
# Returns None on a miss; callers pick their own HTTP status/detail.

def get_visible_template(
    db: Session, template_id: uuid.UUID, team_id: uuid.UUID | None = None
) -> FormulaTemplate | None:
    """A template the caller may read: platform (team_id NULL), or the
    caller's own team. RLS enforces this at the DB too — the explicit check
    stops a team addressing another team's template through a team_id they
    *are* a member of."""
    template = db.query(FormulaTemplate).filter(FormulaTemplate.id == template_id).first()
    if not template or (
        team_id is not None
        and template.team_id is not None
        and template.team_id != team_id
    ):
        return None
    return template


def get_visible_coverage(
    db: Session, coverage_id: uuid.UUID, team_id: uuid.UUID | None = None
) -> FormulaRegionCoverage | None:
    """A coverage ("combo") row the caller may read — visible iff its
    template is visible."""
    coverage = db.query(FormulaRegionCoverage).filter(FormulaRegionCoverage.id == coverage_id).first()
    if not coverage:
        return None
    if get_visible_template(db, coverage.template_id, team_id) is None:
        return None
    return coverage


# ── Pinned vs. tracking (Scrum 28b) ─────────────────────────────────────────

@dataclass
class EffectiveLine:
    """One line of a formula version's *effective* recipe — either the
    frozen snapshot on FormulaComponent (pinned / unlinked) or a live
    resolution of the catalog recipe (tracking). The costing engine reads
    only this shape; it never branches on link_mode itself."""
    label: str
    commodity_id: int | None
    commodity_name: str | None
    weight: float
    component_type: str | None
    depth: int | None
    via_template_id: uuid.UUID | None
    via_template_name: str | None
    line_region: str | None
    is_proxy: bool | None
    # Set only on a live tracking resolution — `FormulaComponent` (the frozen
    # snapshot) has no type-code column, so a pinned line reaches the type-code
    # side through its series instead. Defaulted, so nothing else has to change.
    type_code_id: int | None = None
    # Scrum 27 — which nested cost model a line came through, for display.
    via_cost_model_id: uuid.UUID | None = None
    via_cost_model_name: str | None = None
    # Set on a `model` line that could NOT be expanded. The line stays in the
    # recipe carrying its weight, and the engine turns this into a data gap —
    # a sub-model that quietly vanished would silently rescale every other
    # line, and one that quietly rode flat would look like a fixed cost.
    unresolved_reason: str | None = None


# Scrum 27 — how deep one cost model may nest inside another. Same number and
# the same reason as MAX_CHAIN_DEPTH for templates: past three hops nobody can
# hold the recipe in their head, and the cap is what bounds the recursion below
# even if a cycle somehow reached it.
MAX_NEST_DEPTH = 3


def _effective_lines_from_snapshot(
    fv,
    db: Session | None = None,
    *,
    depth: int = 0,
    seen: frozenset = frozenset(),
    via_id=None,
    via_name: str | None = None,
) -> list[EffectiveLine]:
    """The frozen snapshot, with any nested child cost model expanded in place.

    A `model` line contributes its child's INDEX COMPOSITION, not the child's
    price: the child's lines fold in with their weights multiplied by this
    line's weight, and the parent's own base_price stays the anchor. That is
    the convention `flatten_components` already uses for a chained template,
    and it is what keeps a recipe's weights summing to one.

    `db` is optional so the one legacy call shape (no nesting possible without
    a session) still works; without it a `model` line is reported unresolved
    rather than silently flattened away.
    """
    lines: list[EffectiveLine] = []
    for c in fv.components:
        weight = float(c.weight)

        if c.component_type == "model" or c.child_cost_model_id is not None:
            reason = None
            child_fv = None
            child = None
            if c.child_cost_model_id is None:
                reason = "sub-model line has no cost model bound"
            elif db is None:
                reason = "sub-model could not be resolved"
            elif c.child_cost_model_id in seen:
                # A loop is unbounded recursion inside the engine, not a wrong
                # number. The write-time guard should have stopped this, so
                # reaching it means the graph was edited into a cycle some
                # other way — report it, never follow it.
                reason = "sub-model forms a circular reference"
            elif depth + 1 > MAX_NEST_DEPTH:
                reason = f"sub-model nests deeper than {MAX_NEST_DEPTH} levels"
            else:
                child = c.child_cost_model
                child_fv = child.current_formula if child else None
                if child_fv is None:
                    reason = "sub-model has no formula yet"
                elif child_fv.formula_type == "advanced":
                    # An advanced child has no discrete components to fold in.
                    # Refusing is honest; inventing a single opaque line for it
                    # would put a number in the parent nothing can explain.
                    reason = "sub-model uses an advanced expression, which cannot be nested"

            if reason is not None:
                lines.append(EffectiveLine(
                    label=c.label, commodity_id=None, commodity_name=None,
                    weight=weight, component_type="model",
                    depth=c.depth, via_template_id=c.via_template_id,
                    via_template_name=c.via_template.name if c.via_template else None,
                    line_region=c.line_region, is_proxy=c.is_proxy,
                    via_cost_model_id=c.child_cost_model_id,
                    via_cost_model_name=None,
                    unresolved_reason=reason,
                ))
                continue

            child_name = c.child_cost_model_name or c.label
            for nested in _effective_lines_from_snapshot(
                child_fv, db,
                depth=depth + 1,
                seen=seen | {c.child_cost_model_id},
                via_id=c.child_cost_model_id,
                via_name=child_name,
            ):
                lines.append(replace(
                    nested,
                    weight=nested.weight * weight,
                    # Depth is NOT incremented here: the recursive call was
                    # already given depth+1 and its leaves carry the depth
                    # relative to the root. Adding again double-counts, which
                    # is exactly what the first version did.
                    # The nearest enclosing sub-model wins, so a line reports
                    # where it came from rather than the outermost hop.
                    via_cost_model_id=nested.via_cost_model_id or c.child_cost_model_id,
                    via_cost_model_name=nested.via_cost_model_name or child_name,
                ))
            continue

        lines.append(EffectiveLine(
            label=c.label,
            commodity_id=c.commodity_id,
            commodity_name=c.commodity.name if c.commodity else None,
            weight=weight,
            component_type=c.component_type,
            depth=c.depth if via_id is None else (c.depth or 0) + depth,
            via_template_id=c.via_template_id,
            via_template_name=c.via_template.name if c.via_template else None,
            line_region=c.line_region,
            is_proxy=c.is_proxy,
            via_cost_model_id=via_id,
            via_cost_model_name=via_name,
        ))
    return lines


def assert_valid_nesting(db: Session, parent_cost_model_id, child_cost_model_id) -> None:
    """Write-time guard. Raises FormulaChainError with a reason a user can act on.

    Runs at save rather than at calculation time because a loop here is an
    infinite recursion inside the costing engine: failing where somebody can
    still fix it is the whole point, and the same reasoning made
    assert_valid_chain_input a write-time check for templates.
    """
    from app.models.cost_model import CostModel, FormulaComponent

    if child_cost_model_id == parent_cost_model_id:
        raise FormulaChainError("a cost model cannot contain itself")

    # Walk DOWN from the child. If the parent appears anywhere beneath it, then
    # adding the child under the parent closes a loop.
    frontier = [(child_cost_model_id, 1)]
    visited = set()
    while frontier:
        node, hops = frontier.pop()
        if node in visited:
            continue
        visited.add(node)
        if hops > MAX_NEST_DEPTH:
            raise FormulaChainError(f"nesting would exceed {MAX_NEST_DEPTH} levels")
        cm = db.get(CostModel, node)
        fv = cm.current_formula if cm else None
        if fv is None:
            continue
        for comp in fv.components:
            if comp.child_cost_model_id is None:
                continue
            if comp.child_cost_model_id == parent_cost_model_id:
                raise FormulaChainError(
                    "circular reference: that cost model already depends on this one"
                )
            frontier.append((comp.child_cost_model_id, hops + 1))


def get_effective_lines(db: Session, fv, cost_model) -> tuple[list[EffectiveLine], str | None]:
    """Resolve a formula version's lines for the costing engine to evaluate.

    Returns (lines, fallback_reason). fallback_reason is None whenever
    resolution went as expected (pinned/unlinked, or a live tracking
    resolution succeeded); it's set to a human reason whenever a
    tracking-mode version had to fall back to its last-known snapshot
    (deleted coverage/template, a broken chain, or a degenerate recipe) —
    explicit, so a caller can surface it as a gap rather than silently
    serving a possibly-stale number.
    """
    if fv.link_mode != "tracking":
        return _effective_lines_from_snapshot(fv, db), None
    if fv.source_coverage_id is None:
        # A version saved with link_mode="tracking" always had a
        # source_coverage_id at save time (FormulaVersionCreate requires both
        # or neither). Seeing one without the other now means the linked
        # coverage — or its parent template — was deleted and the FK's ON
        # DELETE SET NULL fired; that's a broken link, not "never linked".
        return _effective_lines_from_snapshot(fv, db), "tracking link unavailable (combo deleted) — showing last-known formula"

    coverage = db.get(FormulaRegionCoverage, fv.source_coverage_id)
    if coverage is None:
        # Defensive backstop — unreachable via any real deletion path (the FK
        # above guarantees source_coverage_id is already None by then).
        return _effective_lines_from_snapshot(fv, db), "tracking link unavailable (combo deleted) — showing last-known formula"

    try:
        raw_lines = flatten_components(db, coverage.template_id, region=cost_model.region)
    except FormulaChainError as exc:
        return _effective_lines_from_snapshot(fv, db), f"tracking link broken ({exc}) — showing last-known formula"

    base_sum = sum(l["effective_weight_pct"] for l in raw_lines)
    if not raw_lines or base_sum <= 0:
        return _effective_lines_from_snapshot(fv, db), "tracking link has no weighted lines — showing last-known formula"

    commodity_ids = {l["commodity_id"] for l in raw_lines if l["commodity_id"]}
    template_ids = {l["via_template_id"] for l in raw_lines if l["via_template_id"]}
    commodity_names = {}
    if commodity_ids:
        from app.models.index_data import CommodityIndex
        commodity_names = {
            row.id: row.name
            for row in db.query(CommodityIndex).filter(CommodityIndex.id.in_(commodity_ids)).all()
        }
    template_names = {
        row.id: row.name
        for row in db.query(FormulaTemplate).filter(FormulaTemplate.id.in_(template_ids)).all()
    } if template_ids else {}

    lines = [
        EffectiveLine(
            label=l["name"],
            commodity_id=l["commodity_id"],
            commodity_name=commodity_names.get(l["commodity_id"]),
            weight=l["effective_weight_pct"] / base_sum,
            component_type=l["component_type"],
            depth=l["depth"],
            via_template_id=l["via_template_id"],
            via_template_name=template_names.get(l["via_template_id"]),
            line_region=l["line_region"],
            is_proxy=l["is_proxy"],
            type_code_id=l.get("type_code_id"),
        )
        for l in raw_lines
    ]
    return lines, None
