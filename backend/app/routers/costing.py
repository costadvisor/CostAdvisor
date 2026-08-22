import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.cost_model import CostModel
from app.rate_limit import limiter
from app.routers.auth import get_current_user
from app.schemas.costing import (
    ShouldCostRequest, ShouldCostResult, ShouldCostBreakdown,
    EvolutionRequest, EvolutionResult,
    SqueezeRequest, SqueezeResult,
    BriefRequest, BriefResult,
    PriceChangeRequest, PriceChangeResult,
)
from app.services.costing_engine import (
    calculate_should_cost, calculate_evolution,
    calculate_squeeze, calculate_brief,
    calculate_price_change, calculate_should_cost_breakdown,
)
from app.services.permissions import require_permission
from app.services.audit import log_event

router = APIRouter()


@router.post("/should-cost", response_model=ShouldCostResult)
def should_cost(
    data: ShouldCostRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == data.cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "costing.view")

    if not cm.current_formula:
        raise HTTPException(
            status_code=422,
            detail="No formula defined for this cost model. Add components in the Cost Model Builder first.",
        )

    return calculate_should_cost(
        db=db,
        cost_model=cm,
        target_year=data.target_year,
        target_quarter=data.target_quarter,
        normalize_to_incoterm=data.normalize_to_incoterm,
    )


@router.post("/should-cost/breakdown", response_model=ShouldCostBreakdown)
def should_cost_breakdown(
    data: ShouldCostRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Scrum 17 — itemized should-cost: per-component index/weight/base/current/ratio/
    contribution, plus margin, FX, unit and Incoterm adjustments, all summing exactly
    to the displayed should-cost."""
    cm = db.query(CostModel).filter(CostModel.id == data.cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "costing.view")

    if not cm.current_formula:
        raise HTTPException(
            status_code=422,
            detail="No formula defined for this cost model. Add components in the Cost Model Builder first.",
        )

    return calculate_should_cost_breakdown(
        db=db,
        cost_model=cm,
        target_year=data.target_year,
        target_quarter=data.target_quarter,
        normalize_to_incoterm=data.normalize_to_incoterm,
        display_currency=data.display_currency,
        display_unit=data.display_unit,
    )


@router.post("/evolution", response_model=EvolutionResult)
def evolution(
    data: EvolutionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == data.cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "evolution.view")

    return calculate_evolution(db=db, cost_model=cm, request=data)


@router.post("/squeeze", response_model=SqueezeResult)
def squeeze(
    data: SqueezeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == data.cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "squeeze.view")

    return calculate_squeeze(db=db, cost_model=cm, request=data)


@router.post("/brief", response_model=BriefResult)
@limiter.limit("30/minute")
async def brief(
    request: Request,
    data: BriefRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == data.cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "briefs.view")

    result = calculate_brief(db=db, cost_model=cm, request=data)

    # Attempt LLM-enhanced narrative (falls back to rule-based)
    from app.services.narrative import generate_enhanced_narrative

    result.narrative = await generate_enhanced_narrative(
        product_name=result.product_name,
        supplier_name=result.supplier_name,
        drivers=[d.model_dump() for d in result.drivers],
        gap=result.gap,
        gap_pct=result.gap_pct,
        total_impact=result.total_impact,
        currency=result.currency,
        period_label=result.period_label,
        num_periods=len(result.evolution),
    )

    # Audit: assembling a negotiation brief surfaces sensitive cost intelligence
    # (the exportable deliverable) — a security-relevant event per SOC 2 rules.
    log_event(db, cm.team_id, current_user.id, "brief_generated", "cost_model", str(cm.id),
              new_value={"gap": result.gap, "gap_pct": result.gap_pct})
    db.commit()

    return result


@router.post("/price-change", response_model=PriceChangeResult)
def price_change(
    data: PriceChangeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == data.cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "costing.view")

    return calculate_price_change(db=db, cost_model=cm, request=data)
