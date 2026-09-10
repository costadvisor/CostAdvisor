"""Contracts API (Wave 3, SCRUM-79 / MON-1).

Gated on the new `contracts.*` category rather than `costing.view`. The point of
the separate keys: a team can now build a role that runs should-costs but cannot
see contract prices or notice dates. Before this, everyone who could run a
costing could see everything.

⚠️ One residual, stated rather than papered over: `has_permission` falls back to
`TeamMembership.role` for a user with **no** custom role assigned, and that
fallback grants every `view` key — so on a team that never configured roles, a
plain member still sees contracts. The migration grants `contracts.*` to the
seeded Owner/Admin roles and deliberately not to Member, so a team that uses
roles at all gets the separation; a team that uses none has no separation
anywhere in the product by design.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contract import Contract, ContractClause, ContractCostModel
from app.models.cost_model import CostModel, FormulaVersion
from app.models.supplier import Supplier
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.contract import (
    ClauseOut, ContractIn, ContractOut, ContractUpdate, CoveredCostModel,
    VALID_CADENCES, VALID_CLAUSE_TYPES,
)
from app.services.audit import log_event
from app.services.permissions import has_permission, require_permission

router = APIRouter()


def _out(db: Session, c: Contract) -> ContractOut:
    from datetime import datetime, timezone

    products = {}
    cm_ids = [cc.cost_model_id for cc in c.covered]
    if cm_ids:
        for cm in db.query(CostModel).filter(CostModel.id.in_(cm_ids)).all():
            products[cm.id] = cm.product.name if cm.product else None

    days = None
    if c.notice_deadline:
        days = (c.notice_deadline - datetime.now(timezone.utc).date()).days

    return ContractOut(
        id=c.id, supplier_id=c.supplier_id,
        supplier_name=c.supplier.name if c.supplier else None,
        reference=c.reference, term_start=c.term_start, term_end=c.term_end,
        auto_renew=c.auto_renew, notice_days=c.notice_days,
        notice_deadline=c.notice_deadline, days_to_notice=days,
        price_review_cadence=c.price_review_cadence,
        indexation_formula_version_id=c.indexation_formula_version_id,
        currency=c.currency, notes=c.notes,
        clauses=[ClauseOut.model_validate(cl) for cl in c.clauses],
        covered=[
            CoveredCostModel(
                cost_model_id=cc.cost_model_id,
                product=products.get(cc.cost_model_id),
                share_pct=float(cc.share_pct) if cc.share_pct else None,
            )
            for cc in c.covered
        ],
        created_at=c.created_at,
    )


def _validate(db: Session, team_id: uuid.UUID, data, *, clauses, cost_model_ids):
    if data.price_review_cadence and data.price_review_cadence not in VALID_CADENCES:
        raise HTTPException(422, f"Invalid price_review_cadence. Allowed: {sorted(VALID_CADENCES)}")
    for cl in (clauses or []):
        if cl.clause_type not in VALID_CLAUSE_TYPES:
            raise HTTPException(422, f"Invalid clause_type {cl.clause_type!r}. "
                                     f"Allowed: {sorted(VALID_CLAUSE_TYPES)}")
    if data.supplier_id is not None:
        ok = db.query(Supplier).filter(Supplier.id == data.supplier_id,
                                       Supplier.team_id == team_id).first()
        if not ok:
            raise HTTPException(404, "Supplier not found in this team")
    if data.indexation_formula_version_id is not None:
        fv = (
            db.query(FormulaVersion)
            .join(CostModel, CostModel.id == FormulaVersion.cost_model_id)
            .filter(FormulaVersion.id == data.indexation_formula_version_id,
                    CostModel.team_id == team_id)
            .first()
        )
        if not fv:
            raise HTTPException(404, "Formula version not found in this team")
    for cm_id in (cost_model_ids or []):
        if not db.query(CostModel).filter(CostModel.id == cm_id,
                                          CostModel.team_id == team_id).first():
            raise HTTPException(404, f"Cost model {cm_id} not found in this team")


@router.get("/can-access")
def can_access(team_id: uuid.UUID, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    """Whether to show the Contracts entry at all, and whether it is editable.

    Contract prices and notice dates are more sensitive than a should-cost
    curve, which is why `contracts.*` is its own permission category rather than
    riding on `costing.view` — so a role that can run a costing but was not
    given contract access must not even see the nav entry. There is no
    effective-permissions read in this app; the convention is a per-feature
    probe (`formulas/can-edit-platform`, `fx-rates/can-manage-pairs`) and this
    follows it, exposing three booleans rather than the permission model.

    Registered ahead of `/{contract_id}` so the literal segment is never parsed
    as a UUID.
    """
    return {
        "can_view": has_permission(db, current_user, team_id, "contracts.view"),
        "can_edit": has_permission(db, current_user, team_id, "contracts.edit"),
        "can_delete": has_permission(db, current_user, team_id, "contracts.delete"),
    }


@router.get("", response_model=list[ContractOut])
def list_contracts(team_id: uuid.UUID, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    require_permission(db, current_user, team_id, "contracts.view")
    rows = (
        db.query(Contract).filter(Contract.team_id == team_id)
        .order_by(Contract.notice_deadline.asc().nullslast(), Contract.created_at.desc())
        .all()
    )
    return [_out(db, c) for c in rows]


@router.get("/{contract_id}", response_model=ContractOut)
def get_contract(contract_id: uuid.UUID, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(404, "Contract not found")
    require_permission(db, current_user, c.team_id, "contracts.view")
    return _out(db, c)


@router.post("", response_model=ContractOut, status_code=201)
def create_contract(team_id: uuid.UUID, data: ContractIn, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    require_permission(db, current_user, team_id, "contracts.edit")
    _validate(db, team_id, data, clauses=data.clauses, cost_model_ids=data.cost_model_ids)

    c = Contract(
        team_id=team_id, supplier_id=data.supplier_id, reference=data.reference,
        term_start=data.term_start, term_end=data.term_end, auto_renew=data.auto_renew,
        notice_days=data.notice_days, price_review_cadence=data.price_review_cadence,
        indexation_formula_version_id=data.indexation_formula_version_id,
        currency=data.currency, notes=data.notes, created_by=current_user.id,
    )
    # Derived on write, never accepted from the caller.
    c.refresh_notice_deadline()
    db.add(c)
    db.flush()

    for cl in data.clauses:
        db.add(ContractClause(team_id=team_id, contract_id=c.id, **cl.model_dump()))
    for cm_id in data.cost_model_ids:
        db.add(ContractCostModel(team_id=team_id, contract_id=c.id, cost_model_id=cm_id))
    # Flush before serializing: with autoflush off session-wide, the response
    # would otherwise echo empty clause/coverage lists even though the rows
    # persist at commit (the same trap create_cost_model hit in Scrum 28b).
    db.flush()
    db.refresh(c)
    out = _out(db, c)
    log_event(db, team_id, current_user.id, "create", "contract", str(c.id),
              new_value={"reference": c.reference,
                         "notice_deadline": c.notice_deadline.isoformat()
                         if c.notice_deadline else None})
    db.commit()
    return out


@router.put("/{contract_id}", response_model=ContractOut)
def update_contract(contract_id: uuid.UUID, data: ContractUpdate,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(404, "Contract not found")
    require_permission(db, current_user, c.team_id, "contracts.edit")
    _validate(db, c.team_id, data, clauses=data.clauses, cost_model_ids=data.cost_model_ids)

    fields = data.model_dump(exclude_unset=True,
                             exclude={"cost_model_ids", "clauses"})
    for k, v in fields.items():
        setattr(c, k, v)
    # Any change to term_end or notice_days moves the deadline; recompute
    # unconditionally so the stored value can never disagree with its inputs.
    c.refresh_notice_deadline()

    if data.clauses is not None:
        db.query(ContractClause).filter(ContractClause.contract_id == c.id).delete(
            synchronize_session=False)
        for cl in data.clauses:
            db.add(ContractClause(team_id=c.team_id, contract_id=c.id, **cl.model_dump()))
    if data.cost_model_ids is not None:
        db.query(ContractCostModel).filter(ContractCostModel.contract_id == c.id).delete(
            synchronize_session=False)
        for cm_id in data.cost_model_ids:
            db.add(ContractCostModel(team_id=c.team_id, contract_id=c.id, cost_model_id=cm_id))

    db.flush()
    db.refresh(c)
    out = _out(db, c)
    log_event(db, c.team_id, current_user.id, "update", "contract", str(c.id),
              new_value={"notice_deadline": c.notice_deadline.isoformat()
                         if c.notice_deadline else None})
    db.commit()
    return out


@router.delete("/{contract_id}")
def delete_contract(contract_id: uuid.UUID, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(404, "Contract not found")
    require_permission(db, current_user, c.team_id, "contracts.delete")
    log_event(db, c.team_id, current_user.id, "delete", "contract", str(c.id))
    db.delete(c)
    db.commit()
    return {"status": "deleted"}
