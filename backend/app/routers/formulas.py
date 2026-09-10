import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.formula_template import (
    FormulaTemplate,
    FormulaTemplateComponent,
    FormulaRegionCoverage,
)
from app.constants.trust import GRADE_SEVERITY, GRADE_UNRATED
from app.models.formula_estimator import EstimatorProposal, EstimatorProposalLine
from app.models.index_data import CommodityIndex
from app.models.region import Region
from app.models.team import TeamMembership
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.formula_estimator import (
    BacktestReportOut,
    EstimatorProposalOut,
)
from app.schemas.formula_template import (
    TrustQueueOut, TrustQueueRow, TrustRecomputeOut,
    FormulaTemplateCreate,
    FormulaTemplateUpdate,
    FormulaTemplateOut,
    FormulaTemplateForkRequest,
    FormulaComponentsReplace,
    FormulaComponentOut,
    FormulaCoverageIn,
    FormulaCoverageOut,
    FormulaResolveOut,
    ResolvedLineOut,
    FormulaEvaluateOut,
)
from app.schemas.negotiation_position import NegotiationResponseOut
from app.constants.trust import GRADE_CAVEATS
from app.services.audit import log_event, log_platform_event
from app.services.trust import (
    QUEUE_ORDERS, apply_assessment, assess, recompute_all as trust_recompute_all,
    review_queue, sign_off as trust_sign_off,
)
from app.services.formula_estimator import (
    approve_proposal,
    create_or_update_proposal,
    reject_proposal,
    run_backtest,
)
from app.services.formula_resolver import (
    FormulaChainError,
    assert_valid_chain_input,
    evaluate_weighted_template,
    flatten_components,
    resolve_coverage,
    get_visible_template,
)
from app.services.negotiation_position import compute_negotiation_position
from app.services.permissions import require_permission, require_platform_permission, has_platform_permission

router = APIRouter()


def _first_team_id(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    """The actor's first team, used only to attribute a *team* template's audit.

    Never used for platform templates any more: attributing a platform action to
    whichever team the actor happens to belong to put the event in an unrelated
    tenant's log, and skipped it entirely for an actor with no team.
    `log_platform_event` covers that case.
    """
    m = db.query(TeamMembership).filter(TeamMembership.user_id == user_id).first()
    return m.team_id if m else None


def _audit_template_event(db: Session, template, user_id: uuid.UUID,
                          event_type: str, entity_type: str, entity_id: str,
                          new_value: dict | None = None) -> None:
    """Audit an action on a template, whichever tenancy it has."""
    if template.team_id is not None:
        log_event(db, template.team_id, user_id, event_type, entity_type,
                  entity_id, new_value=new_value)
        return
    log_platform_event(db, user_id, event_type, entity_type, entity_id,
                       new_value=new_value)


def _coverage_out(db: Session, row) -> "FormulaCoverageOut":
    """Serialise a combo, resolving the reviewer's display identity from the FK.

    Resolved here rather than stored, which is the whole point of the FK: a
    display name read from the user row still resolves after they change their
    email, and the old free-text column did not.
    """
    out = FormulaCoverageOut.model_validate(row)
    out.trust_caveat = GRADE_CAVEATS.get(row.trust_grade)
    if row.reviewed_by_id:
        reviewer = db.query(User).filter(User.id == row.reviewed_by_id).first()
        if reviewer:
            out.reviewed_by_name = reviewer.display_name or reviewer.email
    elif row.reviewed_by:
        # A sign-off that predates the FK: show what was recorded, flagged by
        # the absence of an id rather than silently passed off as resolved.
        out.reviewed_by_name = row.reviewed_by
    return out


def _require_template_approve(db: Session, user: User, template) -> None:
    """Sign-off is a different right from authorship.

    `_require_template_edit` resolves to `formulas.edit`, so the person who
    authored the weights could also vouch for them — which is not a review. The
    approve key comes from SCRUM-76's single permission revision; this consumes
    it and adds none of its own.
    """
    if template.team_id is None:
        require_platform_permission(db, user, "content.approve")
    else:
        require_permission(db, user, template.team_id, "content.approve")


# How many type-codes to name per reason in the rollup. The point of carrying
# reasons up to the list at all is that a reviewer can see *what* to go and look
# at without opening the combo; the full set lives on the coverage row.
_ROLLUP_SUBJECT_CAP = 6


def _trust_rollups(db: Session, template_ids: list[uuid.UUID]) -> dict:
    """Per-template rollup of the SCRUM-78 combo grades. One batch query.

    Trust is graded per (template, region) because a recipe can resolve cleanly
    in one region and hit a dead series in another. The catalog list renders one
    row per template, so it needs the *worst* grade plus how many combos are
    queued — anything softer would let a blocked region hide behind a healthy one.
    """
    if not template_ids:
        return {}
    rows = (
        db.query(FormulaRegionCoverage)
        .filter(FormulaRegionCoverage.template_id.in_(template_ids))
        .all()
    )
    acc: dict = {}
    for row in rows:
        bucket = acc.setdefault(row.template_id, {
            "combo_count": 0, "needs_review_count": 0, "reviewed_count": 0,
            "grades": {}, "reasons": {},
        })
        bucket["combo_count"] += 1
        if row.needs_review:
            bucket["needs_review_count"] += 1
        if row.reviewed_at is not None:
            bucket["reviewed_count"] += 1
        grade = row.trust_grade or GRADE_UNRATED
        bucket["grades"][grade] = bucket["grades"].get(grade, 0) + 1
        for reason in (row.trust_inputs or {}).get("reasons", []) or []:
            subjects = bucket["reasons"].setdefault(reason.get("reason"), set())
            subjects.update(reason.get("subjects") or [])

    out = {}
    for template_id, bucket in acc.items():
        worst = min(bucket["grades"], key=lambda g: GRADE_SEVERITY.get(g, 9))
        out[template_id] = {
            "worst_grade": worst,
            "caveat": GRADE_CAVEATS.get(worst),
            "combo_count": bucket["combo_count"],
            "needs_review_count": bucket["needs_review_count"],
            "reviewed_count": bucket["reviewed_count"],
            "grades": bucket["grades"],
            "reasons": [
                {"reason": reason, "subjects": sorted(subjects)[:_ROLLUP_SUBJECT_CAP],
                 "subject_count": len(subjects)}
                for reason, subjects in sorted(bucket["reasons"].items())
            ],
        }
    return out


def _enrich_with_emails(db: Session, templates: list[FormulaTemplate]) -> list[FormulaTemplateOut]:
    """Batch-load creator emails + taxonomy names to avoid N+1 queries."""
    from app.models.chemical_family import ChemicalFamily
    from app.models.subfamily import Subfamily

    creator_ids = list({t.created_by for t in templates})
    email_map = {
        u.id: u.email
        for u in db.query(User).filter(User.id.in_(creator_ids)).all()
    } if creator_ids else {}
    family_ids = list({t.family_id for t in templates if t.family_id is not None})
    family_map = {
        f.id: (f.code, f.name)
        for f in db.query(ChemicalFamily).filter(ChemicalFamily.id.in_(family_ids)).all()
    } if family_ids else {}
    sub_ids = list({t.subfamily_id for t in templates if t.subfamily_id is not None})
    sub_map = {
        s.id: s.name
        for s in db.query(Subfamily).filter(Subfamily.id.in_(sub_ids)).all()
    } if sub_ids else {}

    trust_map = _trust_rollups(db, [t.id for t in templates])

    result = []
    for t in templates:
        out = FormulaTemplateOut.model_validate(t)
        out.creator_email = email_map.get(t.created_by)
        if t.family_id in family_map:
            out.family_code, out.family_name = family_map[t.family_id]
        out.subfamily_name = sub_map.get(t.subfamily_id)
        out.trust_summary = trust_map.get(t.id)
        result.append(out)
    return result


@router.get("/", response_model=list[FormulaTemplateOut])
def list_formulas(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "formulas.view")
    templates = (
        db.query(FormulaTemplate)
        .filter(
            or_(FormulaTemplate.team_id == None, FormulaTemplate.team_id == team_id)  # noqa: E711
        )
        .order_by(FormulaTemplate.team_id.nullsfirst(), FormulaTemplate.name)
        .all()
    )
    return _enrich_with_emails(db, templates)


@router.post("/", response_model=FormulaTemplateOut, status_code=201)
def create_formula(
    data: FormulaTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if data.team_id is None:
        require_platform_permission(db, current_user, "formulas.edit")
    else:
        require_permission(db, current_user, data.team_id, "formulas.edit")

    template = FormulaTemplate(
        team_id=data.team_id,
        created_by=current_user.id,
        name=data.name,
        description=data.description,
        expression=data.expression,
        variables=data.variables,
    )
    db.add(template)
    db.flush()

    audit_team_id = data.team_id or _first_team_id(db, current_user.id)
    if audit_team_id:
        log_event(db, audit_team_id, current_user.id, "create", "formula_template",
                  str(template.id),
                  new_value={"name": data.name, "platform": data.team_id is None})

    db.expunge(template)
    db.commit()

    refreshed = db.query(FormulaTemplate).filter(FormulaTemplate.id == template.id).first()
    out = FormulaTemplateOut.model_validate(refreshed)
    out.creator_email = current_user.email
    return out


@router.put("/{template_id}", response_model=FormulaTemplateOut)
def update_formula(
    template_id: uuid.UUID,
    data: FormulaTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    template = db.query(FormulaTemplate).filter(FormulaTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Formula template not found")

    if template.team_id is None:
        require_platform_permission(db, current_user, "formulas.edit")
    else:
        require_permission(db, current_user, template.team_id, "formulas.edit")

    prev = {"name": template.name, "expression": template.expression}
    if data.name is not None:
        template.name = data.name
    if data.description is not None:
        template.description = data.description
    if data.expression is not None:
        template.expression = data.expression
    if data.variables is not None:
        template.variables = data.variables

    audit_team_id = template.team_id or _first_team_id(db, current_user.id)
    if audit_team_id:
        log_event(db, audit_team_id, current_user.id, "update", "formula_template",
                  str(template.id), previous_value=prev,
                  new_value={"name": template.name})

    db.flush()
    db.expunge(template)
    db.commit()

    refreshed = db.query(FormulaTemplate).filter(FormulaTemplate.id == template_id).first()
    out = FormulaTemplateOut.model_validate(refreshed)
    out.creator_email = current_user.email
    return out


@router.post("/{template_id}/fork", response_model=FormulaTemplateOut, status_code=201)
def fork_formula(
    template_id: uuid.UUID,
    data: FormulaTemplateForkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Copy a platform formula template into a team as an editable, private fork —
    recipe (components) and per-region coverage included. `origin_id` keeps lineage
    so the team can rename/retune its copy without losing the link to the original.
    Only platform templates are forkable; one fork per team."""
    require_permission(db, current_user, data.team_id, "formulas.edit")

    source = db.query(FormulaTemplate).filter(FormulaTemplate.id == template_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Formula template not found")
    if source.team_id is not None:
        raise HTTPException(status_code=400, detail="Only platform formulas can be forked")

    existing = (
        db.query(FormulaTemplate)
        .filter(FormulaTemplate.team_id == data.team_id, FormulaTemplate.origin_id == source.id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="This formula is already forked for the team")

    fork = FormulaTemplate(
        team_id=data.team_id,
        origin_id=source.id,
        created_by=current_user.id,
        name=source.name,
        code=source.code,
        family_id=source.family_id,
        subfamily_id=source.subfamily_id,
        catalog_meta=source.catalog_meta,
        description=source.description,
        expression=source.expression,
        variables=source.variables,
    )
    db.add(fork)
    db.flush()

    # Copy the weighted recipe. input_template_id chaining is left pointing at the
    # platform originals (team formulas may chain platform templates — Scrum 58 scope).
    for c in db.query(FormulaTemplateComponent).filter(FormulaTemplateComponent.template_id == source.id).all():
        db.add(FormulaTemplateComponent(
            template_id=fork.id, name=c.name, component_type=c.component_type,
            commodity_id=c.commodity_id, input_template_id=c.input_template_id,
            region=c.region, weight_pct=c.weight_pct, is_proxy=c.is_proxy, sort_order=c.sort_order,
        ))
    # Copy per-region coverage (base price / margin).
    #
    # **A platform sign-off does not carry into a fork** — the open call SCRUM-78
    # asks to be made explicitly. The platform expert vouched for the platform
    # numbers, and a fork exists precisely so the team can change them; carrying
    # the tick over would display an approval of a recipe nobody approved. The
    # fingerprint is dropped for the same reason, and the grade is recomputed
    # below from the fork's own rows so a later edit regrades the fork and not
    # its origin.
    for cov in db.query(FormulaRegionCoverage).filter(FormulaRegionCoverage.template_id == source.id).all():
        db.add(FormulaRegionCoverage(
            template_id=fork.id, region=cov.region, base_price=cov.base_price,
            currency=cov.currency, margin_pct=cov.margin_pct,
            base_year=cov.base_year, base_quarter=cov.base_quarter,
            data_confidence=cov.data_confidence, coverage_tier=cov.coverage_tier,
            proxy_density_tier=cov.proxy_density_tier,
            needs_review=cov.needs_review, review_metadata=cov.review_metadata,
        ))
    db.flush()
    for coverage in db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.template_id == fork.id).all():
        apply_assessment(db, coverage)

    log_event(db, data.team_id, current_user.id, "fork", "formula_template",
              str(fork.id), new_value={"name": fork.name, "origin_id": str(source.id)})
    db.expunge(fork)
    db.commit()

    refreshed = db.query(FormulaTemplate).filter(FormulaTemplate.id == fork.id).first()
    out = FormulaTemplateOut.model_validate(refreshed)
    out.creator_email = current_user.email
    return out


@router.delete("/{template_id}")
def delete_formula(
    template_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    template = db.query(FormulaTemplate).filter(FormulaTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Formula template not found")

    if template.team_id is None:
        require_platform_permission(db, current_user, "formulas.delete")
    else:
        require_permission(db, current_user, template.team_id, "formulas.delete")

    # A template chained into another formula can't be deleted — the visible
    # pre-check gives a friendly message; the FK (no ondelete) backstops
    # references RLS hides from this caller.
    ref = (
        db.query(FormulaTemplateComponent)
        .filter(FormulaTemplateComponent.input_template_id == template_id)
        .first()
    )
    if ref:
        raise HTTPException(
            status_code=409,
            detail="This formula is used as an input by another formula — remove that reference first",
        )

    audit_team_id = template.team_id or _first_team_id(db, current_user.id)
    if audit_team_id:
        log_event(db, audit_team_id, current_user.id, "delete", "formula_template",
                  str(template_id), previous_value={"name": template.name})

    db.delete(template)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This formula is used as an input by another formula — remove that reference first",
        )
    return {"status": "deleted"}


@router.get("/review-queue", response_model=TrustQueueOut)
def get_review_queue(
    team_id: uuid.UUID,
    grade: list[str] | None = Query(None),
    # Omitted = no filter. Unit 11 built this because coverage was only
    # listable per template, so it is the cross-library coverage listing as well
    # as the review queue — the Intelligence catalogue needs every (template,
    # region) pair, not just the queued ones. Callers that want the queue send
    # `needs_review=true` explicitly.
    needs_review: bool | None = Query(None),
    order_by: str = Query("severity"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The review queue **across the whole library**, filtered by grade.

    Coverage was only listable per template before this
    (`GET /{template_id}/coverage`), so a console had nothing to read and there
    was no way to order by anything but region within one formula. Registered
    ahead of the `/{template_id}` routes so `review-queue` is never parsed as a
    UUID.
    """
    require_permission(db, current_user, team_id, "formulas.view")
    try:
        rows, total = review_queue(
            db, team_id, grades=grade, needs_review=needs_review,
            order_by=order_by, limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return TrustQueueOut(
        total=total, order_by=order_by,
        rows=[TrustQueueRow(**r) for r in rows],
    )


@router.post("/trust/recompute", response_model=TrustRecomputeOut)
def recompute_trust(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Regrade the library and re-queue anything whose reviewed inputs moved.

    Super-admin: it changes a customer-visible caveat on every combo. The grade
    is derived, so this is the path that makes the queue re-populate itself
    rather than a flag somebody has to remember to set.
    """
    if not current_user.is_super_admin:
        raise HTTPException(403, "Recomputing trust grades is super-admin only")
    report = trust_recompute_all(db)
    db.commit()
    return TrustRecomputeOut(
        considered=report.considered, graded=report.graded,
        invalidated=report.invalidated, by_grade=report.by_grade,
    )


@router.get("/can-edit-platform")
def can_edit_platform_formulas(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Used by the frontend to gate platform formula UI without exposing the full permission check."""
    return {"can_edit": has_platform_permission(db, current_user, "formulas.edit")}


# ── Weighted components + per-region coverage + resolver (Scrum 58) ──────────

def _get_visible_template(
    db: Session, template_id: uuid.UUID, team_id: uuid.UUID | None = None
) -> FormulaTemplate:
    """Thin router-level wrapper: services.formula_resolver.get_visible_template
    (Scrum 28b) owns the actual visibility rule so cost_models.py can reuse it
    without a router-to-router import; this just picks the HTTP status."""
    template = get_visible_template(db, template_id, team_id)
    if not template:
        raise HTTPException(status_code=404, detail="Formula template not found")
    return template


def _require_template_edit(db: Session, current_user: User, template: FormulaTemplate) -> None:
    if template.team_id is None:
        require_platform_permission(db, current_user, "formulas.edit")
    else:
        require_permission(db, current_user, template.team_id, "formulas.edit")


@router.get("/{template_id}/components", response_model=list[FormulaComponentOut])
def list_components(
    template_id: uuid.UUID,
    team_id: uuid.UUID,
    region: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Without a region: the template-level (region-NULL) lines the API
    manages. With one: that exact region's seeded line set (no fallback —
    use /resolve for resolved views)."""
    require_permission(db, current_user, team_id, "formulas.view")
    _get_visible_template(db, template_id, team_id)
    return (
        db.query(FormulaTemplateComponent)
        .filter(
            FormulaTemplateComponent.template_id == template_id,
            FormulaTemplateComponent.region == region if region is not None
            else FormulaTemplateComponent.region.is_(None),
        )
        .order_by(FormulaTemplateComponent.sort_order)
        .all()
    )


@router.put("/{template_id}/components", response_model=list[FormulaComponentOut])
def replace_components(
    template_id: uuid.UUID,
    data: FormulaComponentsReplace,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Replace the template's weighted lines as a block (weights sum to 100)."""
    template = _get_visible_template(db, template_id)
    _require_template_edit(db, current_user, template)

    # Referenced commodity indexes must exist (friendlier than the FK error).
    commodity_ids = {c.commodity_id for c in data.components if c.commodity_id is not None}
    if commodity_ids:
        found = {
            row[0]
            for row in db.query(CommodityIndex.id).filter(CommodityIndex.id.in_(commodity_ids)).all()
        }
        missing = commodity_ids - found
        if missing:
            raise HTTPException(status_code=400, detail=f"Unknown commodity index id(s): {sorted(missing)}")

    # Chained inputs: must be visible, scope-compatible, acyclic, within depth.
    for comp in data.components:
        if comp.component_type != "formula":
            continue
        if comp.input_template_id == template_id:
            raise HTTPException(status_code=400, detail="A formula cannot use itself as an input")
        input_t = db.query(FormulaTemplate).filter(
            FormulaTemplate.id == comp.input_template_id
        ).first()
        if not input_t:
            raise HTTPException(status_code=404, detail="Input formula template not found")
        # A platform formula resolved by any team must never pull in one
        # team's private formula; a team formula may chain platform or its own.
        if template.team_id is None and input_t.team_id is not None:
            raise HTTPException(
                status_code=400,
                detail="A platform formula can only use platform formulas as inputs",
            )
        if template.team_id is not None and input_t.team_id not in (None, template.team_id):
            raise HTTPException(
                status_code=400,
                detail="A team formula can only use platform or same-team formulas as inputs",
            )
        try:
            assert_valid_chain_input(db, template_id, comp.input_template_id)
        except FormulaChainError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Only the template-level (region-NULL) set is API-managed; the seeded
    # per-region combo recipes (Scrum 60) are owned by the seed loader and
    # must survive an API edit.
    db.query(FormulaTemplateComponent).filter(
        FormulaTemplateComponent.template_id == template_id,
        FormulaTemplateComponent.region.is_(None),
    ).delete(synchronize_session=False)

    rows = [
        FormulaTemplateComponent(
            template_id=template_id,
            name=c.name,
            component_type=c.component_type,
            commodity_id=c.commodity_id,
            input_template_id=c.input_template_id,
            weight_pct=c.weight_pct,
            is_proxy=c.is_proxy,
            sort_order=c.sort_order if c.sort_order else i,
        )
        for i, c in enumerate(data.components)
    ]
    db.add_all(rows)
    db.flush()

    # Editing the recipe is exactly what the sign-off fingerprint exists for: a
    # combo an expert vouched for whose weights have since changed has to return
    # to the queue rather than keep its green tick.
    invalidated = 0
    for coverage in db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.template_id == template_id).all():
        if apply_assessment(db, coverage).sign_off_invalidated:
            invalidated += 1

    _audit_template_event(
        db, template, current_user.id, "update", "formula_template_components",
        str(template_id),
        new_value={"count": len(rows), "names": [r.name for r in rows],
                   "sign_offs_invalidated": invalidated},
    )

    # Response built before commit — the transaction-local RLS GUCs reset on
    # commit, so a post-commit re-query can come back empty.
    out = [FormulaComponentOut.model_validate(r) for r in rows]
    for r in rows:
        db.expunge(r)
    db.commit()
    return out


@router.get("/{template_id}/coverage", response_model=list[FormulaCoverageOut])
def list_coverage(
    template_id: uuid.UUID,
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "formulas.view")
    _get_visible_template(db, template_id, team_id)
    # Through the serialiser, so the trust grade and the resolved reviewer
    # identity are on this shape too — a raw ORM return would have silently
    # dropped both from the one read the UI actually calls.
    rows = (
        db.query(FormulaRegionCoverage)
        .filter(FormulaRegionCoverage.template_id == template_id)
        .order_by(FormulaRegionCoverage.region)
        .all()
    )
    return [_coverage_out(db, row) for row in rows]


@router.put("/{template_id}/coverage/{region}", response_model=FormulaCoverageOut)
def upsert_coverage(
    template_id: uuid.UUID,
    region: str,
    data: FormulaCoverageIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create or update the combo (this formula priced in this region)."""
    template = _get_visible_template(db, template_id)
    _require_template_edit(db, current_user, template)

    # Explicit check instead of leaning on the free-text safety net — a typo'd
    # region on a curated pricing row should fail, not auto-register a region.
    if not db.query(Region).filter(Region.code == region).first():
        raise HTTPException(status_code=400, detail=f"Unknown region code: {region}")

    row = db.query(FormulaRegionCoverage).filter(
        FormulaRegionCoverage.template_id == template_id,
        FormulaRegionCoverage.region == region,
    ).first()
    created = row is None
    if created:
        row = FormulaRegionCoverage(template_id=template_id, region=region)
        db.add(row)
    row.base_price = data.base_price
    row.currency = data.currency
    row.margin_pct = data.margin_pct
    row.base_year = data.base_year
    row.base_quarter = data.base_quarter
    db.flush()

    audit_team_id = template.team_id or _first_team_id(db, current_user.id)
    if audit_team_id:
        log_event(db, audit_team_id, current_user.id,
                  "create" if created else "update", "formula_region_coverage",
                  f"{template_id}:{region}",
                  new_value={"base_price": data.base_price, "margin_pct": data.margin_pct})

    out = _coverage_out(db, row)
    db.expunge(row)
    db.commit()
    return out


@router.post("/{template_id}/coverage/{region}/review", response_model=FormulaCoverageOut)
def mark_coverage_reviewed(
    template_id: uuid.UUID,
    region: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Expert sign-off on a combo.

    Three changes from the shipped version, all SCRUM-78:

    * gated on **`content.approve`**, not `formulas.edit` — the weight author
      vouching for their own work is not a review;
    * records a **users FK** rather than the reviewer's email, so the record
      does not decay when somebody changes their address;
    * pins the sign-off to a **fingerprint of the reviewed line set**, so it
      returns to the queue if the weights or index inputs move.
    """
    template = _get_visible_template(db, template_id)
    _require_template_approve(db, current_user, template)

    row = db.query(FormulaRegionCoverage).filter(
        FormulaRegionCoverage.template_id == template_id,
        FormulaRegionCoverage.region == region,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="No coverage for this region")

    trust_sign_off(db, row, current_user.id)

    _audit_template_event(
        db, template, current_user.id, "review", "formula_region_coverage",
        f"{template_id}:{region}",
        new_value={"reviewed_by_id": str(current_user.id),
                   "trust_grade": row.trust_grade,
                   "fingerprint": row.review_fingerprint},
    )

    out = _coverage_out(db, row)
    db.expunge(row)
    db.commit()
    return out


@router.delete("/{template_id}/coverage/{region}")
def delete_coverage(
    template_id: uuid.UUID,
    region: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    template = _get_visible_template(db, template_id)
    _require_template_edit(db, current_user, template)

    row = db.query(FormulaRegionCoverage).filter(
        FormulaRegionCoverage.template_id == template_id,
        FormulaRegionCoverage.region == region,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="No coverage for this region")

    audit_team_id = template.team_id or _first_team_id(db, current_user.id)
    if audit_team_id:
        log_event(db, audit_team_id, current_user.id, "delete", "formula_region_coverage",
                  f"{template_id}:{region}")

    db.delete(row)
    db.commit()
    return {"status": "deleted"}


# ── Cost-structure estimator (Scrum 33) ─────────────────────────────────────
# Drafts only — never mutates the live recipe until explicitly approved. See
# services/formula_estimator.py for the sibling-region-inheritance +
# priced-history-correlation design.

@router.post("/{template_id}/estimator/propose", response_model=EstimatorProposalOut)
def propose_estimator_recipe(
    template_id: uuid.UUID,
    region: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    template = _get_visible_template(db, template_id)
    _require_template_edit(db, current_user, template)

    try:
        proposal = create_or_update_proposal(db, template_id, region)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    audit_team_id = template.team_id or _first_team_id(db, current_user.id)
    if audit_team_id:
        log_event(db, audit_team_id, current_user.id, "propose", "estimator_proposal",
                  f"{template_id}:{region}", new_value={"status": proposal.status, "line_count": len(proposal.lines)})
    db.commit()
    return proposal


@router.get("/{template_id}/estimator/proposal", response_model=EstimatorProposalOut)
def get_estimator_proposal(
    template_id: uuid.UUID,
    region: str,
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "formulas.view")
    _get_visible_template(db, template_id, team_id)
    proposal = db.query(EstimatorProposal).filter(
        EstimatorProposal.template_id == template_id, EstimatorProposal.region == region,
    ).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="No estimator proposal for this combo")
    return proposal


@router.post("/estimator/proposals/{proposal_id}/approve", response_model=FormulaCoverageOut)
def approve_estimator_proposal(
    proposal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    proposal = db.query(EstimatorProposal).filter(EstimatorProposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    template = _get_visible_template(db, proposal.template_id)
    _require_template_edit(db, current_user, template)

    try:
        coverage = approve_proposal(db, proposal, current_user.id, current_user.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    audit_team_id = template.team_id or _first_team_id(db, current_user.id)
    if audit_team_id:
        log_event(db, audit_team_id, current_user.id, "approve", "estimator_proposal",
                  str(proposal_id), new_value={"template_id": str(proposal.template_id), "region": proposal.region})

    out = _coverage_out(db, coverage)
    db.expunge(coverage)
    db.commit()
    return out


@router.post("/estimator/proposals/{proposal_id}/reject")
def reject_estimator_proposal(
    proposal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    proposal = db.query(EstimatorProposal).filter(EstimatorProposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    template = _get_visible_template(db, proposal.template_id)
    _require_template_edit(db, current_user, template)

    try:
        reject_proposal(db, proposal)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    audit_team_id = template.team_id or _first_team_id(db, current_user.id)
    if audit_team_id:
        log_event(db, audit_team_id, current_user.id, "reject", "estimator_proposal", str(proposal_id))
    db.commit()
    return {"status": "rejected"}


@router.get("/estimator/backtest", response_model=BacktestReportOut)
def estimator_backtest(
    template_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Runs the estimator against every combo that already has a trustworthy
    human recipe, holding each one out (sibling search always excludes a
    combo's own region, so this is non-circular by construction) and
    reporting how well the proposal matches the real recipe. Optionally
    scoped to one template instead of the whole catalog."""
    require_platform_permission(db, current_user, "formulas.edit")
    return run_backtest(db, template_id=template_id)


@router.post("/coverage/upload")
async def upload_coverage_prices(
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Bulk base-price anchors for catalog combos. Update-only: a row must
    match an existing (platform formula code, region) combo — a typo can't
    mint a stray combo. Never touches recipes, confidence, or review state."""
    require_platform_permission(db, current_user, "formulas.edit")

    from app.services.file_parser import parse_coverage_price_upload
    content = await file.read()
    filename = file.filename or "upload"
    try:
        result = parse_coverage_price_upload(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    rows, errors = result["rows"], result["errors"]

    templates = {t.code: t for t in db.query(FormulaTemplate).filter(
        FormulaTemplate.team_id.is_(None), FormulaTemplate.code.isnot(None)).all()}
    regions = {r.code for r in db.query(Region).all()}

    updated = 0
    for i, r in enumerate(rows):
        row_num = i + 2  # best-effort: parse keeps source order for valid rows
        template = templates.get(r["code"])
        if template is None:
            errors.append({"row": row_num, "message": f"Unknown formula code '{r['code']}'."})
            continue
        if r["region"] not in regions:
            errors.append({"row": row_num, "message": f"Unknown region code '{r['region']}'."})
            continue
        cov = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.template_id == template.id,
            FormulaRegionCoverage.region == r["region"],
        ).first()
        if cov is None:
            errors.append({"row": row_num,
                           "message": f"No combo for {r['code']} in {r['region']} — prices attach to existing combos."})
            continue
        updated += 1
        if not dry_run:
            cov.base_price = r["base_price"]
            if r["currency"]:
                cov.currency = r["currency"]
            if r["base_year"]:
                cov.base_year, cov.base_quarter = r["base_year"], r["base_quarter"]
            if r["margin_pct"] is not None:
                cov.margin_pct = r["margin_pct"]

    if not dry_run and updated:
        audit_team_id = _first_team_id(db, current_user.id)
        if audit_team_id:
            log_event(db, audit_team_id, current_user.id, "update", "formula_region_coverage",
                      "bulk_price_upload", new_value={"filename": filename, "updated": updated})
        db.commit()

    return {"filename": filename, "rows_processed": updated, "errors": errors, "dry_run": dry_run}


@router.get("/{template_id}/evaluate", response_model=FormulaEvaluateOut)
def evaluate_formula(
    template_id: uuid.UUID,
    region: str,
    year: int,
    quarter: int,
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deterministic weighted should-cost at (region, year, quarter): rebased
    index level (100.0 at the combo's base period) and, when the combo carries
    a base price, the money-denominated should-cost with per-line
    contributions that sum to it exactly. Data gaps are explicit."""
    require_permission(db, current_user, team_id, "formulas.view")
    _get_visible_template(db, template_id, team_id)
    if not (1 <= quarter <= 4) or not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Invalid period")

    try:
        result = evaluate_weighted_template(db, team_id, template_id, region, year, quarter)
    except FormulaChainError as e:
        raise HTTPException(status_code=400, detail=str(e))

    commodity_ids = {l["commodity_id"] for l in result["lines"] if l["commodity_id"] is not None}
    commodity_names = {
        row.id: row.name
        for row in db.query(CommodityIndex.id, CommodityIndex.name)
        .filter(CommodityIndex.id.in_(commodity_ids)).all()
    } if commodity_ids else {}
    for l in result["lines"]:
        l["commodity_name"] = commodity_names.get(l["commodity_id"])

    return FormulaEvaluateOut(template_id=template_id, **result)


@router.get("/{template_id}/negotiation-position", response_model=NegotiationResponseOut)
def negotiation_position(
    template_id: uuid.UUID,
    region: str,
    year: int,
    quarter: int,
    team_id: uuid.UUID,
    supplier_price: float | None = None,
    supplier_currency: str | None = None,
    supplier_unit: str | None = None,
    supplier_incoterm: str | None = None,
    combo_unit: str | None = None,
    combo_incoterm: str | None = None,
    # A bare `dict` type is classified by FastAPI as a request body, which a
    # GET call never sends — accepted as a JSON-encoded string instead so it
    # actually arrives as a query param.
    incoterm_adjustments: str | None = None,
    # Scrum 31b — an alternative to typing supplier_price by hand: pull it
    # (and currency/unit/incoterm) straight from a confirmed quote record
    # line. Mutually exclusive with supplier_price.
    quote_line_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Negotiation position (Scrum 30b): a defensible target off the same
    weighted-evaluation engine as /evaluate, the ask (supplier's number minus
    target) normalised for currency/unit/Incoterm where declared, and an
    explicit unexplained remainder — never a fabricated supplier counter."""
    require_permission(db, current_user, team_id, "formulas.view")
    _get_visible_template(db, template_id, team_id)
    if not (1 <= quarter <= 4) or not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Invalid period")

    if (supplier_price is None) == (quote_line_id is None):
        raise HTTPException(status_code=400, detail="Provide exactly one of supplier_price or quote_line_id")

    if quote_line_id is not None:
        from app.models.quote import QuoteRecordLine, QuoteRecord

        quote_line = (
            db.query(QuoteRecordLine)
            .join(QuoteRecord, QuoteRecordLine.quote_record_id == QuoteRecord.id)
            .filter(QuoteRecordLine.id == quote_line_id, QuoteRecord.team_id == team_id)
            .first()
        )
        if not quote_line:
            raise HTTPException(status_code=404, detail="Quote record line not found")
        if quote_line.price is None:
            raise HTTPException(status_code=400, detail="Quote line has no price")
        supplier_price = float(quote_line.price)
        supplier_currency = quote_line.currency
        supplier_unit = quote_line.unit
        supplier_incoterm = quote_line.incoterm

    parsed_adjustments = None
    if incoterm_adjustments:
        try:
            parsed_adjustments = json.loads(incoterm_adjustments)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="incoterm_adjustments must be a JSON object")

    try:
        result = compute_negotiation_position(
            db, team_id, template_id, region, year, quarter, supplier_price,
            supplier_currency=supplier_currency, supplier_unit=supplier_unit,
            supplier_incoterm=supplier_incoterm, combo_unit=combo_unit,
            combo_incoterm=combo_incoterm, incoterm_adjustments=parsed_adjustments,
        )
    except FormulaChainError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Enrich every line (target + attributed_components share the same
    # commodity_id/via_template_id keys) in two batch lookups, mirroring
    # /resolve's enrichment — traceability requires the display names too.
    all_lines = result["target"]["lines"] + result["position"]["attributed_components"]
    commodity_ids = {l["commodity_id"] for l in all_lines if l["commodity_id"] is not None}
    commodity_names = {
        row.id: row.name
        for row in db.query(CommodityIndex.id, CommodityIndex.name)
        .filter(CommodityIndex.id.in_(commodity_ids)).all()
    } if commodity_ids else {}
    template_ids = {l["via_template_id"] for l in all_lines if l.get("via_template_id") is not None}
    template_names = {
        row.id: row.name
        for row in db.query(FormulaTemplate.id, FormulaTemplate.name)
        .filter(FormulaTemplate.id.in_(template_ids)).all()
    } if template_ids else {}
    for l in all_lines:
        l["commodity_name"] = commodity_names.get(l["commodity_id"])
        l["via_template_name"] = template_names.get(l.get("via_template_id"))

    log_event(db, team_id, current_user.id, "negotiation_position_generated", "formula_template",
              str(template_id), new_value={"region": region, "year": year, "quarter": quarter,
                                            "supplier_price": supplier_price})
    db.commit()
    return NegotiationResponseOut(
        template_id=template_id, region_requested=region, year=year, quarter=quarter, **result
    )


@router.get("/{template_id}/resolve", response_model=FormulaResolveOut)
def resolve_formula(
    template_id: uuid.UUID,
    region: str,
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resolve a formula for a region: the coverage combo (with fallback
    exact → parent region → GLOBAL → Europe) plus the flattened effective
    component lines (chained formulas expanded, weights scaled)."""
    require_permission(db, current_user, team_id, "formulas.view")
    _get_visible_template(db, template_id, team_id)

    cov, resolved_region = resolve_coverage(db, template_id, region)
    try:
        lines = flatten_components(db, template_id, region=region)
    except FormulaChainError as e:
        # Should be unreachable for API-written data (write-time guards), but
        # seed scripts write directly — surface it rather than 500.
        raise HTTPException(status_code=400, detail=str(e))

    # Enrich with display names in two batch lookups.
    commodity_ids = {l["commodity_id"] for l in lines if l["commodity_id"] is not None}
    commodity_names = {
        row.id: row.name
        for row in db.query(CommodityIndex.id, CommodityIndex.name)
        .filter(CommodityIndex.id.in_(commodity_ids)).all()
    } if commodity_ids else {}
    template_ids = {l["via_template_id"] for l in lines}
    template_names = {
        row.id: row.name
        for row in db.query(FormulaTemplate.id, FormulaTemplate.name)
        .filter(FormulaTemplate.id.in_(template_ids)).all()
    } if template_ids else {}

    return FormulaResolveOut(
        template_id=template_id,
        region_requested=region,
        region_resolved=resolved_region,
        coverage=FormulaCoverageOut.model_validate(cov) if cov else None,
        lines=[
            ResolvedLineOut(
                **l,
                commodity_name=commodity_names.get(l["commodity_id"]),
                via_template_name=template_names.get(l["via_template_id"]),
            )
            for l in lines
        ],
    )
