"""Dimension resolution + the faceted query (Wave 3, SCRUM-77 / INT-3).

Two halves:

**Resolution.** A raw value resolves to a term through an alias, within a
facet. A team's own alias wins over the platform one, so a team can disagree
with our vocabulary without editing it. Nothing is guessed: a value with no
alias lands in `dimension_unresolved` with its occurrence count, which is the
analyst's work queue and how anyone checks a load actually worked.

**The faceted query, at two grains.** This is where a single "products"
framing breaks:

* **team grain** — which of *my* products / cost models carry this term. Backs
  Portfolio and the audit use case.
* **platform grain** — which formulas carry it. Backs the Intelligence library,
  which renders platform tiles, not a team's products.

Faceted over `(kind, code)` rather than an endpoint per question — "everything
exposed to EUDR" is an example of the query, not its name. Every hit carries
the alias that matched, the region the claim applies to, and whether it is a
platform assertion or a team override, because a bare list of product names
cannot be checked by the person who has to act on it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.chemical_family import ChemicalFamily
from app.models.cost_model import CostModel
from app.models.dimension import (
    DIMENSION_KINDS, SUBJECT_TYPES, DimensionAlias, DimensionAssertion,
    DimensionTerm, UnresolvedValue, normalize_value,
)
from app.models.formula_template import FormulaTemplate
from app.models.index_data import CommodityIndex
from app.models.product import Product
from app.models.subfamily import Subfamily

# How many example subjects to keep on an unresolved row. Enough to recognise
# the value in context, not enough to turn the queue into a data dump.
UNRESOLVED_SAMPLE_SIZE = 5


# ── Terms and aliases ────────────────────────────────────────────────────────

def upsert_term(
    db: Session,
    *,
    kind: str,
    code: str,
    label: str,
    team_id: uuid.UUID | None = None,
    description: str | None = None,
    sort_order: int = 0,
    source: str = "loader",
) -> DimensionTerm:
    """Idempotent by (scope, kind, code) — re-running a load changes nothing."""
    if kind not in DIMENSION_KINDS:
        raise HTTPException(422, f"Invalid kind. Allowed: {sorted(DIMENSION_KINDS)}")
    q = db.query(DimensionTerm).filter(
        DimensionTerm.kind == kind, DimensionTerm.code == code)
    q = q.filter(DimensionTerm.team_id.is_(None)) if team_id is None \
        else q.filter(DimensionTerm.team_id == team_id)
    term = q.first()
    if term is None:
        term = DimensionTerm(
            team_id=team_id, kind=kind, code=code, label=label,
            description=description, sort_order=sort_order, source=source,
        )
        db.add(term)
        db.flush()
        return term
    # Update in place; never delete a term a load stopped mentioning.
    term.label = label
    if description is not None:
        term.description = description
    term.sort_order = sort_order
    db.flush()
    return term


def upsert_alias(
    db: Session,
    term: DimensionTerm,
    raw_value: str,
    *,
    team_id: uuid.UUID | None = None,
    source: str = "loader",
) -> DimensionAlias:
    """One meaning per raw value per facet.

    Re-pointing an existing alias at a different term is allowed and is exactly
    what re-importing a corrected decision file does — but it happens *in
    place*, so the same string never resolves two ways depending on row order.
    That is the failure mode a re-runnable regex list has: reordering the rules
    quietly reclassifies the library.
    """
    normalized = normalize_value(raw_value)
    q = db.query(DimensionAlias).filter(
        DimensionAlias.kind == term.kind, DimensionAlias.normalized == normalized)
    q = q.filter(DimensionAlias.team_id.is_(None)) if team_id is None \
        else q.filter(DimensionAlias.team_id == team_id)
    alias = q.first()
    if alias is None:
        alias = DimensionAlias(
            team_id=team_id, term_id=term.id, kind=term.kind,
            raw_value=str(raw_value), normalized=normalized, source=source,
        )
        db.add(alias)
        db.flush()
        return alias
    alias.term_id = term.id
    alias.raw_value = str(raw_value)
    alias.source = source
    db.flush()
    return alias


def resolve_raw(
    db: Session, kind: str, raw_value: str, team_id: uuid.UUID | None = None
) -> DimensionAlias | None:
    """The alias that matches a raw value, team-first.

    A team alias overriding a platform one is the point: a team can disagree
    with our mapping of "Industrial" without us changing it for everyone.
    """
    normalized = normalize_value(raw_value)
    if team_id is not None:
        own = (
            db.query(DimensionAlias)
            .filter(DimensionAlias.kind == kind,
                    DimensionAlias.normalized == normalized,
                    DimensionAlias.team_id == team_id)
            .first()
        )
        if own is not None:
            return own
    return (
        db.query(DimensionAlias)
        .filter(DimensionAlias.kind == kind,
                DimensionAlias.normalized == normalized,
                DimensionAlias.team_id.is_(None))
        .first()
    )


def record_unresolved(
    db: Session, kind: str, raw_value: str, subject: str | None = None,
    reason: str | None = None,
) -> UnresolvedValue:
    """Log a raw value that could not resolve, with how much it blocked.

    Counted rather than merely listed, so the queue is rankable: one unresolved
    industry string can block dozens of assertions, and an alphabetical list
    hides which one is worth an analyst's next ten minutes.
    """
    normalized = normalize_value(raw_value)
    row = (
        db.query(UnresolvedValue)
        .filter(UnresolvedValue.kind == kind, UnresolvedValue.normalized == normalized)
        .first()
    )
    now = datetime.now(timezone.utc)
    if row is None:
        row = UnresolvedValue(
            kind=kind, raw_value=str(raw_value), normalized=normalized,
            occurrences=1, sample_subjects=[subject] if subject else [],
            reason=reason, first_seen_at=now, last_seen_at=now,
        )
        db.add(row)
        db.flush()
        return row
    row.occurrences = (row.occurrences or 0) + 1
    row.last_seen_at = now
    if reason:
        row.reason = reason
    samples = list(row.sample_subjects or [])
    if subject and subject not in samples and len(samples) < UNRESOLVED_SAMPLE_SIZE:
        samples.append(subject)
        row.sample_subjects = samples
    db.flush()
    return row


def clear_unresolved(db: Session, kind: str | None = None) -> int:
    """Reset the register before a fresh load.

    The register is a snapshot of *this* load's failures, not an append-only
    ledger — a value resolved by yesterday's decision-file import must stop
    appearing, or the queue never shrinks and stops being trusted.
    """
    q = db.query(UnresolvedValue)
    if kind:
        q = q.filter(UnresolvedValue.kind == kind)
    n = q.delete(synchronize_session=False)
    db.flush()
    return n


# ── Subject links ────────────────────────────────────────────────────────────

@dataclass
class SubjectLinks:
    template_id: uuid.UUID | None = None
    commodity_id: int | None = None
    family_id: int | None = None
    subfamily_id: int | None = None


def resolve_subject(db: Session, subject_type: str, subject_code: str) -> SubjectLinks:
    """Convenience joins, never identity — the same rule as the editorial blocks.

    A hard FK on `template_id` would drop the template-less keys at import
    without raising, and nothing downstream could tell that from "never
    asserted".
    """
    links = SubjectLinks()
    if subject_type == "formula":
        row = db.query(FormulaTemplate).filter(
            FormulaTemplate.code == subject_code,
            FormulaTemplate.team_id.is_(None)).first()
        if row:
            links.template_id = row.id
    elif subject_type == "index":
        row = db.query(CommodityIndex).filter(
            CommodityIndex.commodity_key == subject_code).first()
        if row:
            links.commodity_id = row.id
    elif subject_type == "family":
        row = db.query(ChemicalFamily).filter(
            ChemicalFamily.name == subject_code,
            ChemicalFamily.team_id.is_(None)).first()
        if row:
            links.family_id = row.id
    elif subject_type == "subfamily":
        family_name, _, sub_name = subject_code.partition("|")
        if sub_name:
            row = (
                db.query(Subfamily)
                .join(ChemicalFamily, ChemicalFamily.id == Subfamily.family_id)
                .filter(Subfamily.name == sub_name,
                        ChemicalFamily.name == family_name,
                        Subfamily.team_id.is_(None))
                .first()
            )
            if row:
                links.subfamily_id = row.id
                links.family_id = row.family_id
    return links


# ── Assertions ───────────────────────────────────────────────────────────────

def assert_term(
    db: Session,
    term: DimensionTerm,
    *,
    subject_type: str,
    subject_code: str,
    region: str | None = None,
    team_id: uuid.UUID | None = None,
    raw_value: str | None = None,
    matched_alias: DimensionAlias | None = None,
    source: str = "loader",
    detail: dict | None = None,
) -> DimensionAssertion:
    """Idempotent by (scope, term, subject, region-or-wildcard)."""
    if subject_type not in SUBJECT_TYPES:
        raise HTTPException(422, f"Invalid subject_type. Allowed: {sorted(SUBJECT_TYPES)}")

    q = db.query(DimensionAssertion).filter(
        DimensionAssertion.term_id == term.id,
        DimensionAssertion.subject_type == subject_type,
        DimensionAssertion.subject_code == subject_code,
    )
    q = q.filter(DimensionAssertion.region.is_(None)) if region is None \
        else q.filter(DimensionAssertion.region == region)
    q = q.filter(DimensionAssertion.team_id.is_(None)) if team_id is None \
        else q.filter(DimensionAssertion.team_id == team_id)
    existing = q.first()
    if existing is not None:
        if raw_value is not None:
            existing.raw_value = raw_value
        if matched_alias is not None:
            existing.matched_alias_id = matched_alias.id
        if detail is not None:
            existing.detail = detail
        db.flush()
        return existing

    links = resolve_subject(db, subject_type, subject_code)
    row = DimensionAssertion(
        team_id=team_id, term_id=term.id,
        subject_type=subject_type, subject_code=subject_code, region=region,
        template_id=links.template_id, commodity_id=links.commodity_id,
        family_id=links.family_id, subfamily_id=links.subfamily_id,
        raw_value=raw_value,
        matched_alias_id=matched_alias.id if matched_alias else None,
        source=source, detail=detail,
    )
    db.add(row)
    db.flush()
    return row


# ── The faceted query ────────────────────────────────────────────────────────

@dataclass
class Hit:
    subject_type: str
    subject_code: str
    # NULL means the claim applies to every region, the
    # `FormulaTemplateComponent.region` semantic.
    region: str | None
    scope: str                 # "platform" | "team"
    term_code: str
    term_label: str
    # The audit trail: what the source said, and which alias matched it.
    raw_value: str | None = None
    matched_alias: str | None = None
    source: str = "loader"
    template_id: uuid.UUID | None = None
    template_name: str | None = None


@dataclass
class TeamHit(Hit):
    """A platform assertion projected onto a team's own rows."""
    product_id: uuid.UUID | None = None
    product_name: str | None = None
    cost_model_id: uuid.UUID | None = None
    cost_model_region: str | None = None
    # True when the assertion's region is NULL (applies everywhere) or matches
    # the cost model's own region. A region-specific claim on an EU combo does
    # not carry over to that product's NA combo.
    region_applies: bool = True


@dataclass
class FacetResult:
    kind: str
    code: str
    grain: str
    hits: list = field(default_factory=list)
    total: int = 0


def _term_for(db: Session, kind: str, code: str, team_id: uuid.UUID | None):
    """A team's own term wins over the platform one of the same (kind, code)."""
    if team_id is not None:
        own = db.query(DimensionTerm).filter(
            DimensionTerm.kind == kind, DimensionTerm.code == code,
            DimensionTerm.team_id == team_id).first()
        if own is not None:
            return own
    return db.query(DimensionTerm).filter(
        DimensionTerm.kind == kind, DimensionTerm.code == code,
        DimensionTerm.team_id.is_(None)).first()


def list_terms(
    db: Session, kind: str | None = None, team_id: uuid.UUID | None = None
) -> list[DimensionTerm]:
    if kind is not None and kind not in DIMENSION_KINDS:
        raise HTTPException(422, f"Invalid kind. Allowed: {sorted(DIMENSION_KINDS)}")
    q = db.query(DimensionTerm).filter(
        or_(DimensionTerm.team_id.is_(None), DimensionTerm.team_id == team_id))
    if kind:
        q = q.filter(DimensionTerm.kind == kind)
    return q.order_by(DimensionTerm.kind, DimensionTerm.sort_order,
                      DimensionTerm.label).all()


def _assertion_rows(db: Session, kind: str, code: str, team_id: uuid.UUID | None,
                    region: str | None):
    """Assertions for one (kind, code), platform plus this team's overrides."""
    codes = [t.id for t in db.query(DimensionTerm).filter(
        DimensionTerm.kind == kind, DimensionTerm.code == code,
        or_(DimensionTerm.team_id.is_(None), DimensionTerm.team_id == team_id)).all()]
    if not codes:
        return []
    q = (
        db.query(DimensionAssertion, DimensionTerm, DimensionAlias)
        .join(DimensionTerm, DimensionTerm.id == DimensionAssertion.term_id)
        .outerjoin(DimensionAlias, DimensionAlias.id == DimensionAssertion.matched_alias_id)
        .filter(DimensionAssertion.term_id.in_(codes),
                or_(DimensionAssertion.team_id.is_(None),
                    DimensionAssertion.team_id == team_id))
    )
    if region is not None:
        # A region filter must still admit the "every region" claims, or an EU
        # query silently loses every global assertion.
        q = q.filter(or_(DimensionAssertion.region.is_(None),
                         DimensionAssertion.region == region))
    return q.all()


def query_platform(
    db: Session, kind: str, code: str, team_id: uuid.UUID | None = None,
    region: str | None = None, subject_type: str | None = None,
) -> FacetResult:
    """Which formulas (or indexes, families, producers) carry this term.

    Backs the Intelligence library, which renders platform tiles — not a team's
    products. This is the grain the old single-"products" framing lost.
    """
    rows = _assertion_rows(db, kind, code, team_id, region)
    template_ids = {a.template_id for a, _, _ in rows if a.template_id}
    names = {
        t.id: t.name
        for t in db.query(FormulaTemplate).filter(
            FormulaTemplate.id.in_(template_ids)).all()
    } if template_ids else {}

    hits = []
    for assertion, term, alias in rows:
        if subject_type and assertion.subject_type != subject_type:
            continue
        hits.append(Hit(
            subject_type=assertion.subject_type,
            subject_code=assertion.subject_code,
            region=assertion.region,
            scope="team" if assertion.team_id is not None else "platform",
            term_code=term.code, term_label=term.label,
            raw_value=assertion.raw_value,
            matched_alias=alias.raw_value if alias else None,
            source=assertion.source,
            template_id=assertion.template_id,
            template_name=names.get(assertion.template_id),
        ))
    hits.sort(key=lambda h: (h.subject_type, h.subject_code, h.region or ""))
    return FacetResult(kind=kind, code=code, grain="platform", hits=hits, total=len(hits))


def query_team(
    db: Session, kind: str, code: str, team_id: uuid.UUID,
    region: str | None = None,
) -> FacetResult:
    """Which of *my* products and cost models carry this term.

    The team end scopes itself: RLS on `products` / `cost_models` already
    narrows this to the caller's rows, so the join does the work without the
    endpoint filtering by team.
    """
    rows = _assertion_rows(db, kind, code, team_id, region)
    by_template: dict[uuid.UUID, list] = {}
    for assertion, term, alias in rows:
        if assertion.subject_type != "formula" or assertion.template_id is None:
            continue
        by_template.setdefault(assertion.template_id, []).append((assertion, term, alias))
    if not by_template:
        return FacetResult(kind=kind, code=code, grain="team", hits=[], total=0)

    products = (
        db.query(Product)
        .filter(Product.team_id == team_id,
                Product.formula_template_id.in_(list(by_template)))
        .all()
    )
    models = (
        db.query(CostModel)
        .filter(CostModel.team_id == team_id,
                CostModel.product_id.in_([p.id for p in products]))
        .all()
    ) if products else []
    models_by_product: dict[uuid.UUID, list[CostModel]] = {}
    for cm in models:
        models_by_product.setdefault(cm.product_id, []).append(cm)

    hits: list[TeamHit] = []
    for product in products:
        for assertion, term, alias in by_template[product.formula_template_id]:
            product_models = models_by_product.get(product.id) or [None]
            for cm in product_models:
                # A region-specific claim only reaches a combo in that region;
                # a NULL-region claim reaches all of them.
                applies = (
                    assertion.region is None or cm is None
                    or assertion.region == cm.region
                )
                hits.append(TeamHit(
                    subject_type="formula", subject_code=assertion.subject_code,
                    region=assertion.region,
                    scope="team" if assertion.team_id is not None else "platform",
                    term_code=term.code, term_label=term.label,
                    raw_value=assertion.raw_value,
                    matched_alias=alias.raw_value if alias else None,
                    source=assertion.source,
                    template_id=assertion.template_id,
                    product_id=product.id, product_name=product.name,
                    cost_model_id=cm.id if cm else None,
                    cost_model_region=cm.region if cm else None,
                    region_applies=applies,
                ))
    hits.sort(key=lambda h: (h.product_name or "", h.cost_model_region or ""))
    return FacetResult(kind=kind, code=code, grain="team", hits=hits, total=len(hits))


def subject_dimensions(
    db: Session, subject_type: str, subject_code: str,
    team_id: uuid.UUID | None = None, region: str | None = None,
) -> dict[str, list[dict]]:
    """Every term asserted on one subject, grouped by kind.

    This is the half SCRUM-76's composed card (`CON-7`) reads — the dimension
    side of the ID card, not a second card-shaped endpoint.
    """
    q = (
        db.query(DimensionAssertion, DimensionTerm, DimensionAlias)
        .join(DimensionTerm, DimensionTerm.id == DimensionAssertion.term_id)
        .outerjoin(DimensionAlias, DimensionAlias.id == DimensionAssertion.matched_alias_id)
        .filter(DimensionAssertion.subject_type == subject_type,
                DimensionAssertion.subject_code == subject_code,
                or_(DimensionAssertion.team_id.is_(None),
                    DimensionAssertion.team_id == team_id))
    )
    if region is not None:
        q = q.filter(or_(DimensionAssertion.region.is_(None),
                         DimensionAssertion.region == region))

    out: dict[str, list[dict]] = {}
    for assertion, term, alias in q.all():
        out.setdefault(term.kind, []).append({
            "code": term.code, "label": term.label,
            "region": assertion.region,
            "scope": "team" if assertion.team_id is not None else "platform",
            "raw_value": assertion.raw_value,
            "matched_alias": alias.raw_value if alias else None,
            "source": assertion.source,
        })
    for kind in out:
        out[kind].sort(key=lambda d: d["label"])
    return out


def unresolved_report(db: Session, kind: str | None = None) -> list[UnresolvedValue]:
    q = db.query(UnresolvedValue)
    if kind:
        q = q.filter(UnresolvedValue.kind == kind)
    return q.order_by(UnresolvedValue.occurrences.desc(),
                      UnresolvedValue.raw_value).all()
