"""AI cost modeler (Scrum 32).

    POST   /api/ai-cost-modeler/drafts            suggest a structure
    GET    /api/ai-cost-modeler/drafts            this team's drafts
    GET    /api/ai-cost-modeler/drafts/{id}       one draft
    PUT    /api/ai-cost-modeler/drafts/{id}/lines replace the lines (the refine step)
    POST   /api/ai-cost-modeler/drafts/{id}/promote  -> a real FormulaVersion
    DELETE /api/ai-cost-modeler/drafts/{id}

Suggesting takes `cost_models.edit`: it is the first step of building one, and
gating it lower would let somebody who cannot save a formula spend the model's
time. Promotion takes the same key because it writes one.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.ai_cost_draft import AiCostDraft, AiCostDraftLine
from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.product import Product
from app.models.user import User
from app.routers.auth import get_current_user
from app.services.ai_cost_modeler import (
    SYSTEM_PROMPT, ModelerUnparseable, build_prompt, parse_response,
    promotion_blockers, resolve_indexes, weight_total,
)
from app.services.audit import log_event
from app.services.ollama import ollama_generate
from app.services.permissions import require_permission

router = APIRouter()


class DraftRequest(BaseModel):
    product_name: str = Field(min_length=1, max_length=255)
    sector: str | None = Field(None, max_length=64)
    rough_price: float | None = Field(None, gt=0)
    currency: str | None = Field(None, max_length=3)
    unit: str | None = Field(None, max_length=10)
    region: str | None = Field(None, max_length=20)
    product_id: uuid.UUID | None = None


class LineIn(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    weight_pct: float = Field(gt=0, le=100)
    component_type: str = Field("index", pattern="^(index|fixed)$")
    commodity_id: int | None = None
    suggested_index: str | None = Field(None, max_length=128)
    confidence: str | None = None
    rationale: str | None = None


class LinesUpdate(BaseModel):
    lines: list[LineIn]


class PromoteRequest(BaseModel):
    """Where the promoted formula goes.

    Either onto an existing cost model, or onto a new one for a product — the
    common case is a product nobody has decomposed, which may not have a cost
    model either.
    """
    cost_model_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    supplier_id: int | None = None
    base_price: float = Field(gt=0)
    base_year: int = Field(ge=1990, le=2100)
    base_quarter: int = Field(ge=1, le=4)
    region: str | None = None
    currency: str | None = None


class LineOut(BaseModel):
    id: uuid.UUID
    sort_order: int
    label: str
    weight_pct: float
    component_type: str
    suggested_index: str | None = None
    commodity_id: int | None = None
    # False means the suggested feed name matched nothing we track. Flagged,
    # never dropped.
    index_resolved: bool
    confidence: str | None = None
    rationale: str | None = None

    class Config:
        from_attributes = True


class DraftOut(BaseModel):
    id: uuid.UUID
    product_name: str
    sector: str | None = None
    rough_price: float | None = None
    currency: str | None = None
    unit: str | None = None
    region: str | None = None
    model: str | None = None
    rationale: str | None = None
    status: str
    promoted_cost_model_id: uuid.UUID | None = None
    created_at: datetime
    lines: list[LineOut] = []
    weight_total: float = 0.0
    # Why this cannot be promoted yet. Empty means it can.
    blockers: list[str] = []

    class Config:
        from_attributes = True


def _out(draft: AiCostDraft) -> DraftOut:
    out = DraftOut.model_validate(draft)
    out.weight_total = weight_total(draft.lines)
    out.blockers = promotion_blockers(draft.lines)
    return out


def _get_draft(db: Session, draft_id: uuid.UUID) -> AiCostDraft:
    draft = db.query(AiCostDraft).filter(AiCostDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.post("/drafts", response_model=DraftOut, status_code=201)
async def create_draft(
    payload: DraftRequest,
    team_id: uuid.UUID = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "cost_models.edit")

    if payload.product_id is not None:
        product = db.query(Product).filter(
            Product.id == payload.product_id, Product.team_id == team_id).first()
        if product is None:
            raise HTTPException(status_code=400, detail="Unknown product for this team")

    prompt = build_prompt(payload.product_name, payload.sector, payload.rough_price,
                          payload.currency, payload.unit, payload.region)
    raw = await ollama_generate(prompt, system=SYSTEM_PROMPT)
    if raw is None:
        # In production llm_enabled is False and a cache miss returns None.
        # Saying so is the whole point: an empty draft would read as a
        # considered answer that happened to find nothing.
        raise HTTPException(
            status_code=503,
            detail="The cost modeler is unavailable right now. Build the formula manually, "
                   "or try again once the model is reachable.",
        )

    try:
        parsed = parse_response(raw)
    except ModelerUnparseable as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    lines = resolve_indexes(db, parsed["lines"])

    draft = AiCostDraft(
        team_id=team_id, created_by=current_user.id, product_id=payload.product_id,
        product_name=payload.product_name, sector=payload.sector,
        rough_price=payload.rough_price, currency=payload.currency, unit=payload.unit,
        region=payload.region, model=get_settings().ollama_model,
        rationale=parsed["rationale"], raw_response=raw,
    )
    db.add(draft)
    db.flush()
    for i, line in enumerate(lines):
        db.add(AiCostDraftLine(draft_id=draft.id, sort_order=i, **line))
    db.flush()
    db.refresh(draft)

    log_event(db, team_id, current_user.id, "ai_cost_draft", "ai_cost_draft", str(draft.id),
              new_value={"product_name": draft.product_name, "lines": len(lines)})
    out = _out(draft)
    db.commit()
    return out


@router.get("/drafts", response_model=list[DraftOut])
def list_drafts(
    team_id: uuid.UUID = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "cost_models.view")
    drafts = (
        db.query(AiCostDraft)
        .filter(AiCostDraft.team_id == team_id)
        .order_by(AiCostDraft.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_out(d) for d in drafts]


@router.get("/drafts/{draft_id}", response_model=DraftOut)
def get_draft(
    draft_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    draft = _get_draft(db, draft_id)
    require_permission(db, current_user, draft.team_id, "cost_models.view")
    return _out(draft)


@router.put("/drafts/{draft_id}/lines", response_model=DraftOut)
def replace_lines(
    draft_id: uuid.UUID,
    payload: LinesUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The refine step — correcting the draft is the intended path, not an edge case.

    Replace-as-a-block rather than per-line patching: weights only make sense
    together, and a partial update is how a recipe stops summing to 100 without
    anybody choosing that.
    """
    draft = _get_draft(db, draft_id)
    require_permission(db, current_user, draft.team_id, "cost_models.edit")
    if draft.status != "ai_draft":
        raise HTTPException(status_code=400, detail="This draft has already been promoted")

    incoming = [l.model_dump() for l in payload.lines]
    # A human-supplied commodity_id is trusted; anything still carrying only a
    # name is re-resolved so an edited suggestion binds too.
    for line in incoming:
        line["index_resolved"] = line.get("commodity_id") is not None
    resolve_indexes(db, [l for l in incoming if l["commodity_id"] is None])

    draft.lines.clear()
    db.flush()
    for i, line in enumerate(incoming):
        db.add(AiCostDraftLine(draft_id=draft.id, sort_order=i, **line))
    db.flush()
    db.refresh(draft)
    out = _out(draft)
    db.commit()
    return out


@router.post("/drafts/{draft_id}/promote", response_model=dict, status_code=201)
def promote(
    draft_id: uuid.UUID,
    payload: PromoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Turn an approved draft into a real FormulaVersion.

    The only call that writes anything outside the staging tables, and it
    refuses unless the recipe closes at 100% with every index line bound — both
    failures are silent otherwise.
    """
    draft = _get_draft(db, draft_id)
    require_permission(db, current_user, draft.team_id, "cost_models.edit")
    if draft.status != "ai_draft":
        raise HTTPException(status_code=400, detail="This draft has already been promoted")

    blockers = promotion_blockers(draft.lines)
    if blockers:
        raise HTTPException(status_code=400, detail="; ".join(blockers))

    if payload.cost_model_id is not None:
        cm = db.query(CostModel).filter(
            CostModel.id == payload.cost_model_id, CostModel.team_id == draft.team_id).first()
        if cm is None:
            raise HTTPException(status_code=400, detail="Unknown cost model for this team")
    else:
        product_id = payload.product_id or draft.product_id
        if product_id is None:
            raise HTTPException(
                status_code=400,
                detail="Give either a cost_model_id or a product_id to create one for",
            )
        product = db.query(Product).filter(
            Product.id == product_id, Product.team_id == draft.team_id).first()
        if product is None:
            raise HTTPException(status_code=400, detail="Unknown product for this team")
        cm = CostModel(
            team_id=draft.team_id, product_id=product.id, created_by=current_user.id,
            supplier_id=payload.supplier_id,
            region=payload.region or draft.region or "Europe",
            currency=payload.currency or draft.currency or "USD",
        )
        db.add(cm)
        db.flush()

    fv = FormulaVersion(
        cost_model_id=cm.id, base_price=payload.base_price,
        base_year=payload.base_year, base_quarter=payload.base_quarter,
        # Margin is a line in this recipe (the model is asked for it), so a
        # separate margin on top would double-count it.
        margin_type="pct", margin_value=0,
        notes=f"Promoted from an AI cost estimate ({draft.model}). Estimate, not a measurement.",
    )
    db.add(fv)
    db.flush()

    total = weight_total(draft.lines)
    for line in draft.lines:
        db.add(FormulaComponent(
            formula_version_id=fv.id,
            label=line.label,
            commodity_id=line.commodity_id,
            # Normalised to a fraction of one, the same convention every other
            # writer of this table uses.
            weight=float(line.weight_pct) / total,
            component_type=line.component_type,
        ))

    draft.status = "approved"
    draft.approved_by = current_user.id
    draft.approved_at = datetime.now(timezone.utc)
    draft.promoted_cost_model_id = cm.id
    db.flush()

    log_event(db, draft.team_id, current_user.id, "ai_cost_draft_promoted", "cost_model", str(cm.id),
              new_value={"draft_id": str(draft.id), "provenance": "ai_draft"})
    result = {
        "cost_model_id": str(cm.id),
        "formula_version_id": fv.id,
        # Carried out of the response so a caller cannot show this as a
        # measured formula: the estimate caveat travels with it.
        "provenance": "ai_draft",
    }
    db.commit()
    return result


@router.delete("/drafts/{draft_id}", status_code=204)
def delete_draft(
    draft_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    draft = _get_draft(db, draft_id)
    require_permission(db, current_user, draft.team_id, "cost_models.edit")
    db.delete(draft)
    db.commit()
