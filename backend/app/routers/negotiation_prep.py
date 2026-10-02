"""Guided negotiation prep (Scrum 29).

    GET/POST   /api/negotiation-prep/{cost_model_id}/claims
    PUT/DELETE /api/negotiation-prep/claims/{claim_id}
    GET        /api/negotiation-prep/{cost_model_id}/prep

The app never predicts the supplier's counter — it has no supplier-cost data,
and a fabricated counter-proposal playbook was deleted once for pretending
otherwise. The supplier's position is an input; the output is evidence.

Gating follows what each call exposes rather than what it touches: logging a
claim is note-taking (`costing.view`, the same tier as posting a cost-model
note), while `/prep` assembles the floor, the drivers and a script off the
brief, so it takes `briefs.view` and is audit-logged the way `/brief` is.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.cost_model import CostModel
from app.models.negotiation_prep import SupplierClaim
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.costing import BriefRequest
from app.schemas.negotiation_prep import (
    AvailableDriver, CheckedClaimOut, ClaimCreate, ClaimOut, ClaimUpdate,
    PositionLadder, PrepOut,
)
from app.services.audit import log_event
from app.services.costing_engine import calculate_brief
from app.services.negotiation_prep import build_script, check_claim
from app.services.permissions import require_permission

router = APIRouter()


def _get_cost_model(db: Session, cost_model_id: uuid.UUID) -> CostModel:
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    # RLS already hides another team's model, so not-visible and not-there are
    # the same 404 and deliberately indistinguishable from outside.
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    return cm


@router.get("/{cost_model_id}/claims", response_model=list[ClaimOut])
def list_claims(
    cost_model_id: uuid.UUID,
    year: int | None = Query(None),
    quarter: int | None = Query(None, ge=1, le=4),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = _get_cost_model(db, cost_model_id)
    require_permission(db, current_user, cm.team_id, "costing.view")
    q = db.query(SupplierClaim).filter(SupplierClaim.cost_model_id == cost_model_id)
    if year is not None:
        q = q.filter(SupplierClaim.year == year)
    if quarter is not None:
        q = q.filter(SupplierClaim.quarter == quarter)
    return q.order_by(SupplierClaim.created_at).all()


@router.post("/{cost_model_id}/claims", response_model=ClaimOut, status_code=201)
def create_claim(
    cost_model_id: uuid.UUID,
    payload: ClaimCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = _get_cost_model(db, cost_model_id)
    require_permission(db, current_user, cm.team_id, "costing.view")
    claim = SupplierClaim(
        team_id=cm.team_id, cost_model_id=cm.id, year=payload.year, quarter=payload.quarter,
        said=payload.said.strip(), driver_label=payload.driver_label,
        claimed_change_pct=payload.claimed_change_pct, created_by=current_user.id,
    )
    db.add(claim)
    db.flush()
    log_event(db, cm.team_id, current_user.id, "supplier_claim", "cost_model", str(cm.id),
              new_value={"said": claim.said[:200], "driver_label": claim.driver_label})
    out = ClaimOut.model_validate(claim)
    db.commit()
    return out


@router.put("/claims/{claim_id}", response_model=ClaimOut)
def update_claim(
    claim_id: uuid.UUID,
    payload: ClaimUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    claim = db.query(SupplierClaim).filter(SupplierClaim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    require_permission(db, current_user, claim.team_id, "costing.view")
    # `said` is deliberately not editable: it is a record of what was actually
    # said, and rewriting it would quietly change what the evidence answers.
    fields = payload.model_dump(exclude_unset=True)
    for field, value in fields.items():
        setattr(claim, field, value)
    db.flush()
    out = ClaimOut.model_validate(claim)
    db.commit()
    return out


@router.delete("/claims/{claim_id}", status_code=204)
def delete_claim(
    claim_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    claim = db.query(SupplierClaim).filter(SupplierClaim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    require_permission(db, current_user, claim.team_id, "costing.view")
    db.delete(claim)
    db.commit()


@router.get("/{cost_model_id}/prep", response_model=PrepOut)
def prep(
    cost_model_id: uuid.UUID,
    year: int | None = Query(None),
    quarter: int | None = Query(None, ge=1, le=4),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Ladder, checked claims and an assembled script, off the brief's own numbers.

    Deliberately reuses `calculate_brief` rather than recomputing: the script a
    buyer reads out and the brief they hand over must not be able to disagree.
    The LLM narrative is NOT invoked here — the brief endpoint does that, and a
    script is template text over engine numbers by design.
    """
    cm = _get_cost_model(db, cost_model_id)
    require_permission(db, current_user, cm.team_id, "briefs.view")

    brief = calculate_brief(
        db=db, cost_model=cm,
        request=BriefRequest(cost_model_id=cm.id, to_year=year, to_quarter=quarter),
    )

    claims = (
        db.query(SupplierClaim)
        .filter(SupplierClaim.cost_model_id == cm.id)
        .order_by(SupplierClaim.created_at)
        .all()
    )
    checked = [check_claim(c, brief.drivers, brief.current_should_cost) for c in claims]
    script = build_script(brief, checked)

    total = sum(d.component_cost for d in brief.drivers if d.component_cost is not None)
    drivers = [
        AvailableDriver(
            label=d.component_label, index_name=d.index_name,
            change_pct=d.index_change_pct, direction=d.direction,
            weight_pct=(round(d.component_cost / total * 100, 1)
                        if total and d.component_cost is not None else None),
        )
        for d in brief.drivers
    ]

    ladder = PositionLadder(
        currency=brief.currency, unit=brief.unit,
        floor=brief.current_floor, should_cost=brief.current_should_cost,
        current_price=brief.current_actual_price, gap=brief.gap, gap_pct=brief.gap_pct,
        # The should-cost has already consumed every verified index movement, so
        # anything above it is unexplained by construction. Only meaningful
        # when they are actually asking more than the target.
        unexplained=(brief.gap if brief.gap is not None and brief.gap > 0 else None),
    )

    log_event(db, cm.team_id, current_user.id, "negotiation_prep_generated", "cost_model", str(cm.id),
              new_value={"claims": len(claims), "gap": brief.gap})
    out = PrepOut(
        cost_model_id=cm.id, product_name=brief.product_name, supplier_name=brief.supplier_name,
        period_label=brief.period_label, ladder=ladder, drivers=drivers,
        claims=[CheckedClaimOut(**vars(c) | {"claim_id": uuid.UUID(c.claim_id)}) for c in checked],
        script=script,
        data_gaps=[f"{g.component_label} ({g.period}): {g.reason}" for g in brief.data_gaps],
    )
    db.commit()
    return out
