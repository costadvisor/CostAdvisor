"""
Core costing engine: should-cost, evolution, squeeze/desqueeze, and brief calculations.
"""
from sqlalchemy.orm import Session

from app.models.cost_model import CostModel
from app.models.price_data import ActualPrice
from app.models.actual_volume import ActualVolume
from app.services.data_resolver import get_single_index_value, get_single_index_value_detailed
from app.services.volume_projector import project_volumes
from app.services.narrative import generate_narrative
from app.services.fx_converter import convert_price
from app.services.unit_converter import convert_unit, convert_price_per_unit
from app.services.incoterm_normalizer import normalize_with_lane
from app.services.freight_lane_lookup import get_lane_adjustments
from app.constants.incoterms import normalize as _norm_incoterm
from app.schemas.costing import (
    ShouldCostResult, EvolutionRequest, EvolutionResult, EvolutionPeriod, ComponentInfo,
    SqueezeRequest, SqueezeResult, SqueezePeriod,
    BriefRequest, BriefResult, BriefDriver,
    PriceChangeRequest, PriceChangeResult, PriceChangeComponent,
    DataGap,
)

# ── Advanced formula evaluator ─────────────────────────────────
import ast as _ast
import operator as _op

_SAFE_OPS: dict = {
    _ast.Add: _op.add,
    _ast.Sub: _op.sub,
    _ast.Mult: _op.mul,
    _ast.Div: _op.truediv,
    _ast.Pow: _op.pow,
    _ast.Mod: _op.mod,
    _ast.USub: _op.neg,
    _ast.UAdd: _op.pos,
}

# Comparison operators — enable threshold / conditional formulas via `x if x < 100 else 100`.
_SAFE_CMP: dict = {
    _ast.Lt: _op.lt, _ast.LtE: _op.le,
    _ast.Gt: _op.gt, _ast.GtE: _op.ge,
    _ast.Eq: _op.eq, _ast.NotEq: _op.ne,
}


def _step(x, threshold, below, above):
    """Step function: `below` when x < threshold, else `above`."""
    return below if x < threshold else above


def _clamp(x, lo, hi):
    """Bound x to [lo, hi] — min/max bounds in one call."""
    return max(lo, min(hi, x))


# Whitelisted functions for advanced formulas (Scrum 28: bounds, steps, yield
# factors). No builtins beyond these — the call node only accepts these names.
_SAFE_FUNCS: dict = {
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "clamp": _clamp,
    "step": _step,
}


def _eval_node(node, ctx: dict):
    if isinstance(node, _ast.Constant):
        return float(node.value) if isinstance(node.value, (int, float)) else node.value
    if isinstance(node, _ast.Name):
        if node.id not in ctx:
            raise ValueError(f"Undefined variable '{node.id}'")
        return float(ctx[node.id])
    if isinstance(node, _ast.BinOp):
        fn = _SAFE_OPS.get(type(node.op))
        if fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return fn(_eval_node(node.left, ctx), _eval_node(node.right, ctx))
    if isinstance(node, _ast.UnaryOp):
        # `not` is boolean; the rest are numeric sign operators
        if isinstance(node.op, _ast.Not):
            return not _eval_node(node.operand, ctx)
        fn = _SAFE_OPS.get(type(node.op))
        if fn is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return fn(_eval_node(node.operand, ctx))
    if isinstance(node, _ast.Call):
        # Only bare `name(...)` calls against the whitelist — no attributes, no kwargs.
        if not isinstance(node.func, _ast.Name):
            raise ValueError("Only direct function calls are allowed")
        fn = _SAFE_FUNCS.get(node.func.id)
        if fn is None:
            raise ValueError(f"Unsupported function: {getattr(node.func, 'id', '?')}")
        if node.keywords:
            raise ValueError("Keyword arguments are not allowed")
        return float(fn(*[_eval_node(a, ctx) for a in node.args]))
    if isinstance(node, _ast.IfExp):
        # ternary — `body if test else orelse` (threshold / conditional logic)
        return _eval_node(node.body, ctx) if _eval_node(node.test, ctx) else _eval_node(node.orelse, ctx)
    if isinstance(node, _ast.Compare):
        left = _eval_node(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators):
            cmpfn = _SAFE_CMP.get(type(op))
            if cmpfn is None:
                raise ValueError(f"Unsupported comparison: {type(op).__name__}")
            right = _eval_node(comparator, ctx)
            if not cmpfn(left, right):
                return False
            left = right   # support chained comparisons (a < b < c)
        return True
    if isinstance(node, _ast.BoolOp):
        vals = [_eval_node(v, ctx) for v in node.values]
        if isinstance(node.op, _ast.And):
            return all(vals)
        if isinstance(node.op, _ast.Or):
            return any(vals)
        raise ValueError(f"Unsupported boolean operator: {type(node.op).__name__}")
    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


def safe_eval_expr(expression: str, context: dict) -> float:
    """Safely evaluate a mathematical expression.
    Square brackets are accepted as grouping (common in contract formulas).
    Only arithmetic operators and variable lookups are allowed — no builtins,
    no attribute access, no function calls."""
    expr = expression.replace('[', '(').replace(']', ')')
    tree = _ast.parse(expr, mode='eval')
    return _eval_node(tree.body, context)


# ── Period helpers ─────────────────────────────────────────────

MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def generate_periods(
    from_year: int, from_quarter: int,
    to_year: int, to_quarter: int,
    granularity: str = "quarterly",
) -> list[tuple[int, int, int | None, str]]:
    """
    Generate period list as [(year, quarter, month_or_none, label), ...].
    """
    periods = []
    y, q = from_year, from_quarter
    while (y, q) <= (to_year, to_quarter):
        if granularity == "monthly":
            for m_offset in range(3):
                month = (q - 1) * 3 + m_offset + 1
                label = f"{MONTH_NAMES[month - 1]}-{str(y)[-2:]}"
                periods.append((y, q, month, label))
        else:
            label = f"Q{q}-{str(y)[-2:]}"
            periods.append((y, q, None, label))
        q += 1
        if q > 4:
            q = 1
            y += 1
    return periods


def _get_period_formula(cost_model: CostModel, year: int, quarter: int):
    """Get the formula version active for a given period."""
    return cost_model.formula_for_period(year, quarter)


def _available_index_range(db: Session, cost_model: CostModel):
    """Find the min/max (year, quarter) across both index data and pricing data
    for this cost model, so the date range reflects all available information."""
    from app.models.index_data import IndexValue
    from sqlalchemy import func

    min_yq, max_yq = None, None

    # Check index data for this model's commodity components (simple + advanced)
    commodity_ids = set()
    for fv in cost_model.formula_versions:
        for c in fv.components:
            if c.commodity_id:
                commodity_ids.add(c.commodity_id)
        # Advanced mode: collect commodity ids from variable definitions
        fv_type = getattr(fv, 'formula_type', 'simple') or 'simple'
        if fv_type == 'advanced' and fv.variables:
            for var_def in fv.variables.values():
                if var_def.get('type') == 'index' and var_def.get('commodity_id'):
                    commodity_ids.add(var_def['commodity_id'])

    if commodity_ids:
        row = db.query(
            func.min(IndexValue.year * 10 + IndexValue.quarter),
            func.max(IndexValue.year * 10 + IndexValue.quarter),
        ).filter(IndexValue.commodity_id.in_(commodity_ids)).first()
        if row and row[0] is not None:
            min_yq, max_yq = row

    # Check actual pricing data for this cost model
    price_row = db.query(
        func.min(ActualPrice.year * 10 + ActualPrice.quarter),
        func.max(ActualPrice.year * 10 + ActualPrice.quarter),
    ).filter(ActualPrice.cost_model_id == cost_model.id).first()

    if price_row and price_row[0] is not None:
        p_min, p_max = price_row
        if min_yq is None:
            min_yq, max_yq = p_min, p_max
        else:
            min_yq = min(min_yq, p_min)
            max_yq = max(max_yq, p_max)

    if min_yq is None:
        return None, None, None, None

    return min_yq // 10, min_yq % 10, max_yq // 10, max_yq % 10


def _current_quarter() -> tuple[int, int]:
    """Return (year, quarter) for today."""
    from datetime import date
    today = date.today()
    return today.year, (today.month - 1) // 3 + 1


def _default_period_range(db: Session, cost_model: CostModel):
    """Determine a sensible default period range from available index data.
    Defaults to the last 8 quarters of available data."""
    min_y, min_q, max_y, max_q = _available_index_range(db, cost_model)
    if max_y is None:
        now_y, now_q = _current_quarter()
        fv = cost_model.current_formula
        if not fv:
            return now_y - 2, 1, now_y, now_q
        return max(fv.base_year - 1, 2020), 1, now_y, now_q

    # Go 7 quarters back from the latest available quarter (8 quarters total)
    to_year, to_quarter = max_y, max_q
    from_year, from_quarter = max_y, max_q
    for _ in range(7):
        from_quarter -= 1
        if from_quarter < 1:
            from_quarter = 4
            from_year -= 1

    # Clamp to available data start
    if (from_year, from_quarter) < (min_y, min_q):
        from_year, from_quarter = min_y, min_q

    return from_year, from_quarter, to_year, to_quarter


# ── Conversion helpers ────────────────────────────────────────

def _apply_fx(db: Session, value: float, from_ccy: str, to_ccy: str | None, year: int, quarter: int, team_id=None) -> float:
    if not to_ccy or to_ccy == from_ccy:
        return value
    return convert_price(db, value, from_ccy, to_ccy, year, quarter, team_id=team_id)


def _apply_unit(value: float, from_unit: str, to_unit: str | None) -> float:
    """Convert a price-per-unit value between units."""
    if not to_unit or to_unit == from_unit:
        return value
    try:
        return convert_price_per_unit(value, from_unit, to_unit)
    except ValueError:
        return value


def _output_ccy(model_ccy: str, display_ccy: str | None) -> str:
    return display_ccy if display_ccy else model_ccy


def _output_unit(model_unit: str, display_unit: str | None) -> str:
    return display_unit if display_unit else model_unit


# ── Base-price resolution ─────────────────────────────────────

def _effective_base_price(db: Session, cost_model_id, fv) -> float:
    """Return the actual price for the formula's base period if one exists,
    otherwise fall back to the manually-entered base_price on the formula version."""
    actual = db.query(ActualPrice.price).filter(
        ActualPrice.cost_model_id == cost_model_id,
        ActualPrice.year == fv.base_year,
        ActualPrice.quarter == fv.base_quarter,
    ).scalar()
    if actual is not None:
        return float(actual)
    return float(fv.base_price)


# ── Margin helpers ─────────────────────────────────────────────

def _apply_margin(indexed_cost: float, margin_type: str, margin_value: float | None,
                  base_price: float | None = None) -> tuple[float, float]:
    """
    Apply margin to indexed cost. Returns (should_cost, margin_amount).
    """
    if margin_value is not None:
        margin_value = float(margin_value)
    if base_price is not None:
        base_price = float(base_price)

    if margin_type == "pct" and margin_value is not None:
        pct = margin_value / 100.0
        if pct >= 1.0:
            pct = 0.0
        should_cost = indexed_cost / (1 - pct)
        return should_cost, should_cost - indexed_cost

    elif margin_type == "fixed" and margin_value is not None:
        should_cost = indexed_cost + margin_value
        return should_cost, margin_value

    elif margin_type == "unknown" and base_price is not None:
        margin = base_price - indexed_cost
        should_cost = indexed_cost + margin
        return should_cost, margin

    return indexed_cost, 0.0


# ── Should-Cost ────────────────────────────────────────────────

def _resolve_basis(cost_model: CostModel, fv) -> tuple[str | None, dict | None]:
    """The Incoterm a price is *quoted under* and the price-level adjustments.
    `landed_cost_adjustments` lives only on FormulaVersion — CostModel has no such
    column, so there is no model-level fallback (previously crashed with
    AttributeError whenever fv had no adjustments of its own)."""
    incoterm = (fv.incoterm if fv and fv.incoterm else cost_model.incoterm)
    adjustments = fv.landed_cost_adjustments if fv else None
    return _norm_incoterm(incoterm), adjustments


def _normalize_to(
    db: Session, cost_model: CostModel, fv,
    price: float, target_incoterm: str | None,
    price_adjustments_override: dict | None = None,
) -> float:
    """Normalize `price` to `target_incoterm` using lane defaults as fallback."""
    if not target_incoterm:
        return price
    from_inc, fv_adj = _resolve_basis(cost_model, fv)
    price_adj = price_adjustments_override if price_adjustments_override is not None else fv_adj
    lane_adj = get_lane_adjustments(db, cost_model.region, cost_model.destination_region)
    return normalize_with_lane(
        price, from_inc, target_incoterm, price_adj, lane_adj
    )


def calculate_should_cost(
    db: Session,
    cost_model: CostModel,
    target_year: int | None = None,
    target_quarter: int | None = None,
    normalize_to_incoterm: str | None = None,
) -> ShouldCostResult:
    # Use period-aware formula selection
    if target_year and target_quarter:
        fv = _get_period_formula(cost_model, target_year, target_quarter)
    else:
        fv = cost_model.current_formula

    if not fv:
        return ShouldCostResult(
            should_cost=0, cost_before_margin=0, margin_amount=0,
            rm_cost=0, ovc_cost=0, per_active_unit=None,
            currency=cost_model.currency, unit=cost_model.product.unit,
        )

    base_price = _effective_base_price(db, cost_model.id, fv)
    region = cost_model.region
    ref_year = fv.base_year
    ref_quarter = fv.base_quarter
    active = float(cost_model.product.active_content or 1)

    t_year = target_year or ref_year
    t_quarter = target_quarter or ref_quarter

    indexed_cost = _compute_indexed_cost(
        db, fv, cost_model, region, ref_year, ref_quarter, t_year, t_quarter, base_price
    )

    # Advanced formulas embed margin in the expression — skip the margin step.
    formula_type = getattr(fv, 'formula_type', 'simple') or 'simple'
    if formula_type == 'advanced':
        should_cost = indexed_cost
        margin_amount = 0.0
    else:
        should_cost, margin_amount = _apply_margin(
            indexed_cost, fv.margin_type, fv.margin_value, base_price
        )

    target_inc = _norm_incoterm(normalize_to_incoterm)
    if target_inc:
        should_cost = _normalize_to(db, cost_model, fv, should_cost, target_inc)

    return ShouldCostResult(
        should_cost=round(should_cost, 4),
        cost_before_margin=round(indexed_cost, 4),
        margin_amount=round(margin_amount, 4),
        rm_cost=0,
        ovc_cost=0,
        per_active_unit=round(should_cost / active, 4) if active else None,
        currency=cost_model.currency,
        unit=cost_model.product.unit,
        incoterm=(fv.incoterm if fv and fv.incoterm else cost_model.incoterm),
        normalized_to_incoterm=target_inc,
    )


# ── Evolution ──────────────────────────────────────────────────

def calculate_evolution(
    db: Session,
    cost_model: CostModel,
    request: EvolutionRequest,
) -> EvolutionResult:
    fv = cost_model.current_formula
    use_active = request.formula_mode != "versioned"
    model_ccy = cost_model.currency
    model_unit = cost_model.product.unit
    out_ccy = _output_ccy(model_ccy, request.display_currency)
    out_unit = _output_unit(model_unit, request.display_unit)

    if not fv:
        return EvolutionResult(
            product_name=cost_model.product.name,
            supplier_name=cost_model.supplier.name if cost_model.supplier else None,
            reference_cost=0, region=cost_model.region,
            currency=out_ccy, unit=out_unit,
            periods=[],
        )

    base_price = _effective_base_price(db, cost_model.id, fv)
    region = cost_model.region

    ref_year = request.reference_year or fv.base_year
    ref_quarter = request.reference_quarter or fv.base_quarter

    if request.from_year and request.from_quarter and request.to_year and request.to_quarter:
        from_y, from_q = request.from_year, request.from_quarter
        to_y, to_q = request.to_year, request.to_quarter
    else:
        from_y, from_q, to_y, to_q = _default_period_range(db, cost_model)

    periods = generate_periods(from_y, from_q, to_y, to_q, request.granularity)

    target_inc = _norm_incoterm(request.normalize_to_incoterm)
    lane_adj = get_lane_adjustments(db, cost_model.region, cost_model.destination_region) if target_inc else None

    actuals = {}
    actual_meta = {}  # (year, quarter) -> (incoterm, adjustments)
    for ap in db.query(ActualPrice).filter(ActualPrice.cost_model_id == cost_model.id).all():
        actuals[(ap.year, ap.quarter)] = float(ap.price)
        actual_meta[(ap.year, ap.quarter)] = (
            _norm_incoterm(ap.incoterm) if ap.incoterm else None,
            ap.landed_cost_adjustments,
        )

    # Convert reference cost for display (use current/latest formula)
    ref_cost_display = _apply_unit(
        _apply_fx(db, base_price, model_ccy, out_ccy, ref_year, ref_quarter, team_id=cost_model.team_id),
        model_unit, out_unit
    )

    # Build component info list for display.
    # In versioned mode, collect the union of labels across all formula versions
    # so every period's component costs can be matched by the frontend.
    if use_active:
        comp_info = [
            ComponentInfo(label=c.label, commodity_name=c.commodity.name if c.commodity else None)
            for c in fv.components
        ]
    else:
        seen = set()
        comp_info = []
        for ver in cost_model.formula_versions:
            for c in ver.components:
                if c.label not in seen:
                    seen.add(c.label)
                    comp_info.append(ComponentInfo(
                        label=c.label,
                        commodity_name=c.commodity.name if c.commodity else None,
                    ))

    periods_out = []
    data_gaps: list[DataGap] = []
    for year, quarter, month, label in periods:
        # Period-aware: get the formula for this specific period
        period_fv = fv if use_active else _get_period_formula(cost_model, year, quarter)
        period_base_price = _effective_base_price(db, cost_model.id, period_fv)
        period_ref_year = period_fv.base_year
        period_ref_quarter = period_fv.base_quarter

        indexed_cost = _compute_indexed_cost(
            db, period_fv, cost_model, region,
            period_ref_year, period_ref_quarter, year, quarter, period_base_price
        )

        # Compute per-component costs using period formula
        comp_base = _component_base(period_base_price, period_fv.margin_type, period_fv.margin_value)
        comp_costs = {}
        for comp in period_fv.components:
            weight = float(comp.weight)
            if comp.commodity_id:
                ref_val = get_single_index_value(
                    db, cost_model.team_id, comp.commodity_id, region, period_ref_year, period_ref_quarter
                )
                cur_val = get_single_index_value(
                    db, cost_model.team_id, comp.commodity_id, region, year, quarter
                )
                if not ref_val or not cur_val:
                    data_gaps.append(DataGap(
                        component_label=comp.label,
                        period=label,
                        reason="no index value found",
                    ))
                ratio = (cur_val / ref_val) if (ref_val and cur_val) else 1.0
            else:
                ratio = 1.0
            comp_cost = comp_base * weight * ratio
            comp_cost = _apply_unit(_apply_fx(db, comp_cost, model_ccy, out_ccy, year, quarter, team_id=cost_model.team_id), model_unit, out_unit)
            comp_costs[comp.label] = round(comp_cost, 4)

        theoretical, _ = _apply_margin(indexed_cost, period_fv.margin_type, period_fv.margin_value, period_base_price)

        actual = actuals.get((year, quarter))

        # Normalize to target Incoterm before FX/unit. Use the *period*
        # formula's basis for theoretical, and the actual price's own basis
        # (with cost-model fallback) for actual — they can differ.
        if target_inc:
            from_inc, fv_adj = _resolve_basis(cost_model, period_fv)
            theoretical = normalize_with_lane(theoretical, from_inc, target_inc, fv_adj, lane_adj)
            if actual is not None:
                a_inc, a_adj = actual_meta.get((year, quarter), (None, None))
                a_inc = a_inc or from_inc
                a_adj = a_adj or fv_adj
                actual = normalize_with_lane(actual, a_inc, target_inc, a_adj, lane_adj)

        # Apply FX and unit conversions
        theoretical = _apply_unit(_apply_fx(db, theoretical, model_ccy, out_ccy, year, quarter, team_id=cost_model.team_id), model_unit, out_unit)
        if actual is not None:
            actual = _apply_unit(_apply_fx(db, actual, model_ccy, out_ccy, year, quarter, team_id=cost_model.team_id), model_unit, out_unit)

        gap = (actual - theoretical) if actual is not None else None
        bp_display = _apply_unit(_apply_fx(db, period_base_price, model_ccy, out_ccy, year, quarter, team_id=cost_model.team_id), model_unit, out_unit)
        gap_pct = (gap / bp_display * 100) if (gap is not None and bp_display) else None

        periods_out.append(EvolutionPeriod(
            period=label,
            year=year,
            quarter=quarter,
            month=month,
            theoretical=round(theoretical, 4),
            actual=round(actual, 4) if actual is not None else None,
            gap=round(gap, 4) if gap is not None else None,
            gap_pct=round(gap_pct, 2) if gap_pct is not None else None,
            component_costs=comp_costs,
        ))

    avail_min_y, avail_min_q, avail_max_y, avail_max_q = _available_index_range(db, cost_model)

    return EvolutionResult(
        product_name=cost_model.product.name,
        supplier_name=cost_model.supplier.name if cost_model.supplier else None,
        reference_cost=round(ref_cost_display, 4),
        region=region,
        currency=out_ccy,
        unit=out_unit,
        incoterm=(fv.incoterm if fv and fv.incoterm else cost_model.incoterm),
        named_place=(fv.named_place if fv and fv.named_place else None),
        normalized_to_incoterm=target_inc,
        periods=periods_out,
        components=comp_info,
        available_from_year=avail_min_y,
        available_from_quarter=avail_min_q,
        available_to_year=avail_max_y,
        available_to_quarter=avail_max_q,
        data_gaps=data_gaps,
    )


# ── Squeeze / Desqueeze ───────────────────────────────────────

def calculate_squeeze(
    db: Session,
    cost_model: CostModel,
    request: SqueezeRequest,
) -> SqueezeResult:
    fv = cost_model.current_formula
    model_ccy = cost_model.currency
    model_unit = cost_model.product.unit
    out_ccy = _output_ccy(model_ccy, request.display_currency)
    out_unit = _output_unit(model_unit, request.display_unit)

    if not fv:
        return SqueezeResult(
            product_name=cost_model.product.name,
            supplier_name=cost_model.supplier.name if cost_model.supplier else None,
            reference_cost=0, region=cost_model.region,
            currency=out_ccy, unit=out_unit,
            periods=[], cumulative_impact=0, total_volume=0,
        )

    base_price = _effective_base_price(db, cost_model.id, fv)
    region = cost_model.region
    ref_year = request.reference_year or fv.base_year
    ref_quarter = request.reference_quarter or fv.base_quarter

    if request.from_year and request.from_quarter and request.to_year and request.to_quarter:
        from_y, from_q = request.from_year, request.from_quarter
        to_y, to_q = request.to_year, request.to_quarter
    else:
        from_y, from_q, to_y, to_q = _default_period_range(db, cost_model)

    periods = generate_periods(from_y, from_q, to_y, to_q, request.granularity)

    actuals = {}
    for ap in db.query(ActualPrice).filter(ActualPrice.cost_model_id == cost_model.id).all():
        actuals[(ap.year, ap.quarter)] = float(ap.price)

    raw_volumes = {}
    for av in db.query(ActualVolume).filter(ActualVolume.cost_model_id == cost_model.id).all():
        raw_volumes[(av.year, av.quarter)] = float(av.volume)

    period_keys = [(y, q) for y, q, _, _ in periods]
    volume_data = project_volumes(raw_volumes, request.volume_projection, period_keys)

    ref_cost_display = _apply_unit(
        _apply_fx(db, base_price, model_ccy, out_ccy, ref_year, ref_quarter, team_id=cost_model.team_id),
        model_unit, out_unit
    )

    cumulative = 0.0
    total_volume = 0.0
    periods_out = []

    for year, quarter, month, label in periods:
        # Period-aware formula selection
        period_fv = _get_period_formula(cost_model, year, quarter)
        period_base_price = _effective_base_price(db, cost_model.id, period_fv)
        period_ref_year = period_fv.base_year
        period_ref_quarter = period_fv.base_quarter

        indexed_cost = _compute_indexed_cost(
            db, period_fv, cost_model, region,
            period_ref_year, period_ref_quarter, year, quarter, period_base_price
        )

        if request.include_margin:
            theoretical, _ = _apply_margin(indexed_cost, period_fv.margin_type, period_fv.margin_value, period_base_price)
        else:
            theoretical = indexed_cost

        actual = actuals.get((year, quarter))

        theoretical = _apply_unit(_apply_fx(db, theoretical, model_ccy, out_ccy, year, quarter, team_id=cost_model.team_id), model_unit, out_unit)
        if actual is not None:
            actual = _apply_unit(_apply_fx(db, actual, model_ccy, out_ccy, year, quarter, team_id=cost_model.team_id), model_unit, out_unit)

        gap = (actual - theoretical) if actual is not None else None
        bp_display = _apply_unit(_apply_fx(db, period_base_price, model_ccy, out_ccy, year, quarter, team_id=cost_model.team_id), model_unit, out_unit)
        gap_pct = (gap / bp_display * 100) if (gap is not None and bp_display) else None

        vol, vol_projected = volume_data.get((year, quarter), (0.0, True))
        # Convert volume units if needed
        if vol and out_unit and out_unit != model_unit:
            try:
                vol = convert_unit(vol, model_unit, out_unit)
            except ValueError:
                pass
        impact = gap * vol if gap is not None else None
        if impact is not None:
            cumulative += impact
        total_volume += vol

        periods_out.append(SqueezePeriod(
            period=label,
            year=year,
            quarter=quarter,
            month=month,
            theoretical=round(theoretical, 4),
            actual=round(actual, 4) if actual is not None else None,
            gap=round(gap, 4) if gap is not None else None,
            gap_pct=round(gap_pct, 2) if gap_pct is not None else None,
            volume=round(vol, 4),
            volume_projected=vol_projected,
            impact=round(impact, 2) if impact is not None else None,
            cumulative_impact=round(cumulative, 2),
        ))

    return SqueezeResult(
        product_name=cost_model.product.name,
        supplier_name=cost_model.supplier.name if cost_model.supplier else None,
        reference_cost=round(ref_cost_display, 4),
        region=region,
        currency=out_ccy,
        unit=out_unit,
        periods=periods_out,
        cumulative_impact=round(cumulative, 2),
        total_volume=round(total_volume, 4),
    )


# ── Negotiation Brief ─────────────────────────────────────────

def calculate_brief(
    db: Session,
    cost_model: CostModel,
    request: BriefRequest,
) -> BriefResult:
    fv = cost_model.current_formula
    model_ccy = cost_model.currency
    model_unit = cost_model.product.unit
    out_ccy = _output_ccy(model_ccy, request.display_currency)
    out_unit = _output_unit(model_unit, request.display_unit)

    if not fv:
        return BriefResult(
            product_name=cost_model.product.name,
            supplier_name=cost_model.supplier.name if cost_model.supplier else None,
            destination_country=cost_model.destination_country,
            currency=out_ccy, unit=out_unit,
            current_should_cost=0, current_actual_price=None,
            gap=None, gap_pct=None, total_impact=None,
            period_label="", evolution=[], narrative="No formula defined.",
            drivers=[],
        )

    base_price = _effective_base_price(db, cost_model.id, fv)
    region = cost_model.region
    ref_year = fv.base_year
    ref_quarter = fv.base_quarter

    if request.from_year and request.from_quarter and request.to_year and request.to_quarter:
        from_y, from_q = request.from_year, request.from_quarter
        to_y, to_q = request.to_year, request.to_quarter
    else:
        from_y, from_q, to_y, to_q = _default_period_range(db, cost_model)

    periods = generate_periods(from_y, from_q, to_y, to_q, "quarterly")

    actuals = {}
    for ap in db.query(ActualPrice).filter(ActualPrice.cost_model_id == cost_model.id).all():
        actuals[(ap.year, ap.quarter)] = float(ap.price)

    raw_volumes = {}
    for av in db.query(ActualVolume).filter(ActualVolume.cost_model_id == cost_model.id).all():
        raw_volumes[(av.year, av.quarter)] = float(av.volume)

    evo_periods = []
    for year, quarter, month, label in periods:
        # Use the current formula for all periods (matches Evolution's default
        # "active" mode) so both pages produce identical theoretical lines.
        period_fv = fv
        period_base_price = base_price
        period_ref_year = ref_year
        period_ref_quarter = ref_quarter

        indexed_cost = _compute_indexed_cost(
            db, period_fv, cost_model, region,
            period_ref_year, period_ref_quarter, year, quarter, period_base_price
        )
        theoretical, _ = _apply_margin(indexed_cost, period_fv.margin_type, period_fv.margin_value, period_base_price)
        actual = actuals.get((year, quarter))

        theoretical = _apply_unit(_apply_fx(db, theoretical, model_ccy, out_ccy, year, quarter, team_id=cost_model.team_id), model_unit, out_unit)
        if actual is not None:
            actual = _apply_unit(_apply_fx(db, actual, model_ccy, out_ccy, year, quarter, team_id=cost_model.team_id), model_unit, out_unit)

        gap = (actual - theoretical) if actual is not None else None
        bp_display = _apply_unit(_apply_fx(db, period_base_price, model_ccy, out_ccy, year, quarter, team_id=cost_model.team_id), model_unit, out_unit)
        gap_pct = (gap / bp_display * 100) if (gap is not None and bp_display) else None

        evo_periods.append(EvolutionPeriod(
            period=label, year=year, quarter=quarter, month=month,
            theoretical=round(theoretical, 4),
            actual=round(actual, 4) if actual is not None else None,
            gap=round(gap, 4) if gap is not None else None,
            gap_pct=round(gap_pct, 2) if gap_pct is not None else None,
        ))

    last_period = evo_periods[-1] if evo_periods else None
    current_sc = last_period.theoretical if last_period else 0
    current_actual = last_period.actual if last_period else None
    current_gap = last_period.gap if last_period else None
    current_gap_pct = last_period.gap_pct if last_period else None

    volumes_missing = not bool(raw_volumes)
    total_impact = None
    if raw_volumes:
        total_impact = 0.0
        for ep in evo_periods:
            vol = raw_volumes.get((ep.year, ep.quarter), 0)
            if ep.gap is not None:
                total_impact += ep.gap * vol

    # Compute drivers using latest formula
    last_y, last_q = periods[-1][0], periods[-1][1]
    last_label = periods[-1][3]
    comp_base = _component_base(base_price, fv.margin_type, fv.margin_value)
    drivers = []
    data_gaps: list[DataGap] = []
    for comp in fv.components:
        weight = float(comp.weight)
        idx_name = None
        idx_change_pct = 0.0
        ratio = 1.0

        if comp.commodity_id:
            ref_val = get_single_index_value(
                db, cost_model.team_id, comp.commodity_id, region, ref_year, ref_quarter
            )
            cur_val = get_single_index_value(
                db, cost_model.team_id, comp.commodity_id, region, last_y, last_q
            )
            if not ref_val or not cur_val:
                data_gaps.append(DataGap(
                    component_label=comp.label,
                    period=last_label,
                    reason="no index value found",
                ))
            if ref_val is not None and cur_val is not None and ref_val != 0:
                ratio = cur_val / ref_val
                idx_change_pct = (ratio - 1) * 100
            if comp.commodity:
                idx_name = comp.commodity.name

        contribution = _apply_unit(
            _apply_fx(db, comp_base * weight * (idx_change_pct / 100), model_ccy, out_ccy, last_y, last_q, team_id=cost_model.team_id),
            model_unit, out_unit
        )
        comp_cost = _apply_unit(
            _apply_fx(db, comp_base * weight * ratio, model_ccy, out_ccy, last_y, last_q, team_id=cost_model.team_id),
            model_unit, out_unit
        )
        direction = "up" if idx_change_pct > 1 else "down" if idx_change_pct < -1 else "flat"

        drivers.append(BriefDriver(
            component_label=comp.label,
            index_name=idx_name,
            index_change_pct=round(idx_change_pct, 2),
            contribution_to_gap=round(contribution, 4),
            component_cost=round(comp_cost, 4),
            direction=direction,
        ))

    drivers.sort(key=lambda d: abs(d.component_cost), reverse=True)

    period_label = f"{periods[0][3]} to {periods[-1][3]}" if periods else ""

    narrative = generate_narrative(
        product_name=cost_model.product.name,
        supplier_name=cost_model.supplier.name if cost_model.supplier else None,
        drivers=[d.model_dump() for d in drivers],
        gap=current_gap,
        gap_pct=current_gap_pct,
        total_impact=total_impact,
        currency=out_ccy,
        period_label=period_label,
        num_periods=len(periods),
    )

    return BriefResult(
        product_name=cost_model.product.name,
        supplier_name=cost_model.supplier.name if cost_model.supplier else None,
        destination_country=cost_model.destination_country,
        currency=out_ccy,
        unit=out_unit,
        current_should_cost=current_sc,
        current_actual_price=current_actual,
        gap=current_gap,
        gap_pct=current_gap_pct,
        total_impact=round(total_impact, 2) if total_impact is not None else None,
        volumes_missing=volumes_missing,
        period_label=period_label,
        evolution=evo_periods,
        narrative=narrative,
        drivers=drivers,
        data_gaps=data_gaps,
    )


# ── Price Change Analysis ─────────────────────────────────────

def calculate_price_change(
    db: Session,
    cost_model: CostModel,
    request: PriceChangeRequest,
) -> PriceChangeResult:
    """Compute the fair price change between two periods based on component index movements."""
    # Use period-aware formula for both from and to periods
    from_fv = _get_period_formula(cost_model, request.from_year, request.from_quarter)
    to_fv = _get_period_formula(cost_model, request.to_year, request.to_quarter)

    if not from_fv or not to_fv:
        return PriceChangeResult(
            product_name=cost_model.product.name,
            supplier_name=cost_model.supplier.name if cost_model.supplier else None,
            currency=cost_model.currency,
            unit=cost_model.product.unit,
            base_price=0,
            from_label=f"Q{request.from_quarter}-{str(request.from_year)[-2:]}",
            to_label=f"Q{request.to_quarter}-{str(request.to_year)[-2:]}",
            fair_change_pct=0,
            fair_new_price=0,
            margin_weight=0,
            components=[],
        )

    # Use the to-period formula for the analysis
    fv = to_fv
    base_price = _effective_base_price(db, cost_model.id, fv)
    region = cost_model.region

    # Compute margin weight
    comp_base = _component_base(base_price, fv.margin_type, fv.margin_value)
    margin_weight = (base_price - comp_base) / base_price if base_price else 0

    components = []
    total_fair_change = 0.0

    for comp in fv.components:
        weight = float(comp.weight)
        # Weight relative to full price (not just component pool)
        full_weight = weight * (1 - margin_weight)

        idx_start = None
        idx_end = None
        idx_change_pct = 0.0

        if comp.commodity_id:
            ref_val = get_single_index_value(
                db, cost_model.team_id, comp.commodity_id, region,
                request.from_year, request.from_quarter,
            )
            cur_val = get_single_index_value(
                db, cost_model.team_id, comp.commodity_id, region,
                request.to_year, request.to_quarter,
            )
            if ref_val:
                idx_start = ref_val
            if cur_val:
                idx_end = cur_val
            if ref_val and cur_val:
                idx_change_pct = (cur_val / ref_val - 1) * 100

        contribution = full_weight * idx_change_pct
        total_fair_change += contribution

        components.append(PriceChangeComponent(
            label=comp.label,
            index_name=comp.commodity.name if comp.commodity else None,
            weight=round(full_weight * 100, 2),
            index_start=round(idx_start, 4) if idx_start else None,
            index_end=round(idx_end, 4) if idx_end else None,
            index_change_pct=round(idx_change_pct, 2),
            contribution_pct=round(contribution, 2),
        ))

    fair_new_price = base_price * (1 + total_fair_change / 100)

    return PriceChangeResult(
        product_name=cost_model.product.name,
        supplier_name=cost_model.supplier.name if cost_model.supplier else None,
        currency=cost_model.currency,
        unit=cost_model.product.unit,
        base_price=round(base_price, 4),
        from_label=f"Q{request.from_quarter}-{str(request.from_year)[-2:]}",
        to_label=f"Q{request.to_quarter}-{str(request.to_year)[-2:]}",
        fair_change_pct=round(total_fair_change, 2),
        fair_new_price=round(fair_new_price, 4),
        margin_weight=round(margin_weight * 100, 2),
        components=components,
    )


# ── Shared helpers ─────────────────────────────────────────────

def _component_base(base_price: float, margin_type: str, margin_value: float | None) -> float:
    """Compute the cost pool (base_price minus margin).
    Component weights sum to 1.0 and represent the composition of this pool,
    not the full price. Margin is re-applied separately by _apply_margin."""
    if margin_type == "pct" and margin_value is not None:
        pct = float(margin_value) / 100.0
        if pct >= 1.0:
            pct = 0.0
        return base_price * (1 - pct)
    elif margin_type == "fixed" and margin_value is not None:
        return base_price - float(margin_value)
    return base_price


def _compute_indexed_cost(
    db: Session,
    fv,
    cost_model: CostModel,
    region: str,
    ref_year: int,
    ref_quarter: int,
    target_year: int,
    target_quarter: int,
    base_price: float,
) -> float:
    """Compute the indexed cost for a given period using the formula components."""
    formula_type = getattr(fv, 'formula_type', 'simple') or 'simple'
    if formula_type == 'advanced':
        return _compute_advanced_cost(db, fv, cost_model, region, target_year, target_quarter)

    comp_base = _component_base(base_price, fv.margin_type, fv.margin_value)
    indexed_cost = 0.0
    for comp in fv.components:
        weight = float(comp.weight)
        if comp.commodity_id:
            ref_val = get_single_index_value(
                db, cost_model.team_id, comp.commodity_id, region, ref_year, ref_quarter
            )
            cur_val = get_single_index_value(
                db, cost_model.team_id, comp.commodity_id, region, target_year, target_quarter
            )
            ratio = (cur_val / ref_val) if (ref_val and cur_val) else 1.0
        else:
            ratio = 1.0
        indexed_cost += comp_base * weight * ratio
    return indexed_cost


def _period_label(year: int, quarter: int) -> str:
    return f"Q{quarter}-{str(year)[-2:]}"


def _compute_indexed_cost_detailed(
    db: Session,
    fv,
    cost_model: CostModel,
    region: str,
    ref_year: int,
    ref_quarter: int,
    target_year: int,
    target_quarter: int,
    base_price: float,
) -> tuple[float, list, list]:
    """Scrum 17 — per-component breakdown alongside the indexed-cost total. Mirrors
    `_compute_indexed_cost`'s simple-mode loop exactly (same comp_base/weight/ratio
    math), but also records base/current index values, ratio, contribution and
    provenance per component, plus a DataGap entry for any component riding flat for
    want of data. Advanced-mode formulas have no discrete components to break down —
    callers should check `fv.formula_type` and fall back to a single opaque line."""
    from app.schemas.costing import ComponentBreakdown, DataGap

    comp_base = _component_base(base_price, fv.margin_type, fv.margin_value)
    indexed_cost = 0.0
    components: list[ComponentBreakdown] = []
    data_gaps: list[DataGap] = []
    ref_label = _period_label(ref_year, ref_quarter)
    cur_label = _period_label(target_year, target_quarter)

    for comp in fv.components:
        weight = float(comp.weight)
        ref_val = cur_val = None
        source = None
        has_data = True
        if comp.commodity_id:
            ref_val, _ = get_single_index_value_detailed(
                db, cost_model.team_id, comp.commodity_id, region, ref_year, ref_quarter
            )
            cur_val, source = get_single_index_value_detailed(
                db, cost_model.team_id, comp.commodity_id, region, target_year, target_quarter
            )
            ratio = (cur_val / ref_val) if (ref_val and cur_val) else 1.0
            if not ref_val or not cur_val:
                has_data = False
                data_gaps.append(DataGap(
                    component_label=comp.label, period=cur_label,
                    reason="no index value found",
                ))
        else:
            ratio = 1.0
        contribution = comp_base * weight * ratio
        indexed_cost += contribution
        components.append(ComponentBreakdown(
            label=comp.label,
            commodity_id=comp.commodity_id,
            commodity_name=comp.commodity.name if comp.commodity_id and comp.commodity else None,
            weight_pct=round(weight * 100, 4),
            base_value=round(ref_val, 4) if ref_val is not None else None,
            current_value=round(cur_val, 4) if cur_val is not None else None,
            ratio=round(ratio, 6),
            contribution=round(contribution, 4),
            source=source,
            base_period=ref_label,
            current_period=cur_label,
            has_data=has_data,
        ))
    return indexed_cost, components, data_gaps


def calculate_should_cost_breakdown(
    db: Session,
    cost_model: CostModel,
    target_year: int | None = None,
    target_quarter: int | None = None,
    normalize_to_incoterm: str | None = None,
    display_currency: str | None = None,
    display_unit: str | None = None,
):
    """Scrum 17 — the should-cost, itemized: per-component index name/weight/base/
    current/ratio/contribution, plus margin, FX, unit and Incoterm adjustments — all
    numbers summing exactly to the displayed should-cost. Mirrors calculate_should_cost's
    orchestration exactly but calls the *_detailed variants to keep the per-component
    math alongside the total, rather than recomputing it."""
    from app.schemas.costing import ShouldCostBreakdown

    if target_year and target_quarter:
        fv = _get_period_formula(cost_model, target_year, target_quarter)
    else:
        fv = cost_model.current_formula

    if not fv:
        return ShouldCostBreakdown(
            should_cost=0, cost_before_margin=0, margin_amount=0, margin_type="unknown",
            components=[], data_gaps=[], currency=cost_model.currency,
            unit=cost_model.product.unit,
        )

    base_price = _effective_base_price(db, cost_model.id, fv)
    region = cost_model.region
    ref_year = fv.base_year
    ref_quarter = fv.base_quarter
    t_year = target_year or ref_year
    t_quarter = target_quarter or ref_quarter

    formula_type = getattr(fv, 'formula_type', 'simple') or 'simple'
    if formula_type == 'advanced':
        # Advanced (expression-based) formulas have no discrete weighted components —
        # report the whole result as a single opaque line rather than fabricating a
        # per-component split that doesn't exist in the formula's own definition.
        indexed_cost = _compute_advanced_cost(db, fv, cost_model, region, t_year, t_quarter)
        components, data_gaps = [], []
        should_cost = indexed_cost
        margin_amount = 0.0
    else:
        indexed_cost, components, data_gaps = _compute_indexed_cost_detailed(
            db, fv, cost_model, region, ref_year, ref_quarter, t_year, t_quarter, base_price
        )
        should_cost, margin_amount = _apply_margin(
            indexed_cost, fv.margin_type, fv.margin_value, base_price
        )

    should_cost_pre_incoterm = should_cost
    target_inc = _norm_incoterm(normalize_to_incoterm)
    if target_inc:
        should_cost = _normalize_to(db, cost_model, fv, should_cost, target_inc)
    incoterm_adjustment = (
        round(should_cost - should_cost_pre_incoterm, 4) if target_inc else None
    )

    # Display currency/unit — applied to the total AND proportionally to each
    # component's contribution, so the breakdown still sums exactly in the
    # requested display units (ShouldCostRequest carries these fields but they were
    # previously never wired through to the engine).
    out_ccy = cost_model.currency
    out_unit = cost_model.product.unit
    fx_rate_used = None
    unit_factor_used = None
    if display_currency and display_currency != cost_model.currency:
        converted = _apply_fx(db, should_cost, cost_model.currency, display_currency, t_year, t_quarter, team_id=cost_model.team_id)
        fx_rate_used = (converted / should_cost) if should_cost else None
        if fx_rate_used is not None:
            for c in components:
                c.contribution = round(c.contribution * fx_rate_used, 4)
            should_cost = round(converted, 4)
            out_ccy = display_currency
    if display_unit and display_unit != cost_model.product.unit:
        converted = _apply_unit(should_cost, cost_model.product.unit, display_unit)
        unit_factor_used = (converted / should_cost) if should_cost else None
        if unit_factor_used is not None:
            for c in components:
                c.contribution = round(c.contribution * unit_factor_used, 4)
            should_cost = round(converted, 4)
            out_unit = display_unit

    return ShouldCostBreakdown(
        should_cost=round(should_cost, 4),
        cost_before_margin=round(indexed_cost, 4),
        margin_amount=round(margin_amount, 4),
        margin_type=getattr(fv, 'margin_type', 'unknown') or 'unknown',
        components=components,
        data_gaps=data_gaps,
        incoterm_adjustment=incoterm_adjustment,
        fx_rate_used=round(fx_rate_used, 6) if fx_rate_used is not None else None,
        unit_factor_used=round(unit_factor_used, 6) if unit_factor_used is not None else None,
        currency=out_ccy,
        unit=out_unit,
        incoterm=(fv.incoterm if fv and fv.incoterm else cost_model.incoterm),
        normalized_to_incoterm=target_inc,
    )


def _compute_advanced_cost(
    db: Session,
    fv,
    cost_model: CostModel,
    region: str,
    target_year: int,
    target_quarter: int,
) -> float:
    """Evaluate an advanced free-form expression for the given period.

    Each variable in fv.variables maps to either an absolute index value for the
    target period or a user-supplied fixed constant. The expression result IS the
    should-cost — no margin step is applied on top.
    """
    if not fv.expression:
        return float(fv.base_price)

    context: dict[str, float] = {}
    for var_name, var_def in (fv.variables or {}).items():
        if var_def.get('type') == 'index' and var_def.get('commodity_id'):
            val = get_single_index_value(
                db, cost_model.team_id, var_def['commodity_id'],
                region, target_year, target_quarter,
            )
            context[var_name] = float(val) if val is not None else 0.0
        else:
            context[var_name] = float(var_def.get('value', 0))

    try:
        return safe_eval_expr(fv.expression, context)
    except Exception:
        # Fall back to base_price so callers always get a numeric result.
        return float(fv.base_price)
