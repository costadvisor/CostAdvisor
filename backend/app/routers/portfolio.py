import statistics
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.cost_model import CostModel
from app.models.price_data import ActualPrice
from app.models.actual_volume import ActualVolume
from app.routers.auth import get_current_user
from app.schemas.costing import EvolutionRequest
from app.services.costing_engine import calculate_should_cost, calculate_evolution
from app.services.fx_converter import convert_price
from app.services.permissions import require_permission

router = APIRouter()


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

    cost_models = db.query(CostModel).filter(CostModel.team_id == team_id).all()

    summaries = []
    total_exposure = 0.0
    largest_exposure = 0.0
    largest_exposure_id = None
    models_flagged = 0

    for cm in cost_models:
        fv = cm.current_formula
        if not fv:
            continue

        # Compute current should-cost
        sc_result = calculate_should_cost(db, cm)
        current_sc = sc_result.should_cost

        # Get latest actual price
        latest_price = (
            db.query(ActualPrice)
            .filter(ActualPrice.cost_model_id == cm.id)
            .order_by(ActualPrice.year.desc(), ActualPrice.quarter.desc())
            .first()
        )
        latest_actual = float(latest_price.price) if latest_price else None

        gap = (latest_actual - current_sc) if latest_actual is not None else None
        base_price = float(fv.base_price)
        gap_pct = (gap / base_price * 100) if (gap is not None and base_price) else None

        # Calculate cumulative impact if volumes exist
        cumulative_impact = None
        volumes = db.query(ActualVolume).filter(ActualVolume.cost_model_id == cm.id).all()
        if volumes and gap is not None:
            total_vol = sum(float(v.volume) for v in volumes)
            cumulative_impact = gap * total_vol

        # Flags
        flag_price_drift = abs(gap_pct) > 10 if gap_pct is not None else False
        flag_index_moved = False

        # Check if indices moved >5% since base date without new formula version
        if fv.components:
            from app.services.data_resolver import get_single_index_value
            for comp in fv.components:
                if comp.commodity_id:
                    ref_val = get_single_index_value(
                        db, cm.team_id, comp.commodity_id, cm.region,
                        fv.base_year, fv.base_quarter
                    )
                    # Check most recent quarter
                    from app.services.costing_engine import _default_period_range
                    _, _, to_y, to_q = _default_period_range(db, cm)
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
                from app.services.costing_engine import _default_period_range
                _, _, to_y, to_q = _default_period_range(db, cm)
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

    cost_models = db.query(CostModel).filter(CostModel.team_id == team_id).all()
    rows = []
    for cm in cost_models:
        fv = cm.current_formula
        if not fv:
            continue
        current_sc = calculate_should_cost(db, cm).should_cost

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
                from app.services.costing_engine import _default_period_range
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
    current = calculate_should_cost(db, cm).should_cost
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
    cost_models = db.query(CostModel).filter(CostModel.team_id == team_id).all()
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
    signal = _buy_signal(db, cm)
    if signal is None:
        raise HTTPException(status_code=400, detail="Cost model has no formula")
    return signal
