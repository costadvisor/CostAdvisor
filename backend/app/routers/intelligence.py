"""Intelligence derived-payload API (Wave 3, SCRUM-75 / INT-1).

    GET  /api/intelligence/combos/{template_id}/{region}
    POST /api/intelligence/combos                        (batch, for the library)
    GET  /api/intelligence/cost-models/{cost_model_id}   (product -> combo)

**The read-path decision, written down before it was built: a denormalised
endpoint with a bounded query budget**, not materialised rows — see
`services/intelligence`'s module docstring for the reasoning and the budget.
The batch route exists because the library today fires one POST per visible
tile, which is tolerable against a team's cost models and does not scale to the
platform catalogue.

The combo route takes **no CostModel and no product**: the Intelligence library
renders the platform formula catalogue with region as a selector, and an
endpoint keyed on a product could not serve that page at all. The cost-model
route resolves product → combo and returns the same numbers.

This ships the derived half of the ID card. The composed editorial + dimensions
half is SCRUM-76's `/api/editorial/cards/...`; the card is two calls by design.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.cost_model import CostModel
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.intelligence import BatchOut, BatchRequest, IntelligenceOut
from app.services.formula_resolver import get_visible_template
from app.services.intelligence import combo_for_cost_model, derive
from app.services.permissions import require_permission

router = APIRouter()


def _out(result, resolved_via: str | None = None) -> IntelligenceOut:
    payload = IntelligenceOut(
        template_id=result.template_id,
        template_code=result.template_code,
        region_requested=result.region_requested,
        coverage_region=result.coverage_region,
        evaluable=result.evaluable,
        reason=result.reason,
        base_price=result.base_price,
        currency=result.currency,
        base_year=result.base_year,
        base_quarter=result.base_quarter,
        series=result.series,
        components=result.components,
        change=result.change or None,
        cycle=result.cycle or None,
        seasonality=result.seasonality or None,
        volatility=result.volatility or None,
        trust=result.trust or None,
        data_gaps=result.data_gaps,
        value_sources=result.value_sources,
        resolved_via=resolved_via,
    )
    return payload


def _visible(db: Session, template_id: uuid.UUID, team_id: uuid.UUID):
    template = get_visible_template(db, template_id, team_id)
    if template is None:
        raise HTTPException(404, "Formula template not found")
    return template


@router.get("/combos/{template_id}/{region}", response_model=IntelligenceOut)
def combo_intelligence(template_id: uuid.UUID, region: str, team_id: uuid.UUID,
                       db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """Every derived number for one formula × region combo.

    No CostModel and no product involved — this is the grain the Intelligence
    library actually renders. A combo with no lines, no base-period anchor or
    nothing priceable returns a payload with nulls and a stated reason rather
    than an error.
    """
    require_permission(db, current_user, team_id, "formulas.view")
    _visible(db, template_id, team_id)
    return _out(derive(db, template_id, region, team_id=team_id))


@router.post("/combos", response_model=BatchOut)
def batch_intelligence(team_id: uuid.UUID, data: BatchRequest,
                       db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """A page of tiles in one request.

    One call per visible tile is what does not scale to the platform catalogue;
    the cap on the batch size is there so this does not become the same problem
    with a bigger blast radius.
    """
    require_permission(db, current_user, team_id, "formulas.view")
    results = []
    for combo in data.combos:
        _visible(db, combo.template_id, team_id)
        results.append(_out(derive(db, combo.template_id, combo.region,
                                   team_id=team_id)))
    return BatchOut(count=len(results), results=results)


@router.get("/cost-models/{cost_model_id}", response_model=IntelligenceOut)
def cost_model_intelligence(cost_model_id: uuid.UUID, team_id: uuid.UUID,
                            db: Session = Depends(get_db),
                            current_user: User = Depends(get_current_user)):
    """The same payload, reached by resolving a team's product to its combo.

    The product is not the thing being derived; it is how Portfolio gets to the
    combo. `resolved_via` says which route it took, so a caller can check the
    resolution rather than trust it.
    """
    require_permission(db, current_user, team_id, "costing.view")
    cost_model = db.query(CostModel).filter(
        CostModel.id == cost_model_id, CostModel.team_id == team_id).first()
    if cost_model is None:
        raise HTTPException(404, "Cost model not found")

    ref = combo_for_cost_model(db, cost_model)
    if ref is None:
        raise HTTPException(
            422,
            "This product is not linked to a catalog formula, so it has no combo "
            "to derive from — link it to a catalog formula first",
        )
    _visible(db, ref.template_id, team_id)
    return _out(derive(db, ref.template_id, ref.region, team_id=team_id),
                resolved_via=ref.via)
