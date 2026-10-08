import statistics
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.models.user import User
from app.models.cost_model import CostModel, FormulaVersion
from app.models.price_data import ActualPrice
from app.models.actual_volume import ActualVolume
from app.routers.auth import get_current_user
from app.schemas.costing import EvolutionRequest, DataGap
from app.services.costing_engine import (
    calculate_should_cost, calculate_evolution, calculate_forward_should_cost, _current_quarter,
    _default_period_range,
)
from app.services.data_resolver import get_single_index_value, index_lookup_cache
from app.services.fx_converter import convert_price
from app.services.permissions import require_permission

router = APIRouter()

# Every read below runs inside `index_lookup_cache()`: one request asks for the
# same (commodity, region, quarter) index value many times over (each cost
# model, each period of its evolution, the base quarter once per period), and
# the memo answers the repeats. Opened after the permission check, closed when
# the response is built; see data_resolver.index_lookup_cache.


def _team_cost_models(db: Session, team_id: uuid.UUID) -> list[CostModel]:
    """The team's cost models with what every portfolio endpoint reads on each
    one (formula versions + components, product, supplier) loaded up front,
    instead of one lazy load per model and attribute."""
    return (
        db.query(CostModel)
        .options(
            selectinload(CostModel.formula_versions).selectinload(FormulaVersion.components),
            joinedload(CostModel.product),
            joinedload(CostModel.supplier),
        )
        .filter(CostModel.team_id == team_id)
        .all()
    )


def _should_cost_today(db: Session, cm: CostModel) -> float:
    """The should-cost at the current quarter — what "should-cost now" means on
    every portfolio view. With no target quarter, calculate_should_cost
    evaluates at the formula's base quarter, i.e. returns the base price."""
    now_y, now_q = _current_quarter()
    return calculate_should_cost(db, cm, target_year=now_y, target_quarter=now_q).should_cost


class PortfolioModelSummary(BaseModel):
    cost_model_id: uuid.UUID
    product_name: str
    product_reference: str | None
    supplier_name: str | None
    destination_country: str | None
    region: str
    currency: str
    current_should_cost: float
    latest_actual_price: float | None
    gap: float | None
    gap_pct: float | None
    cumulative_impact: float | None
    flag_index_moved: bool
    flag_price_drift: bool


class PortfolioKPIs(BaseModel):
    total_exposure: float
    models_flagged: int
    largest_single_exposure: float
    largest_exposure_model_id: uuid.UUID | None


class PortfolioResponse(BaseModel):
    models: list[PortfolioModelSummary]
    kpis: PortfolioKPIs


@router.get("/summary", response_model=PortfolioResponse)
def portfolio_summary(
    team_id: uuid.UUID,
    reporting_currency: str = "USD",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "costing.view")
    with index_lookup_cache():
        return _portfolio_summary(db, team_id, reporting_currency)


def _portfolio_summary(db: Session, team_id: uuid.UUID, reporting_currency: str) -> PortfolioResponse:
    cost_models = _team_cost_models(db, team_id)

    # Latest actual price and total volume of every model, in two queries
    # rather than two per model. (cost_model_id, year, quarter) is unique on
    # both tables, so "latest" is unambiguous.
    cm_ids = [cm.id for cm in cost_models]
    latest_price_by_cm: dict[uuid.UUID, float] = {}
    total_volume_by_cm: dict[uuid.UUID, float] = {}
    if cm_ids:
        for cm_id, price in (
            db.query(ActualPrice.cost_model_id, ActualPrice.price)
            .filter(ActualPrice.cost_model_id.in_(cm_ids))
            .order_by(ActualPrice.cost_model_id, ActualPrice.year.desc(), ActualPrice.quarter.desc())
        ):
            latest_price_by_cm.setdefault(cm_id, float(price))
        for cm_id, volume in (
            db.query(ActualVolume.cost_model_id, ActualVolume.volume)
            .filter(ActualVolume.cost_model_id.in_(cm_ids))
        ):
            total_volume_by_cm[cm_id] = total_volume_by_cm.get(cm_id, 0.0) + float(volume)

    summaries = []
    total_exposure = 0.0
    largest_exposure = 0.0
    largest_exposure_id = None
    models_flagged = 0

    # "Should-cost today" = the should-cost at the current quarter, as the
    # Portfolio and product pages compute it. With no target quarter,
    # calculate_should_cost evaluates at the formula's base quarter, i.e.
    # returns the base price, and every gap below would mix the index move
    # since the base date into the supplier gap.
    now_y, now_q = _current_quarter()

    for cm in cost_models:
        fv = cm.current_formula
        if not fv:
            continue

        # Compute current should-cost
        sc_result = calculate_should_cost(db, cm, target_year=now_y, target_quarter=now_q)
        current_sc = sc_result.should_cost
        # Latest quarter with index data for this model (flags and FX below).
        _, _, to_y, to_q = _default_period_range(db, cm)

        latest_actual = latest_price_by_cm.get(cm.id)

        gap = (latest_actual - current_sc) if latest_actual is not None else None
        base_price = float(fv.base_price)
        gap_pct = (gap / base_price * 100) if (gap is not None and base_price) else None

        # Calculate cumulative impact if volumes exist
        cumulative_impact = None
        if cm.id in total_volume_by_cm and gap is not None:
            cumulative_impact = gap * total_volume_by_cm[cm.id]

        # Flags
        flag_price_drift = abs(gap_pct) > 10 if gap_pct is not None else False
        flag_index_moved = False

        # Check if indices moved >5% since base date without new formula version
        if fv.components:
            for comp in fv.components:
                if comp.commodity_id:
                    # The same (commodity, region, quarter) recurs across
                    # models on one recipe: the request memo answers repeats.
                    ref_val = get_single_index_value(
                        db, cm.team_id, comp.commodity_id, cm.region,
                        fv.base_year, fv.base_quarter
                    )
                    # Check most recent quarter
                    cur_val = get_single_index_value(
                        db, cm.team_id, comp.commodity_id, cm.region, to_y, to_q
                    )
                    if ref_val and cur_val:
                        change = abs(cur_val / ref_val - 1)
                        if change > 0.05:
                            flag_index_moved = True
                            break

        if flag_price_drift or flag_index_moved:
            models_flagged += 1

        exposure = abs(cumulative_impact) if cumulative_impact is not None else (abs(gap) if gap else 0)

        # Convert exposure to reporting currency for aggregation
        fx_exposure = exposure
        if cm.currency != reporting_currency and exposure > 0:
            try:
                fx_exposure = convert_price(db, exposure, cm.currency, reporting_currency, to_y, to_q)
            except Exception:
                fx_exposure = exposure

        total_exposure += fx_exposure
        if fx_exposure > largest_exposure:
            largest_exposure = exposure
            largest_exposure_id = cm.id

        summaries.append(PortfolioModelSummary(
            cost_model_id=cm.id,
            product_name=cm.product.name,
            product_reference=cm.product.formula,
            supplier_name=cm.supplier.name if cm.supplier else None,
            destination_country=cm.destination_country,
            region=cm.region,
            currency=cm.currency,
            current_should_cost=round(current_sc, 4),
            latest_actual_price=round(latest_actual, 4) if latest_actual else None,
            gap=round(gap, 4) if gap is not None else None,
            gap_pct=round(gap_pct, 2) if gap_pct is not None else None,
            cumulative_impact=round(cumulative_impact, 2) if cumulative_impact is not None else None,
            flag_index_moved=flag_index_moved,
            flag_price_drift=flag_price_drift,
        ))

    # Sort by exposure descending
    summaries.sort(key=lambda s: abs(s.cumulative_impact or s.gap or 0), reverse=True)

    return PortfolioResponse(
        models=summaries,
        kpis=PortfolioKPIs(
            total_exposure=round(total_exposure, 2),
            models_flagged=models_flagged,
            largest_single_exposure=round(largest_exposure, 2),
            largest_exposure_model_id=largest_exposure_id,
        ),
    )


# ── Scrum 20: Procurement Priority Matrix (volatility × spend exposure) ───────

class PriorityMatrixItem(BaseModel):
    cost_model_id: uuid.UUID
    product_name: str
    supplier_name: str | None
    region: str
    currency: str
    current_should_cost: float
    volatility_pct: float       # stdev of QoQ % change in should-cost over trailing quarters
    spend_exposure: float       # should-cost × trailing-4Q volume, in reporting currency
    has_volume: bool
    quadrant: str               # act_now | hedge | monitor | low_priority


class PriorityMatrixResponse(BaseModel):
    items: list[PriorityMatrixItem]
    reporting_currency: str
    volatility_threshold: float
    exposure_threshold: float


def _quadrant(vol: float, exp: float, vol_thr: float, exp_thr: float) -> str:
    hi_vol, hi_exp = vol >= vol_thr, exp >= exp_thr
    if hi_vol and hi_exp:
        return "act_now"       # volatile AND big spend — negotiate/hedge now
    if not hi_vol and hi_exp:
        return "hedge"         # stable but big spend — lock in a contract
    if hi_vol and not hi_exp:
        return "monitor"       # volatile but small spend — keep watching
    return "low_priority"      # stable + small spend


@router.get("/priority-matrix", response_model=PriorityMatrixResponse)
def priority_matrix(
    team_id: uuid.UUID,
    reporting_currency: str = "USD",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Score every product on index volatility (x) vs spend exposure (y) so a
    buyer can triage the portfolio into act-now / hedge / monitor / low-priority.

    Volatility = stdev of quarter-on-quarter % change in the should-cost over the
    trailing quarters (should-cost tracks the indices, so this is index-driven
    volatility). Spend exposure = current should-cost × trailing-4-quarter volume,
    converted to the reporting currency for cross-product comparability."""
    require_permission(db, current_user, team_id, "costing.view")
    with index_lookup_cache():
        return _priority_matrix(db, team_id, reporting_currency)


def _priority_matrix(db: Session, team_id: uuid.UUID, reporting_currency: str) -> PriorityMatrixResponse:
    cost_models = _team_cost_models(db, team_id)
    rows = []
    for cm in cost_models:
        fv = cm.current_formula
        if not fv:
            continue
        current_sc = _should_cost_today(db, cm)

        # Should-cost series over the default trailing range → QoQ volatility.
        evo = calculate_evolution(db, cm, EvolutionRequest(cost_model_id=cm.id))
        series = [p.theoretical for p in evo.periods if p.theoretical]
        changes = [
            (series[i] / series[i - 1] - 1) * 100
            for i in range(1, len(series)) if series[i - 1]
        ]
        # trailing 4 QoQ changes; pstdev of <2 points is 0 (flat / insufficient history)
        recent = changes[-4:]
        volatility = statistics.pstdev(recent) if len(recent) >= 2 else 0.0

        # Spend exposure = should-cost × trailing-4Q volume, in reporting currency.
        vols = (
            db.query(ActualVolume)
            .filter(ActualVolume.cost_model_id == cm.id)
            .order_by(ActualVolume.year.desc(), ActualVolume.quarter.desc())
            .limit(4)
            .all()
        )
        trailing_vol = sum(float(v.volume) for v in vols)
        has_volume = trailing_vol > 0
        sc_reporting = current_sc
        if cm.currency != reporting_currency:
            try:
                _, _, to_y, to_q = _default_period_range(db, cm)
                sc_reporting = convert_price(db, current_sc, cm.currency, reporting_currency, to_y, to_q, team_id=team_id)
            except Exception:
                sc_reporting = current_sc
        spend_exposure = sc_reporting * trailing_vol

        rows.append({
            "cost_model_id": cm.id,
            "product_name": cm.product.name,
            "supplier_name": cm.supplier.name if cm.supplier else None,
            "region": cm.region,
            "currency": cm.currency,
            "current_should_cost": round(current_sc, 4),
            "volatility_pct": round(volatility, 3),
            "spend_exposure": round(spend_exposure, 2),
            "has_volume": has_volume,
        })

    # Thresholds = median of each axis (across products with real spend for the
    # exposure axis) so the 2×2 splits this portfolio, not an arbitrary constant.
    vol_vals = [r["volatility_pct"] for r in rows]
    exp_vals = [r["spend_exposure"] for r in rows if r["has_volume"]]
    vol_thr = statistics.median(vol_vals) if vol_vals else 0.0
    exp_thr = statistics.median(exp_vals) if exp_vals else 0.0

    items = [
        PriorityMatrixItem(**r, quadrant=_quadrant(r["volatility_pct"], r["spend_exposure"], vol_thr, exp_thr))
        for r in rows
    ]
    return PriorityMatrixResponse(
        items=items,
        reporting_currency=reporting_currency,
        volatility_threshold=round(vol_thr, 3),
        exposure_threshold=round(exp_thr, 2),
    )


# ── Scrum 22: Opportunistic buy windows (should-cost vs trailing-4Q average) ──

_BUY_THRESHOLD_PCT = 3.0   # deviation beyond ±3% flips the cheap/expensive signal


class BuyWindow(BaseModel):
    cost_model_id: uuid.UUID
    product_name: str
    supplier_name: str | None
    region: str
    currency: str
    current_should_cost: float
    avg_4q: float | None            # trailing 4-quarter average should-cost (baseline)
    deviation_pct: float | None     # current vs baseline, %
    signal: str                     # cheap | neutral | expensive | insufficient


def _buy_signal(db: Session, cm: CostModel) -> BuyWindow | None:
    """Cheap/expensive-now signal: current should-cost vs the average should-cost
    of the prior 4 quarters (from the evolution series — the should-cost tracks
    the indices, so this is a spot-vs-recent-contract read without needing spot
    price data). None if the model has no formula."""
    fv = cm.current_formula
    if not fv:
        return None
    current = _should_cost_today(db, cm)
    evo = calculate_evolution(db, cm, EvolutionRequest(cost_model_id=cm.id))
    series = [p.theoretical for p in evo.periods if p.theoretical]
    prior = series[-5:-1]   # the up-to-4 quarters *before* the latest point
    base = dict(
        cost_model_id=cm.id, product_name=cm.product.name,
        supplier_name=cm.supplier.name if cm.supplier else None,
        region=cm.region, currency=cm.currency,
        current_should_cost=round(current, 4),
    )
    if len(prior) < 2:
        return BuyWindow(**base, avg_4q=None, deviation_pct=None, signal="insufficient")
    avg4 = sum(prior) / len(prior)
    dev = (current - avg4) / avg4 * 100 if avg4 else 0.0
    signal = "cheap" if dev <= -_BUY_THRESHOLD_PCT else "expensive" if dev >= _BUY_THRESHOLD_PCT else "neutral"
    return BuyWindow(**base, avg_4q=round(avg4, 4), deviation_pct=round(dev, 2), signal=signal)


@router.get("/buy-windows", response_model=list[BuyWindow])
def buy_windows(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-product buy-now-or-wait signal across the portfolio (Scrum 22).
    Sorted cheapest-relative-to-recent first (best buying opportunities up top)."""
    require_permission(db, current_user, team_id, "costing.view")
    with index_lookup_cache():
        cost_models = _team_cost_models(db, team_id)
        rows = [w for cm in cost_models if (w := _buy_signal(db, cm)) is not None]
    rows.sort(key=lambda w: (w.deviation_pct is None, w.deviation_pct if w.deviation_pct is not None else 0))
    return rows


@router.get("/buy-windows/{cost_model_id}", response_model=BuyWindow)
def buy_window_for_model(
    cost_model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Buy signal for a single cost model — used in the cost-model / product view."""
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "costing.view")
    with index_lookup_cache():
        signal = _buy_signal(db, cm)
    if signal is None:
        raise HTTPException(status_code=400, detail="Cost model has no formula")
    return signal


# ── Scrum 70 (Part 2): lock/hold verdict off the forward should-cost ─────────
# Additive sibling of the backward /buy-windows endpoints above — their
# response shape is untouched, which is also what keeps the Scrum 70 Part 1
# regression test meaningful (forecast storage must not change their output).

_LOCK_THRESHOLD_PCT = _BUY_THRESHOLD_PCT  # same ±3% convention as the backward signal


class LockHoldVerdict(BaseModel):
    cost_model_id: uuid.UUID
    product_name: str
    supplier_name: str | None
    region: str
    currency: str
    horizon_quarters: int
    horizon_year: int
    horizon_quarter: int
    current_should_cost: float
    forecast_should_cost: float | None
    deviation_pct: float | None
    forecast_vintage: datetime | None
    forecast_method: str | None
    verdict: str            # lock | hold | neutral | insufficient
    data_gaps: list[DataGap]


def _lock_hold_verdict(db: Session, cm: CostModel, horizon_quarters: int) -> LockHoldVerdict:
    base = dict(
        cost_model_id=cm.id, product_name=cm.product.name,
        supplier_name=cm.supplier.name if cm.supplier else None,
        region=cm.region, currency=cm.currency, horizon_quarters=horizon_quarters,
    )
    forward = calculate_forward_should_cost(db, cm, horizon_quarters)
    # "Current" is the should-cost at the current quarter, as on every other
    # portfolio view. calculate_should_cost with no target quarter evaluates at
    # the formula's base quarter, i.e. returns the base price, which made the
    # verdict compare the forecast with the base-date price.
    if forward.insufficient or forward.forecast_should_cost is None:
        current = _should_cost_today(db, cm) if cm.current_formula else 0.0
        return LockHoldVerdict(
            **base, horizon_year=forward.horizon_year, horizon_quarter=forward.horizon_quarter,
            current_should_cost=round(current, 4), forecast_should_cost=None, deviation_pct=None,
            forecast_vintage=None, forecast_method=None, verdict="insufficient",
            data_gaps=forward.data_gaps,
        )

    current = _should_cost_today(db, cm)
    dev = (forward.forecast_should_cost - current) / current * 100 if current else 0.0
    # Percent only, per the ticket — the underlying values are index levels,
    # not currency, so no fabricated money-saving figure is quoted here.
    if dev >= _LOCK_THRESHOLD_PCT:
        verdict = "lock"      # price is projected to rise — lock a contract now
    elif dev <= -_LOCK_THRESHOLD_PCT:
        verdict = "hold"      # price is projected to fall — wait
    else:
        verdict = "neutral"

    return LockHoldVerdict(
        **base, horizon_year=forward.horizon_year, horizon_quarter=forward.horizon_quarter,
        current_should_cost=round(current, 4), forecast_should_cost=forward.forecast_should_cost,
        deviation_pct=round(dev, 2), forecast_vintage=forward.forecast_vintage,
        forecast_method=forward.forecast_method, verdict=verdict, data_gaps=forward.data_gaps,
    )


@router.get("/buy-windows/{cost_model_id}/verdict", response_model=LockHoldVerdict)
def buy_window_verdict(
    cost_model_id: uuid.UUID,
    horizon_quarters: int = Query(4, ge=1, le=12),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Forward lock/hold verdict — should-cost forecast horizon_quarters ahead
    vs today's should-cost, using Scrum 70 Part 1's projected index values.
    Same team-scoping gate as the existing single-model buy-window endpoint."""
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "costing.view")
    with index_lookup_cache():
        return _lock_hold_verdict(db, cm, horizon_quarters)
