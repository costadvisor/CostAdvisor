"""Dimension query API (Wave 3, SCRUM-77 / INT-3).

    GET  /api/dimensions/terms                      term list, filterable by kind
    POST /api/dimensions/terms                      author a term (platform or team)
    POST /api/dimensions/terms/{id}/aliases         map a raw value onto a term
    POST /api/dimensions/terms/{id}/assertions      assert a term on a subject
    GET  /api/dimensions/query                      the faceted query, both grains
    GET  /api/dimensions/subjects/{type}/{code}     every term on one subject
    GET  /api/dimensions/unresolved                 the analyst's work queue
    GET  /api/dimensions/producers                  the company master
    GET  /api/dimensions/producers/{id}             what this producer makes

**Faceted over `(kind, code)`, not an endpoint per question.** "Everything
exposed to EUDR" is an example of the query, not its name — an endpoint per
question means a migration and a route for every facet anyone thinks of.

**Two grains, because a single "products" framing loses one of them.** The team
grain answers "which of *my* products carry this", which Portfolio and the
audit use case need. The platform grain answers "which formulas carry this",
which the Intelligence library needs because it renders platform tiles.

No faceted sidebar ships here — the mockup's sidebar is the consumer, and it
should be buildable against this without a schema change.

Reads and writes are separate gates: platform writes go through
`has_platform_permission` + `UserPlatformRole` on the `dimensions.*` keys that
SCRUM-76's single permission migration created. This story adds no second
migration.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.dimension import (
    DIMENSION_KINDS, SUBJECT_TYPES, DimensionAlias, DimensionTerm,
)
from app.models.producer import Producer, ProducerAlias, ProducerFormula
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.dimension import (
    AliasCreate, AliasOut, AssertionCreate, FacetOut, HitOut, ProducerDetailOut,
    ProducerFormulaOut, ProducerOut, SubjectDimensionsOut, TermCreate, TermOut,
    UnresolvedOut,
)
from app.services.audit import log_event
from app.services.dimensions import (
    assert_term, list_terms, query_platform, query_team, subject_dimensions,
    unresolved_report, upsert_alias, upsert_term,
)
from app.services.permissions import require_permission, require_platform_permission
from app.services.producers import producer_portfolio

router = APIRouter()


def _require_write(db: Session, user: User, team_id: uuid.UUID, *, platform: bool,
                   key: str = "dimensions.edit") -> None:
    if platform:
        require_platform_permission(db, user, key)
    else:
        require_permission(db, user, team_id, key)


def _visible_term(db: Session, term_id: uuid.UUID, team_id: uuid.UUID) -> DimensionTerm:
    term = (
        db.query(DimensionTerm)
        .filter(DimensionTerm.id == term_id,
                or_(DimensionTerm.team_id.is_(None), DimensionTerm.team_id == team_id))
        .first()
    )
    if term is None:
        raise HTTPException(404, "Dimension term not found")
    return term


@router.get("/terms", response_model=list[TermOut])
def get_terms(team_id: uuid.UUID, kind: str | None = Query(None),
              db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    require_permission(db, current_user, team_id, "dimensions.view")
    return list_terms(db, kind=kind, team_id=team_id)


@router.get("/kinds")
def get_kinds(team_id: uuid.UUID, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    """The facet vocabulary and how populated each one is.

    `functionality` and `functionality_family` are two disjoint naming schemes
    for the same idea and are deliberately separate kinds — merging them would
    produce one facet with two halves and no way to tell which half a filter is
    acting on.
    """
    require_permission(db, current_user, team_id, "dimensions.view")
    counts = dict(
        db.query(DimensionTerm.kind, func.count(DimensionTerm.id))
        .filter(or_(DimensionTerm.team_id.is_(None), DimensionTerm.team_id == team_id))
        .group_by(DimensionTerm.kind)
        .all()
    )
    return {"kinds": [{"kind": k, "terms": counts.get(k, 0)} for k in DIMENSION_KINDS]}


@router.post("/terms", response_model=TermOut, status_code=201)
def create_term(team_id: uuid.UUID, data: TermCreate, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    _require_write(db, current_user, team_id, platform=data.platform)
    term = upsert_term(
        db, kind=data.kind, code=data.code, label=data.label,
        team_id=None if data.platform else team_id,
        description=data.description, sort_order=data.sort_order, source="api",
    )
    out = TermOut.model_validate(term)
    log_event(db, team_id, current_user.id, "create", "dimension_term", str(term.id),
              new_value={"kind": data.kind, "code": data.code,
                         "platform": data.platform})
    db.commit()
    return out


@router.post("/terms/{term_id}/aliases", response_model=AliasOut, status_code=201)
def create_alias(term_id: uuid.UUID, team_id: uuid.UUID, data: AliasCreate,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """Map a raw value onto a term.

    One meaning per raw value per facet, and re-pointing happens in place — so
    the same string never resolves two ways depending on row order. That is the
    failure mode a re-runnable regex list has: reordering the rules quietly
    reclassifies the library.
    """
    term = _visible_term(db, term_id, team_id)
    _require_write(db, current_user, team_id, platform=data.platform)
    alias = upsert_alias(db, term, data.raw_value,
                         team_id=None if data.platform else team_id, source="api")
    out = AliasOut.model_validate(alias)
    log_event(db, team_id, current_user.id, "create", "dimension_alias", str(alias.id),
              new_value={"kind": term.kind, "raw": data.raw_value})
    db.commit()
    return out


@router.post("/terms/{term_id}/assertions", status_code=201)
def create_assertion(term_id: uuid.UUID, team_id: uuid.UUID, data: AssertionCreate,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    term = _visible_term(db, term_id, team_id)
    _require_write(db, current_user, team_id, platform=data.platform)
    if data.subject_type not in SUBJECT_TYPES:
        raise HTTPException(422, f"Invalid subject_type. Allowed: {sorted(SUBJECT_TYPES)}")
    row = assert_term(
        db, term, subject_type=data.subject_type, subject_code=data.subject_code,
        region=data.region, team_id=None if data.platform else team_id,
        raw_value=data.raw_value, source="api",
    )
    log_event(db, team_id, current_user.id, "create", "dimension_assertion", str(row.id),
              new_value={"term": term.code, "subject": data.subject_code,
                         "region": data.region, "platform": data.platform})
    db.commit()
    return {
        "id": str(row.id), "term_code": term.code,
        "subject_type": row.subject_type, "subject_code": row.subject_code,
        "region": row.region,
        "scope": "platform" if row.team_id is None else "team",
        # Null wherever the subject has no template row — a normal state, not a
        # load failure.
        "template_id": str(row.template_id) if row.template_id else None,
    }


@router.get("/query", response_model=FacetOut)
def facet_query(team_id: uuid.UUID, kind: str, code: str,
                grain: str = Query("platform", pattern="^(platform|team)$"),
                region: str | None = Query(None),
                subject_type: str | None = Query(None),
                db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """The faceted query. `grain=platform` for formulas, `grain=team` for the
    caller's own products and cost models.

    A `region` filter still admits the NULL-region ("every region") claims — an
    EU query that dropped them would silently lose every global assertion.
    """
    require_permission(db, current_user, team_id, "dimensions.view")
    if kind not in DIMENSION_KINDS:
        raise HTTPException(422, f"Invalid kind. Allowed: {sorted(DIMENSION_KINDS)}")
    if subject_type and subject_type not in SUBJECT_TYPES:
        raise HTTPException(422, f"Invalid subject_type. Allowed: {sorted(SUBJECT_TYPES)}")

    result = (
        query_team(db, kind, code, team_id, region=region)
        if grain == "team"
        else query_platform(db, kind, code, team_id=team_id, region=region,
                            subject_type=subject_type)
    )
    return FacetOut(
        kind=result.kind, code=result.code, grain=result.grain, total=result.total,
        hits=[HitOut(**vars(h)) for h in result.hits],
    )


@router.get("/subjects/{subject_type}/{subject_code:path}",
            response_model=SubjectDimensionsOut)
def get_subject_dimensions(subject_type: str, subject_code: str, team_id: uuid.UUID,
                           region: str | None = Query(None),
                           db: Session = Depends(get_db),
                           current_user: User = Depends(get_current_user)):
    """Every term on one subject, grouped by kind — the dimension half that
    SCRUM-76's composed card read folds in, rather than a second card-shaped
    endpoint.

    `:path` because a `subfamily` key is `"<family>|<subfamily>"`.
    """
    require_permission(db, current_user, team_id, "dimensions.view")
    if subject_type not in SUBJECT_TYPES:
        raise HTTPException(422, f"Invalid subject_type. Allowed: {sorted(SUBJECT_TYPES)}")
    return SubjectDimensionsOut(
        subject_type=subject_type, subject_code=subject_code, region=region,
        dimensions=subject_dimensions(db, subject_type, subject_code,
                                      team_id=team_id, region=region),
    )


@router.get("/unresolved", response_model=list[UnresolvedOut])
def get_unresolved(team_id: uuid.UUID, kind: str | None = Query(None),
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """Every raw value the load could not resolve, ranked by how much it blocked.

    Not swallowed and not guessed. This is the analyst's work queue and how
    anyone checks the load actually worked — 178 of 204 raw industry strings
    land here on a first load, because `INDUSTRY_RULES.json` cannot classify
    them and the mockup holding the original regexes is not in this repo.
    """
    require_permission(db, current_user, team_id, "dimensions.view")
    if kind and kind not in DIMENSION_KINDS:
        raise HTTPException(422, f"Invalid kind. Allowed: {sorted(DIMENSION_KINDS)}")
    return unresolved_report(db, kind=kind)


# ── Producers ────────────────────────────────────────────────────────────────

@router.get("/producers", response_model=list[ProducerOut])
def get_producers(team_id: uuid.UUID, q: str | None = Query(None),
                  limit: int = Query(100, ge=1, le=1000),
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """The platform company master.

    Not `Supplier`: that table's `team_id` is NOT NULL under strict tenant, so
    it has no row shape for a company that exists independently of a team
    buying from it.
    """
    require_permission(db, current_user, team_id, "dimensions.view")
    query = db.query(Producer)
    if q:
        query = query.filter(Producer.normalized_name.contains(q.strip().casefold()))
    rows = query.order_by(Producer.name).limit(limit).all()
    counts = dict(
        db.query(ProducerAlias.producer_id, func.count(ProducerAlias.id))
        .filter(ProducerAlias.producer_id.in_([r.id for r in rows]))
        .group_by(ProducerAlias.producer_id)
        .all()
    ) if rows else {}
    return [
        ProducerOut(
            id=r.id, name=r.name, normalized_name=r.normalized_name,
            hq_country=r.hq_country, notes=r.notes, source=r.source,
            alias_count=counts.get(r.id, 0),
        )
        for r in rows
    ]


@router.get("/producers/{producer_id}", response_model=ProducerDetailOut)
def get_producer(producer_id: uuid.UUID, team_id: uuid.UUID,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """What this producer actually makes — the question `Supplier` could never
    answer."""
    require_permission(db, current_user, team_id, "dimensions.view")
    row = db.query(Producer).filter(Producer.id == producer_id).first()
    if row is None:
        raise HTTPException(404, "Producer not found")
    aliases = db.query(ProducerAlias).filter(
        ProducerAlias.producer_id == producer_id).all()
    return ProducerDetailOut(
        id=row.id, name=row.name, normalized_name=row.normalized_name,
        hq_country=row.hq_country, notes=row.notes, source=row.source,
        alias_count=len(aliases),
        aliases=sorted({a.raw_value for a in aliases}),
        portfolio=[
            ProducerFormulaOut.model_validate(pf)
            for pf in producer_portfolio(db, producer_id)
        ],
    )
