import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db, impersonating_admin_email_var
from app.models.audit_log import AuditLog
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

# Opening a brief or a negotiation prep assembles the deliverable, which is
# audited as an access to cost intelligence (SOC 2). The pages build it on
# every load (twice under React StrictMode), so the same user opening the same
# model again within this window writes no second row: the log still says who
# opened what and when, and real edits are not buried under page views.
DELIVERABLE_VIEW_WINDOW = timedelta(hours=1)


def log_deliverable_view(db: Session, cm: CostModel, user_id: uuid.UUID, event_type: str,
                         new_value: dict) -> None:
    """log_event for a deliverable view, once per user, model and window.
    An impersonated view is always written."""
    if not impersonating_admin_email_var.get():
        # The StrictMode pair arrives together: hold a lock until this
        # transaction commits so the second request sees the first's row.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                   {"k": f"{event_type}:{user_id}:{cm.id}"})
        since = datetime.now(timezone.utc) - DELIVERABLE_VIEW_WINDOW
        seen = db.query(AuditLog.id).filter(
            AuditLog.team_id == cm.team_id, AuditLog.user_id == user_id,
            AuditLog.event_type == event_type, AuditLog.entity_type == "cost_model",
            AuditLog.entity_id == str(cm.id), AuditLog.timestamp >= since,
        ).first()
        if seen is not None:
            return
    log_event(db, cm.team_id, user_id, event_type, "cost_model", str(cm.id), new_value=new_value)


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
        base_period_label=result.base_period_label,
        unit=result.unit,
        scale=result.current_should_cost,
    )

    # Audit: assembling a negotiation brief surfaces sensitive cost intelligence
    # (the exportable deliverable) — a security-relevant event per SOC 2 rules.
    log_deliverable_view(db, cm, current_user.id, "brief_generated",
                         {"gap": result.gap, "gap_pct": result.gap_pct})
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
