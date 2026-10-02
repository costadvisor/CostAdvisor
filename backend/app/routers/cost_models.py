import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.product import Product
from app.models.cost_model import CostModel, FormulaVersion, FormulaComponent
from app.models.index_data import CommodityIndex
from app.routers.auth import get_current_user
from app.schemas.cost_model import (
    CostModelCreate, CostModelUpdate, CostModelOut,
    FormulaVersionCreate, FormulaVersionOut,
)
from app.services.audit import log_event
from app.services.permissions import require_permission
from app.services.formula_resolver import FormulaChainError, assert_valid_nesting, get_visible_coverage

router = APIRouter()


def resolve_commodity_id(db: Session, name: str) -> int | None:
    if not name:
        return None
    commodity = db.query(CommodityIndex).filter(CommodityIndex.name == name).first()
    return commodity.id if commodity else None


def _resolve_component_fields(db: Session, comp, team_id=None, parent_cost_model_id=None) -> dict:
    """Turn a FormulaComponentItem into FormulaComponent kwargs (Scrum 28b).

    An explicit commodity_id (already resolved by the caller, e.g. via GET
    /formulas/{id}/resolve) wins over a fragile exact-name lookup; a supplied
    id that doesn't exist is rejected rather than silently stored — that
    silent-store is the exact bug that let a broken link masquerade as a
    healthy fixed line. component_type is inferred from whichever intent
    signal is present when the caller doesn't send one explicitly, so
    existing callers that never send it keep behaving exactly as before.
    """
    commodity_id = comp.commodity_id
    if commodity_id is not None:
        if not db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first():
            raise HTTPException(status_code=400, detail=f"Unknown commodity_id: {commodity_id}")
    else:
        commodity_id = resolve_commodity_id(db, comp.commodity_name)

    child_id = comp.child_cost_model_id
    if child_id is not None:
        child = db.query(CostModel).filter(
            CostModel.id == child_id, CostModel.team_id == team_id
        ).first()
        # Not only RLS: a nested model has to belong to the same team, or a
        # price would depend on a row this team can neither see nor maintain.
        if child is None:
            raise HTTPException(status_code=400, detail="Unknown or inaccessible child_cost_model_id")
        if parent_cost_model_id is not None:
            try:
                assert_valid_nesting(db, parent_cost_model_id, child_id)
            except FormulaChainError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

    component_type = comp.component_type or (
        "model" if child_id
        else "index" if (commodity_id or comp.commodity_name)
        else "fixed"
    )
    # Stated explicitly rather than inferred away: a line cannot be both.
    if child_id is not None and commodity_id is not None:
        raise HTTPException(
            status_code=400,
            detail="A component is either index-linked or a sub-model, not both",
        )

    return dict(
        label=comp.label,
        commodity_id=commodity_id,
        weight=comp.weight,
        component_type=component_type,
        depth=comp.depth,
        via_template_id=comp.via_template_id,
        line_region=comp.line_region,
        is_proxy=comp.is_proxy,
        child_cost_model_id=child_id,
    )


def _validate_source_coverage(db: Session, team_id: uuid.UUID, source_coverage_id: uuid.UUID | None) -> None:
    if source_coverage_id is None:
        return
    if get_visible_coverage(db, source_coverage_id, team_id) is None:
        raise HTTPException(status_code=400, detail="Unknown or inaccessible source_coverage_id")


def _build_cost_model_out(cm: CostModel) -> CostModelOut:
    out = CostModelOut.model_validate(cm)
    out.product_name = cm.product.name if cm.product else None
    out.product_reference = cm.product.formula if cm.product else None
    out.product_unit = cm.product.unit if cm.product else None
    out.product_active_content = cm.product.active_content if cm.product else None
    out.supplier_name = cm.supplier.name if cm.supplier else None
    return out


@router.get("/", response_model=list[CostModelOut])
def list_cost_models(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "cost_models.view")
    models = db.query(CostModel).filter(CostModel.team_id == team_id).all()
    return [_build_cost_model_out(cm) for cm in models]


@router.post("/", response_model=CostModelOut, status_code=201)
def create_cost_model(
    team_id: uuid.UUID,
    data: CostModelCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "cost_models.edit")

    # Verify product exists and belongs to team
    product = db.query(Product).filter(Product.id == data.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if product.team_id != team_id:
        raise HTTPException(status_code=403, detail="Product does not belong to this team")

    _validate_source_coverage(db, team_id, data.formula.source_coverage_id)

    cm = CostModel(
        team_id=team_id,
        product_id=data.product_id,
        supplier_id=data.supplier_id,
        destination_country=data.destination_country,
        destination_region=data.destination_region,
        region=data.region,
        currency=data.currency,
        incoterm=data.incoterm,
        created_by=current_user.id,
    )
    db.add(cm)
    db.flush()

    # Create first formula version. Version-level incoterm wins; otherwise the
    # cost model's default flows in so existing callers don't regress.
    fv = FormulaVersion(
        cost_model_id=cm.id,
        base_price=data.formula.base_price,
        base_year=data.formula.base_year,
        base_quarter=data.formula.base_quarter,
        margin_type=data.formula.margin_type,
        margin_value=data.formula.margin_value,
        incoterm=data.formula.incoterm or data.incoterm,
        named_place=data.formula.named_place,
        landed_cost_adjustments=data.formula.landed_cost_adjustments,
        notes=data.formula.notes,
        formula_type=data.formula.formula_type,
        expression=data.formula.expression,
        variables=data.formula.variables,
        source_coverage_id=data.formula.source_coverage_id,
        link_mode=data.formula.link_mode,
    )
    db.add(fv)
    db.flush()

    for comp in data.formula.components:
        # No parent id yet on create, so there is nothing a child could cycle
        # back to; the team-ownership check still runs.
        fc = FormulaComponent(formula_version_id=fv.id,
                              **_resolve_component_fields(db, comp, team_id))
        db.add(fc)
    # Autoflush is off session-wide — without this, the lazy-loaded
    # formula_versions[0].components below re-queries before these pending
    # inserts exist, so the create response echoes an empty component list
    # even though they persist correctly at commit.
    db.flush()

    log_event(db, team_id, current_user.id, "create", "cost_model", str(cm.id),
              new_value={"product_id": str(data.product_id), "region": data.region, "currency": data.currency})
    # Build response while still in transaction so lazy-loaded relationships (product, supplier)
    # are accessible without opening a second transaction after commit.
    result = _build_cost_model_out(cm)
    db.commit()
    return result


@router.get("/{cost_model_id}", response_model=CostModelOut)
def get_cost_model(
    cost_model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "cost_models.view")
    return _build_cost_model_out(cm)


@router.put("/{cost_model_id}", response_model=CostModelOut)
def update_cost_model(
    cost_model_id: uuid.UUID,
    data: CostModelUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "cost_models.edit")

    changes = {}
    for field in ["supplier_id", "destination_country", "destination_region", "region", "currency", "incoterm"]:
        val = getattr(data, field, None)
        if val is not None:
            changes[field] = {"old": str(getattr(cm, field)), "new": str(val)}
            setattr(cm, field, val)

    if changes:
        log_event(db, cm.team_id, current_user.id, "update", "cost_model", str(cm.id), new_value=changes)
    result = _build_cost_model_out(cm)
    db.commit()
    return result


@router.delete("/{cost_model_id}")
def delete_cost_model(
    cost_model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "cost_models.delete")
    team_id = cm.team_id
    log_event(db, team_id, current_user.id, "delete", "cost_model", str(cm.id),
              previous_value={"product_id": str(cm.product_id), "region": cm.region})
    db.delete(cm)
    db.commit()
    return {"status": "deleted"}


@router.post("/{cost_model_id}/renegotiate", response_model=FormulaVersionOut, status_code=201)
def renegotiate(
    cost_model_id: uuid.UUID,
    data: FormulaVersionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upsert a formula version for a quarter. If one exists for the same quarter, update in place."""
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "cost_models.edit")
    _validate_source_coverage(db, cm.team_id, data.source_coverage_id)

    # Check if a version exists for this quarter
    existing = db.query(FormulaVersion).filter(
        FormulaVersion.cost_model_id == cost_model_id,
        FormulaVersion.base_year == data.base_year,
        FormulaVersion.base_quarter == data.base_quarter,
    ).first()

    if existing:
        # Update in place
        existing.base_price = data.base_price
        existing.margin_type = data.margin_type
        existing.margin_value = data.margin_value
        existing.incoterm = data.incoterm or cm.incoterm
        existing.named_place = data.named_place
        existing.landed_cost_adjustments = data.landed_cost_adjustments
        existing.notes = data.notes
        existing.formula_type = data.formula_type
        existing.expression = data.expression
        existing.variables = data.variables
        existing.source_coverage_id = data.source_coverage_id
        existing.link_mode = data.link_mode
        existing.updated_at = datetime.now(timezone.utc)

        # Delete old components, create new ones
        db.query(FormulaComponent).filter(
            FormulaComponent.formula_version_id == existing.id
        ).delete()

        for comp in data.components:
            fc = FormulaComponent(formula_version_id=existing.id,
                                  **_resolve_component_fields(db, comp, cm.team_id, cm.id))
            db.add(fc)

        db.flush()
        log_event(db, cm.team_id, current_user.id, "update", "formula_version", str(existing.id),
                  new_value={"cost_model_id": str(cost_model_id),
                             "quarter": f"Q{data.base_quarter}-{data.base_year}",
                             "base_price": str(existing.base_price), "margin_type": existing.margin_type})
        # Build the response while the session is live: expire first so the
        # freshly-swapped components reload (the bulk delete + add left the
        # cached collection stale), and so each component's commodity_name — a
        # lazy relationship — resolves before commit. Expunging first (the old
        # pattern) detached the components and 500'd on that lazy load.
        db.expire(existing)
        out = FormulaVersionOut.model_validate(existing)
        db.commit()
        return out
    else:
        # New quarter — create new version
        fv = FormulaVersion(
            cost_model_id=cost_model_id,
            base_price=data.base_price,
            base_year=data.base_year,
            base_quarter=data.base_quarter,
            margin_type=data.margin_type,
            margin_value=data.margin_value,
            incoterm=data.incoterm or cm.incoterm,
            named_place=data.named_place,
            landed_cost_adjustments=data.landed_cost_adjustments,
            notes=data.notes,
            formula_type=data.formula_type,
            expression=data.expression,
            variables=data.variables,
            source_coverage_id=data.source_coverage_id,
            link_mode=data.link_mode,
        )
        db.add(fv)
        db.flush()

        for comp in data.components:
            fc = FormulaComponent(formula_version_id=fv.id,
                                  **_resolve_component_fields(db, comp, cm.team_id, cm.id))
            db.add(fc)

        db.flush()
        log_event(db, cm.team_id, current_user.id, "create", "formula_version", str(fv.id),
                  new_value={"cost_model_id": str(cost_model_id),
                             "quarter": f"Q{data.base_quarter}-{data.base_year}",
                             "base_price": str(fv.base_price), "margin_type": fv.margin_type})
        # Build the response while session-bound so components (and each lazy
        # commodity_name) load; expire first so the just-added components attach.
        db.expire(fv)
        out = FormulaVersionOut.model_validate(fv)
        db.commit()
        return out


@router.get("/{cost_model_id}/versions", response_model=list[FormulaVersionOut])
def list_versions(
    cost_model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "cost_models.view")
    return (
        db.query(FormulaVersion)
        .filter(FormulaVersion.cost_model_id == cost_model_id)
        .order_by(FormulaVersion.base_year.desc(), FormulaVersion.base_quarter.desc())
        .all()
    )


@router.delete("/{cost_model_id}/versions/{version_id}")
def delete_version(
    cost_model_id: uuid.UUID,
    version_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "cost_models.delete")

    fv = db.query(FormulaVersion).filter(
        FormulaVersion.id == version_id,
        FormulaVersion.cost_model_id == cost_model_id,
    ).first()
    if not fv:
        raise HTTPException(status_code=404, detail="Formula version not found")

    # Don't allow deleting the last version
    count = db.query(FormulaVersion).filter(
        FormulaVersion.cost_model_id == cost_model_id,
    ).count()
    if count <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the only formula version")

    log_event(db, cm.team_id, current_user.id, "delete", "formula_version", str(fv.id),
              previous_value={"quarter": f"Q{fv.base_quarter}-{fv.base_year}", "base_price": str(fv.base_price)})
    db.delete(fv)
    db.commit()
    return {"status": "deleted"}


@router.get("/{cost_model_id}/nestable")
def list_nestable(
    cost_model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cost models this one may nest, with the ones that would cycle excluded.

    Offering a candidate the save would reject is how a picker teaches somebody
    to distrust it, so the exclusion happens here rather than as an error after
    the fact. The same walk backs both.
    """
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, cm.team_id, "cost_models.view")

    candidates = (
        db.query(CostModel)
        .filter(CostModel.team_id == cm.team_id, CostModel.id != cm.id)
        .all()
    )
    out = []
    for c in candidates:
        try:
            assert_valid_nesting(db, cm.id, c.id)
        except FormulaChainError as exc:
            out.append({
                "cost_model_id": str(c.id),
                "product_name": c.product.name if c.product else None,
                "supplier_name": c.supplier.name if c.supplier else None,
                "region": c.region,
                "eligible": False,
                # Named, not just excluded: "why is X missing from this list"
                # is the first question anybody asks.
                "reason": str(exc),
            })
            continue
        fv = c.current_formula
        out.append({
            "cost_model_id": str(c.id),
            "product_name": c.product.name if c.product else None,
            "supplier_name": c.supplier.name if c.supplier else None,
            "region": c.region,
            "eligible": fv is not None and fv.formula_type != "advanced",
            "reason": (
                None if fv is not None and fv.formula_type != "advanced"
                else "has no formula yet" if fv is None
                else "uses an advanced expression, which cannot be nested"
            ),
        })
    out.sort(key=lambda r: (not r["eligible"], r["product_name"] or ""))
    return {"candidates": out}


@router.post("/{cost_model_id}/clone", response_model=CostModelOut, status_code=201)
def clone_cost_model(
    cost_model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    original = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Cost model not found")
    require_permission(db, current_user, original.team_id, "cost_models.edit")

    clone = CostModel(
        team_id=original.team_id,
        product_id=original.product_id,
        supplier_id=original.supplier_id,
        destination_country=original.destination_country,
        destination_region=original.destination_region,
        region=original.region,
        currency=original.currency,
        incoterm=original.incoterm,
        created_by=current_user.id,
    )
    db.add(clone)
    db.flush()

    # Clone the current formula version
    current_fv = original.current_formula
    if current_fv:
        fv = FormulaVersion(
            cost_model_id=clone.id,
            base_price=current_fv.base_price,
            base_year=current_fv.base_year,
            base_quarter=current_fv.base_quarter,
            margin_type=current_fv.margin_type,
            margin_value=current_fv.margin_value,
            incoterm=current_fv.incoterm,
            named_place=current_fv.named_place,
            landed_cost_adjustments=current_fv.landed_cost_adjustments,
            formula_type=current_fv.formula_type,
            expression=current_fv.expression,
            variables=current_fv.variables,
            # Scrum 28b — cloning is meant to produce an equivalent formula;
            # silently dropping the catalog link on clone would be exactly
            # the kind of quiet behavior this scrum is about closing.
            source_coverage_id=current_fv.source_coverage_id,
            link_mode=current_fv.link_mode,
        )
        db.add(fv)
        db.flush()

        for comp in current_fv.components:
            fc = FormulaComponent(
                formula_version_id=fv.id,
                label=comp.label,
                commodity_id=comp.commodity_id,
                weight=comp.weight,
                component_type=comp.component_type,
                depth=comp.depth,
                via_template_id=comp.via_template_id,
                line_region=comp.line_region,
                is_proxy=comp.is_proxy,
                # Scrum 27 — same reasoning as source_coverage_id above: a
                # clone that quietly lost its sub-models would not be an
                # equivalent formula.
                child_cost_model_id=comp.child_cost_model_id,
            )
            db.add(fc)
        db.flush()  # see the matching comment in create_cost_model

    log_event(db, clone.team_id, current_user.id, "clone", "cost_model", str(clone.id),
              new_value={"source_cost_model_id": str(original.id)})
    result = _build_cost_model_out(clone)
    db.commit()
    return result
