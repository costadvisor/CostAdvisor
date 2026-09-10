"""Combo trust grade + expert sign-off state (Wave 3, SCRUM-78 / INT-4).

`needs_review` used to be set by `data_confidence == "CONF-LOW"`. The July
catalog sheet dropped that column and `seed_catalog` already resolves it to
None, so nothing set the flag any more: the queue was empty and the sign-off
button had nothing to clear. This replaces the input that disappeared.

**The replacement signals are structural, not a column.** Trust in the July data
lives in the resolution layer built in units 2–4: whether a combo's inputs
resolve to a series at all, whether they resolve through a proxy, and whether
the weight set closes. Three things worth stating outright:

* **The derivation reads the type-code side of `proxy_status`.** A substantial
  share of indexed cost lines carry a `proxy_status` that contradicts the one on
  their own type-code row, so whichever column is read the other disagrees —
  naming one as authoritative and reporting the disagreement as its own reason
  is the only honest option.
* **`coverage_tier` is not overwritten.** Either coverage column answers "how
  well covered is this"; the grade answers "is this worth a human's time", and
  coverage is one input to it. The grade gets its own field.
* **The grade is rankable, not a boolean.** "Any proxy input means review" would
  put most of the library in the queue in one pass, because proxies are a large
  share of the resolution layer and roughly a quarter of indexed cost weight
  resolves through a single series — the flags cluster rather than spread.

A sign-off is pinned to **what was signed off**, via a fingerprint over the
reviewed line set. Change a weight or an index input and the combo returns to
the queue instead of showing a stale green tick. The descriptor is stored under
`review_derived_from`, deliberately the same field name and JSONB shape as
CON-5's staleness descriptor, rather than inventing a second format.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.constants.trust import (
    GRADE_BLOCKED, GRADE_HIGH, GRADE_LOW, GRADE_MEDIUM, GRADE_SEVERITY,
    GRADE_UNRATED, GRADES_NEEDING_REVIEW, PROXY_STATUS_SOURCE, REASON_AMBIGUOUS,
    REASON_NO_LINES, REASON_NO_SERIES, REASON_NO_TYPE_CODE, REASON_PROXY,
    REASON_PROXY_DISAGREEMENT, REASON_WEIGHTS_OPEN, TRUST_GRADES,
)
from app.models.formula_template import (
    FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_layer import TypeCode
from app.models.user import User

# Catalog recipes legitimately run 99.9–110 because margin sits inside the 100%
# total, so "closed" is a band rather than exactly 100. Outside it the weights
# were not authored to close and the combo needs a human.
WEIGHT_CLOSURE_MIN = 99.5
WEIGHT_CLOSURE_MAX = 110.5

# How much proxied weight separates "softer signal" from "needs a look".
PROXY_WEIGHT_MEDIUM_MAX = 50.0


@dataclass
class TrustReason:
    """One thing that pulled the grade down, with what to go and look at."""
    reason: str
    # The type-codes or line names behind it — an ungraded "low" tells a
    # reviewer nothing.
    subjects: list[str] = field(default_factory=list)
    weight_pct: float = 0.0
    detail: str | None = None


@dataclass
class TrustAssessment:
    grade: str
    needs_review: bool
    reasons: list[TrustReason] = field(default_factory=list)
    total_weight: float = 0.0
    indexed_weight: float = 0.0
    proxied_weight: float = 0.0
    blocked_weight: float = 0.0
    line_count: int = 0
    type_coded_lines: int = 0
    # Named on every assessment, because the two proxy columns disagree and a
    # consumer has to know which one produced this.
    proxy_status_source: str = PROXY_STATUS_SOURCE

    def as_inputs(self) -> dict:
        """The stored, inspectable "why" — `trust_inputs`."""
        return {
            "proxy_status_source": self.proxy_status_source,
            "total_weight": round(self.total_weight, 2),
            "indexed_weight": round(self.indexed_weight, 2),
            "proxied_weight": round(self.proxied_weight, 2),
            "blocked_weight": round(self.blocked_weight, 2),
            "line_count": self.line_count,
            "type_coded_lines": self.type_coded_lines,
            "reasons": [
                {
                    "reason": r.reason,
                    "subjects": r.subjects,
                    "weight_pct": round(r.weight_pct, 2),
                    "detail": r.detail,
                }
                for r in self.reasons
            ],
        }


# ── The line set a combo is graded and signed off on ─────────────────────────

def coverage_lines(
    db: Session, coverage: FormulaRegionCoverage
) -> list[FormulaTemplateComponent]:
    """The lines a combo's grade and sign-off are about.

    Region-tagged lines for this combo's region, falling back to the
    region-NULL (template-level) set — the same precedence the resolver uses,
    and the same `variant` key the catalog retarget introduced, because two
    combos differing only by variant have different recipes.
    """
    variant = coverage.variant or ""
    rows = (
        db.query(FormulaTemplateComponent)
        .filter(FormulaTemplateComponent.template_id == coverage.template_id,
                FormulaTemplateComponent.region == coverage.region,
                FormulaTemplateComponent.variant == variant)
        .order_by(FormulaTemplateComponent.sort_order,
                  FormulaTemplateComponent.name)
        .all()
    )
    if rows:
        return rows
    return (
        db.query(FormulaTemplateComponent)
        .filter(FormulaTemplateComponent.template_id == coverage.template_id,
                FormulaTemplateComponent.region.is_(None))
        .order_by(FormulaTemplateComponent.sort_order,
                  FormulaTemplateComponent.name)
        .all()
    )


def line_descriptor(lines: list[FormulaTemplateComponent]) -> list[dict]:
    """What a sign-off is a sign-off *of*.

    Only the fields a reviewer actually vouched for: the shape of the recipe and
    the inputs behind it. Deliberately not base price or margin — the caveat
    this drives is about cost-line weights, and re-queueing a validated recipe
    because somebody corrected a price would train reviewers to ignore the flag.
    """
    return [
        {
            "name": line.name,
            "component_type": line.component_type,
            "commodity_id": line.commodity_id,
            "type_code_id": line.type_code_id,
            "input_template_id": str(line.input_template_id)
            if line.input_template_id else None,
            "weight_pct": float(line.weight_pct),
        }
        for line in lines
    ]


def fingerprint_for(lines: list[FormulaTemplateComponent]) -> str:
    """A stable digest of the reviewed inputs.

    Sorted before hashing so a reordered but otherwise identical recipe does not
    read as a change — `sort_order` is presentation, not substance.
    """
    descriptor = sorted(
        line_descriptor(lines),
        key=lambda d: (d["name"], d["component_type"] or "", d["weight_pct"]),
    )
    blob = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── The derivation ───────────────────────────────────────────────────────────

def assess(db: Session, coverage: FormulaRegionCoverage) -> TrustAssessment:
    """Grade one combo from the resolution layer and its weight set."""
    lines = coverage_lines(db, coverage)
    if not lines:
        return TrustAssessment(
            grade=GRADE_UNRATED, needs_review=False,
            reasons=[TrustReason(reason=REASON_NO_LINES,
                                 detail="no cost lines for this combo's region")],
        )

    total_weight = sum(float(l.weight_pct) for l in lines)
    # Margin sits inside the 100% total in this catalog, and a fixed line
    # carries no index — only index lines can be graded on their resolution.
    index_lines = [l for l in lines if l.component_type == "index"]
    indexed_weight = sum(float(l.weight_pct) for l in index_lines)

    code_ids = {l.type_code_id for l in index_lines if l.type_code_id}
    codes = {
        c.id: c for c in db.query(TypeCode).filter(TypeCode.id.in_(code_ids)).all()
    } if code_ids else {}

    no_series: dict[str, float] = {}
    ambiguous: dict[str, float] = {}
    unlinked: dict[str, float] = {}
    proxied: dict[str, float] = {}
    disagreeing: dict[str, float] = {}

    for line in index_lines:
        weight = float(line.weight_pct)
        code = codes.get(line.type_code_id) if line.type_code_id else None
        if code is None:
            unlinked[line.name] = unlinked.get(line.name, 0.0) + weight
            continue
        if code.resolution == "no_series":
            no_series[code.code] = no_series.get(code.code, 0.0) + weight
            continue
        if code.resolution == "ambiguous":
            ambiguous[code.code] = ambiguous.get(code.code, 0.0) + weight
            continue

        registry_is_proxy = code.proxy_status == "proxy"
        if registry_is_proxy:
            proxied[code.code] = proxied.get(code.code, 0.0) + weight
        # The known defect, surfaced rather than trusted: the line's own
        # `is_proxy` / `line_proxy_status` and the type-code row disagree on a
        # substantial share of lines. The registry side is authoritative here
        # (PROXY_STATUS_SOURCE) and the contradiction is its own reason.
        line_is_proxy = (
            line.line_proxy_status == "proxy" if line.line_proxy_status
            else bool(line.is_proxy)
        )
        if line_is_proxy != registry_is_proxy:
            disagreeing[code.code] = disagreeing.get(code.code, 0.0) + weight

    reasons: list[TrustReason] = []
    if no_series:
        reasons.append(TrustReason(
            reason=REASON_NO_SERIES, subjects=sorted(no_series),
            weight_pct=sum(no_series.values()),
            detail="the type code names a series we hold no numbers for",
        ))
    if ambiguous:
        reasons.append(TrustReason(
            reason=REASON_AMBIGUOUS, subjects=sorted(ambiguous),
            weight_pct=sum(ambiguous.values()),
            detail="the type code resolves to nothing — somebody has to decide "
                   "what it means",
        ))
    if unlinked:
        reasons.append(TrustReason(
            reason=REASON_NO_TYPE_CODE, subjects=sorted(unlinked),
            weight_pct=sum(unlinked.values()),
            detail="index-linked line with no type-code link, so its resolution "
                   "cannot be checked",
        ))
    if proxied:
        reasons.append(TrustReason(
            reason=REASON_PROXY, subjects=sorted(proxied),
            weight_pct=sum(proxied.values()),
            detail="priced through a stand-in index",
        ))
    if disagreeing:
        reasons.append(TrustReason(
            reason=REASON_PROXY_DISAGREEMENT, subjects=sorted(disagreeing),
            weight_pct=sum(disagreeing.values()),
            detail=f"the cost line and its type code disagree on proxy status; "
                   f"the grade reads {PROXY_STATUS_SOURCE}",
        ))
    weights_open = not (WEIGHT_CLOSURE_MIN <= total_weight <= WEIGHT_CLOSURE_MAX)
    if weights_open:
        reasons.append(TrustReason(
            reason=REASON_WEIGHTS_OPEN, weight_pct=round(total_weight, 2),
            detail=f"weights sum to {total_weight:.2f}, outside the "
                   f"{WEIGHT_CLOSURE_MIN}–{WEIGHT_CLOSURE_MAX} band the catalog "
                   "convention allows (margin sits inside the total)",
        ))

    blocked_weight = sum(no_series.values()) + sum(ambiguous.values())
    proxied_weight = sum(proxied.values())

    if blocked_weight > 0:
        grade = GRADE_BLOCKED
    elif not code_ids:
        # Every index line is unlinked: there is nothing to grade against, which
        # is a different answer from "graded and found wanting".
        grade = GRADE_UNRATED
    elif weights_open or unlinked or proxied_weight > PROXY_WEIGHT_MEDIUM_MAX:
        grade = GRADE_LOW
    elif proxied_weight > 0 or disagreeing:
        grade = GRADE_MEDIUM
    else:
        grade = GRADE_HIGH

    return TrustAssessment(
        grade=grade,
        needs_review=grade in GRADES_NEEDING_REVIEW,
        reasons=reasons,
        total_weight=total_weight,
        indexed_weight=indexed_weight,
        proxied_weight=proxied_weight,
        blocked_weight=blocked_weight,
        line_count=len(lines),
        type_coded_lines=len(code_ids),
    )


# ── Applying it ──────────────────────────────────────────────────────────────

@dataclass
class ApplyResult:
    grade: str
    needs_review: bool
    # True when a standing sign-off was invalidated because the reviewed inputs
    # moved — the thing the fingerprint exists to catch.
    sign_off_invalidated: bool = False
    changed: bool = False


def apply_assessment(
    db: Session, coverage: FormulaRegionCoverage, assessment: TrustAssessment | None = None
) -> ApplyResult:
    """Store the grade, and re-queue the combo if a sign-off went stale.

    Does not commit. `coverage_tier` and `proxy_density_tier` are never touched:
    they are inputs, the grade is the output.
    """
    assessment = assessment or assess(db, coverage)
    lines = coverage_lines(db, coverage)
    fingerprint = fingerprint_for(lines)

    invalidated = False
    if coverage.reviewed_at is not None and coverage.review_fingerprint is not None \
            and coverage.review_fingerprint != fingerprint:
        # The sign-off was on a different recipe. Clear it rather than showing a
        # stale green tick — a reviewer vouched for numbers that have since moved.
        invalidated = True
        coverage.reviewed_at = None
        coverage.reviewed_by_id = None
        coverage.review_fingerprint = None
        coverage.review_derived_from = None

    signed_off = coverage.reviewed_at is not None
    # A live sign-off outranks the derivation: an expert who looked at a
    # proxy-heavy combo and accepted it should not be asked again every night.
    needs_review = assessment.needs_review and not signed_off

    before = (coverage.trust_grade, coverage.needs_review)
    coverage.trust_grade = assessment.grade
    coverage.trust_inputs = assessment.as_inputs()
    coverage.trust_computed_at = datetime.now(timezone.utc)
    coverage.needs_review = needs_review
    db.flush()

    return ApplyResult(
        grade=assessment.grade, needs_review=needs_review,
        sign_off_invalidated=invalidated,
        changed=invalidated or before != (assessment.grade, needs_review),
    )


def sign_off(
    db: Session, coverage: FormulaRegionCoverage, reviewer_id: uuid.UUID
) -> FormulaRegionCoverage:
    """Record an expert sign-off, pinned to the recipe that was reviewed."""
    lines = coverage_lines(db, coverage)
    coverage.needs_review = False
    coverage.reviewed_by_id = reviewer_id
    coverage.reviewed_at = datetime.now(timezone.utc)
    coverage.review_fingerprint = fingerprint_for(lines)
    # Same field name and JSONB shape as CON-5's staleness descriptor, so the
    # two do not become two formats for the same idea.
    coverage.review_derived_from = {
        "fingerprint": coverage.review_fingerprint,
        "lines": line_descriptor(lines),
    }
    db.flush()
    return coverage


@dataclass
class RecomputeReport:
    considered: int = 0
    graded: int = 0
    invalidated: int = 0
    by_grade: dict = field(default_factory=dict)

    def render(self) -> str:
        lines = [
            "Trust grades", "",
            f"  combos considered   {self.considered:5d}",
            f"  grade changed       {self.graded:5d}",
            f"  sign-offs invalidated {self.invalidated:3d}"
            "  (reviewed inputs moved)",
            "",
        ]
        for grade in TRUST_GRADES:
            lines.append(f"  {grade:10} {self.by_grade.get(grade, 0):5d}")
        return "\n".join(lines)


def recompute_all(db: Session, platform_only: bool = False) -> RecomputeReport:
    """Regrade every combo. Does not commit."""
    q = db.query(FormulaRegionCoverage)
    if platform_only:
        q = q.join(FormulaTemplate,
                   FormulaTemplate.id == FormulaRegionCoverage.template_id
                   ).filter(FormulaTemplate.team_id.is_(None))
    report = RecomputeReport()
    for coverage in q.all():
        result = apply_assessment(db, coverage)
        report.considered += 1
        if result.changed:
            report.graded += 1
        if result.sign_off_invalidated:
            report.invalidated += 1
        report.by_grade[result.grade] = report.by_grade.get(result.grade, 0) + 1
    return report


# ── The queue ────────────────────────────────────────────────────────────────

QUEUE_ORDERS = ("severity", "blocked_weight", "recipe_size", "region", "code")


def review_queue(
    db: Session,
    team_id: uuid.UUID | None = None,
    *,
    grades: list[str] | None = None,
    needs_review: bool | None = True,
    order_by: str = "severity",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Unreviewed combos **across the library**, not scoped to one template.

    Coverage was only listable per template before this, so the console and any
    triage ordering had nothing to read. Ordered by severity by default —
    ordering by region or name would leave a reviewer reading alphabetically
    through a queue whose whole point is "worst first".
    """
    if order_by not in QUEUE_ORDERS:
        raise ValueError(f"order_by must be one of {sorted(QUEUE_ORDERS)}")
    if grades:
        unknown = set(grades) - set(TRUST_GRADES)
        if unknown:
            raise ValueError(f"unknown grade(s): {sorted(unknown)}")

    q = (
        db.query(FormulaRegionCoverage, FormulaTemplate)
        .join(FormulaTemplate, FormulaTemplate.id == FormulaRegionCoverage.template_id)
        .filter(or_(FormulaTemplate.team_id.is_(None),
                    FormulaTemplate.team_id == team_id))
    )
    if needs_review is not None:
        q = q.filter(FormulaRegionCoverage.needs_review.is_(needs_review))
    if grades:
        q = q.filter(FormulaRegionCoverage.trust_grade.in_(grades))

    rows = q.all()
    total = len(rows)

    def sort_key(pair):
        coverage, template = pair
        inputs = coverage.trust_inputs or {}
        if order_by == "severity":
            return (GRADE_SEVERITY.get(coverage.trust_grade, 9),
                    -float(inputs.get("blocked_weight") or 0),
                    template.code or "", coverage.region)
        if order_by == "blocked_weight":
            return (-float(inputs.get("blocked_weight") or 0),
                    GRADE_SEVERITY.get(coverage.trust_grade, 9))
        if order_by == "recipe_size":
            return (-int(inputs.get("line_count") or 0),
                    GRADE_SEVERITY.get(coverage.trust_grade, 9))
        if order_by == "code":
            return (template.code or "", coverage.region)
        return (coverage.region, template.code or "")

    rows.sort(key=sort_key)
    window = rows[offset: offset + limit]

    # Resolved on read from the FK rather than stored: unit 11 moved
    # `reviewed_by` off a copied email precisely so a sign-off stays explicable
    # after the reviewer changes their address. A queue rendering a bare UUID
    # would hand that gain straight back.
    reviewer_ids = {c.reviewed_by_id for c, _ in window if c.reviewed_by_id}
    reviewer_names = {
        u.id: u.email
        for u in db.query(User).filter(User.id.in_(reviewer_ids)).all()
    } if reviewer_ids else {}

    return [
        {
            "template_id": coverage.template_id,
            "template_code": template.code,
            "template_name": template.name,
            "region": coverage.region,
            "variant": coverage.variant or "",
            "scope": "platform" if template.team_id is None else "team",
            "trust_grade": coverage.trust_grade,
            "needs_review": coverage.needs_review,
            "trust_inputs": coverage.trust_inputs,
            # Kept distinct from the grade: they answer different questions and
            # neither is overwritten by it.
            "coverage_tier": coverage.coverage_tier,
            "proxy_density_tier": coverage.proxy_density_tier,
            "reviewed_at": coverage.reviewed_at,
            "reviewed_by_id": coverage.reviewed_by_id,
            "reviewed_by_name": reviewer_names.get(coverage.reviewed_by_id),
            # Carried so the console can show a sign-off is pinned to the exact
            # line set that was signed off, rather than merely asserting it:
            # change a weight and the combo comes back here.
            "review_fingerprint": coverage.review_fingerprint,
        }
        for coverage, template in window
    ], total
