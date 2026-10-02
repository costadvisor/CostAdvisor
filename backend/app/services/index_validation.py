"""SCRUM-34 — index data-quality validation over the resolution layer.

The original framing of this ticket was "hold two readings of one series and
flag the outlier". We mostly do not hold two readings. What the drop *does*
supply, in quantity, is **declared and structural disagreement sitting in the
data unexamined** — contradictions that are mechanically checkable today,
against the layers DB-5/DB-6 loaded, with no statistics and no second feed.

Two kinds of finding, kept apart because they are produced differently:

* **declared** — the drop's own `_issues.csv`, already loaded as `drop_issues`
  (unit 3). Carried through verbatim, never recomputed. Re-deriving a delivered
  defect list would both duplicate it and risk quietly contradicting it.
* **derived** — what the checks below actually computed from the loaded rows.

**What is deliberately not a finding.** `resolution='no_series'` is 31 codes
that name the series they want and have no numbers for it — that is the
sourcing instruction the swap backlog already ranks by cost weight
(`proxy_derivation.swap_backlog`), not a defect, and emitting it here would be
a second, unranked copy of that queue. `proxy_status='unclassified'` is the
registry declining to say, which cannot contradict a line that does say.

Statistical cross-checking of two independent readings stays out, as the ticket
scopes it: a later mode, for the series where we actually hold two.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants.index_metadata import FREQUENCIES
from app.models.drop_issue import DropIssueRecord
from app.models.formula_template import FormulaTemplateComponent
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexCard, IndexMonthlyValue, TypeCode
from app.models.index_validation import IndexValidationFinding, IndexValidationRun

# Region tokens a series key may legitimately end with. Deliberately a closed
# set: `-ppi`, `-wb` and `-mb` are *sources*, not regions (DB-5's finding), so a
# key ending in one of those is not re-badged, it is differently sourced. A
# check that parsed any trailing token would report every one of them.
# The values are only for readable prose. **Comparison is on the code**, never
# the name: `index_cards.region` stores the drop's raw code (`CN`), and an
# earlier version of this check compared that against the mapped region name
# (`China`), which reported all 44 region-suffixed cards as contradicting
# themselves.
REGION_TOKENS = {
    "eu": "Europe", "na": "North America", "cn": "China", "in": "India",
    "apac": "APAC", "mea": "MEA", "la": "Latam", "gl": "Global",
}

# An agency string that marks itself as unverified. The drop writes this into
# the agency field rather than into a flag, so it reads as ordinary provenance
# until somebody looks.
UNVERIFIED_MARKER = "unverif"


@dataclass
class Finding:
    check_code: str
    severity: str
    subject_table: str
    subject_key: str
    summary: str
    subject_column: str | None = None
    left_label: str | None = None
    left_value: str | None = None
    right_label: str | None = None
    right_value: str | None = None
    detail: dict | None = None
    origin: str = "derived"

    def fingerprint(self) -> str:
        """Stable identity, so a re-run updates rather than duplicates.

        Built from what the finding *is about* and the values in conflict — not
        from counts or timestamps, which move without the defect changing. A
        contradiction whose two values change is genuinely a different finding
        and correctly gets a new fingerprint; the old one then resolves.
        """
        parts = [self.origin, self.check_code, self.subject_table,
                 self.subject_key, self.subject_column or "",
                 self.left_value or "", self.right_value or ""]
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


@dataclass
class RunReport:
    run_id: uuid.UUID | None = None
    findings: list[Finding] = field(default_factory=list)
    checks: dict[str, int] = field(default_factory=dict)
    n_new: int = 0
    n_resolved: int = 0

    def by_check(self, code: str) -> list[Finding]:
        return [f for f in self.findings if f.check_code == code]

    def render(self) -> str:
        out = [f"Index validation — {len(self.findings)} finding(s)", ""]
        for code, n in sorted(self.checks.items(), key=lambda kv: -kv[1]):
            out.append(f"  {n:>5}  {code}")
        out.append("")
        out.append(f"  new this run: {self.n_new}   "
                   f"resolved since last: {self.n_resolved}")
        return "\n".join(out)


# ── Derived checks ───────────────────────────────────────────────────────────

def check_proxy_status_contradiction(db: Session) -> list[Finding]:
    """A type code's registry `proxy_status` against the cost lines naming it.

    The two columns disagree on a large share of indexed lines, and DB-5
    deliberately kept **both** rather than picking a winner: `w_proxy` and
    `coverage_tier` were computed from the line value, so adopting registry
    truth would silently move hundreds of combos. Keeping both is the right call
    and is exactly why the contradiction has to be *surfaced* — otherwise
    whichever column a reader happens to consult looks authoritative.

    One finding per (code, direction) rather than per line: six hundred-odd
    lines collapse to a hundred-odd codes, and the code is what somebody fixes.
    """
    rows = (
        db.query(TypeCode.code, TypeCode.proxy_status,
                 FormulaTemplateComponent.is_proxy,
                 func.count(FormulaTemplateComponent.id))
        .join(FormulaTemplateComponent,
              FormulaTemplateComponent.type_code_id == TypeCode.id)
        .filter(FormulaTemplateComponent.component_type == "index",
                TypeCode.proxy_status.in_(("direct", "proxy")))
        .group_by(TypeCode.code, TypeCode.proxy_status,
                  FormulaTemplateComponent.is_proxy)
        .all()
    )
    out = []
    for code, registry, line_is_proxy, n_lines in rows:
        line_says = "proxy" if line_is_proxy else "direct"
        if line_says == registry:
            continue
        out.append(Finding(
            check_code="proxy_status_contradiction",
            severity="contradiction",
            subject_table="type_codes", subject_key=code,
            subject_column="proxy_status",
            left_label="type_codes.proxy_status", left_value=registry,
            right_label="combo_lines.proxy_status", right_value=line_says,
            summary=(f"{code}: the registry says {registry}, but {n_lines} cost "
                     f"line(s) naming it say {line_says}"),
            detail={"n_lines": n_lines},
        ))
    return out


def check_ambiguous_resolution(db: Session) -> list[Finding]:
    """`ambiguous` is a first-class resolution state, not a missing value.

    `no_series` still names the series it wanted — it means "that target has no
    numbers", which is a purchase. `ambiguous` means we cannot say which series
    the code points at, which is a decision. Different remedies, so the two are
    never collapsed, and only this one is a finding.
    """
    out = []
    for tc in db.query(TypeCode).filter(TypeCode.resolution == "ambiguous").all():
        note = f" — registry note: {tc.registry_note}" if tc.registry_note else ""
        out.append(Finding(
            check_code="resolution_ambiguous", severity="gap",
            subject_table="type_codes", subject_key=tc.code,
            subject_column="resolution",
            left_label="resolution", left_value="ambiguous",
            right_label="resolves_to", right_value=None,
            summary=f"{tc.code} cannot be pointed at one series{note}",
            detail={"label": tc.label, "ideal_index": tc.ideal_index},
        ))
    return out


_CARD_VS_SERIES = (
    # (card attribute, series attribute, column name for the finding)
    ("agency", "provider", "agency"),
    ("unit", "unit", "unit"),
    ("incoterm", "quoted_incoterm", "incoterm"),
)


def check_card_series_provenance(db: Session) -> list[Finding]:
    """A card's declared provenance against the series it sits on.

    Only compares where **both** sides state a value. A series with no declared
    incoterm is undeclared, not contradicted — and `decisions/index_basis.csv`
    shipped empty, so treating absence as disagreement would emit a finding for
    most of the library and bury the real ones.
    """
    out = []
    rows = (db.query(IndexCard, CommodityIndex)
            .join(CommodityIndex, CommodityIndex.id == IndexCard.commodity_id)
            .all())
    for card, series in rows:
        for card_attr, series_attr, column in _CARD_VS_SERIES:
            left, right = getattr(card, card_attr), getattr(series, series_attr)
            if left is None or right is None or left == right:
                continue
            out.append(Finding(
                check_code="card_series_provenance", severity="contradiction",
                subject_table="index_cards", subject_key=card.feed_key,
                subject_column=column,
                left_label=f"index_cards.{card_attr}", left_value=str(left),
                right_label=f"commodity_indexes.{series_attr}", right_value=str(right),
                summary=(f"{card.feed_key} declares {column} {left!r} but its "
                         f"series {series.commodity_key} declares {right!r}"),
            ))
    return out


# Columns where two cards on one series must agree, because the series has one
# identity. `region`/`region_label`/`name` are excluded on purpose: differing
# there is what having several cards on one series *means*.
_SIBLING_COLUMNS = ("agency", "unit", "incoterm", "source_freq", "frequency",
                    "category")


def check_sibling_card_disagreement(db: Session) -> list[Finding]:
    """Two cards on the same series describing it differently.

    This is where the card-vs-series contradiction actually lives. The loader
    copies a card's provenance onto its series, so card-vs-series compares a
    value against its own copy — but several series carry more than one card,
    and if two of them declare different units for one set of numbers, one is
    wrong.
    """
    by_series: dict[int, list[IndexCard]] = {}
    for card in db.query(IndexCard).all():
        by_series.setdefault(card.commodity_id, []).append(card)

    keys = {row.id: row.commodity_key for row in
            db.query(CommodityIndex.id, CommodityIndex.commodity_key).all()}
    out = []
    for commodity_id, cards in by_series.items():
        if len(cards) < 2:
            continue
        for column in _SIBLING_COLUMNS:
            values = {getattr(c, column) for c in cards
                      if getattr(c, column) is not None}
            if len(values) < 2:
                continue
            ordered = sorted(str(v) for v in values)
            out.append(Finding(
                check_code="sibling_card_disagreement", severity="contradiction",
                subject_table="commodity_indexes",
                subject_key=keys.get(commodity_id) or str(commodity_id),
                subject_column=column,
                left_label="card", left_value=ordered[0],
                right_label="card", right_value=" | ".join(ordered[1:]),
                summary=(f"{len(cards)} cards on one series declare "
                         f"{len(values)} different {column} values"),
                detail={"feed_keys": sorted(c.feed_key for c in cards),
                        "values": ordered},
            ))
    return out


def check_duplicate_default_region(db: Session) -> list[Finding]:
    """More than one card claiming to be the default region for a slug.

    DB-5 deliberately did **not** put a unique index on this — 18 slugs ship
    several defaults and the obvious constraint rejects the data outright. That
    is precisely the shape this ticket exists for: the data says something
    self-contradictory, so record it as a finding instead of either crashing the
    load or pretending it is fine.
    """
    slugs: dict[str, list[IndexCard]] = {}
    for card in db.query(IndexCard).filter(
            IndexCard.is_default_region.is_(True)).all():
        slugs.setdefault(card.feed_slug, []).append(card)
    out = []
    for slug, cards in slugs.items():
        if len(cards) < 2:
            continue
        regions = sorted(c.region or "?" for c in cards)
        out.append(Finding(
            check_code="duplicate_default_region", severity="contradiction",
            subject_table="index_cards", subject_key=slug,
            subject_column="is_default_region",
            left_label="default regions", left_value=", ".join(regions),
            right_label="expected", right_value="exactly one",
            summary=f"{slug} marks {len(cards)} cards as the default region",
            detail={"feed_keys": sorted(c.feed_key for c in cards)},
        ))
    return out


def check_forecast_only_series(db: Session) -> list[Finding]:
    """A series whose only points are forecasts.

    The ticket asks for this to be **queryable rather than log-only**, and the
    reason is that a forecast-only series is quietly dangerous: it charts, it
    has a latest value, and nothing about it says the number was never observed.
    `data_resolver` refuses forecast rows outright, so such a series resolves to
    nothing in a should-cost while still looking populated everywhere else.
    """
    rows = (
        db.query(IndexMonthlyValue.commodity_id,
                 func.count(IndexMonthlyValue.id).filter(
                     IndexMonthlyValue.kind == "actual").label("n_actual"),
                 func.count(IndexMonthlyValue.id).filter(
                     IndexMonthlyValue.kind == "forecast").label("n_forecast"))
        .group_by(IndexMonthlyValue.commodity_id)
        .all()
    )
    keys = {row.id: (row.commodity_key or row.name) for row in
            db.query(CommodityIndex.id, CommodityIndex.commodity_key,
                     CommodityIndex.name).all()}
    out = []
    for commodity_id, n_actual, n_forecast in rows:
        if n_actual or not n_forecast:
            continue
        out.append(Finding(
            check_code="forecast_only_series", severity="gap",
            subject_table="commodity_indexes",
            subject_key=keys.get(commodity_id) or str(commodity_id),
            subject_column="index_monthly_values.kind",
            left_label="actual months", left_value="0",
            right_label="forecast months", right_value=str(n_forecast),
            summary=("series has forecast points and no observed history — it "
                     "charts but cannot price"),
        ))
    return out


def check_unverified_agency(db: Session) -> list[Finding]:
    """An agency string that marks itself unverified.

    The drop writes this into the agency field rather than a flag, so it reads
    as ordinary provenance in every UI that shows a source.
    """
    out = []
    for card in db.query(IndexCard).filter(
            IndexCard.agency.ilike(f"%{UNVERIFIED_MARKER}%")).all():
        out.append(Finding(
            check_code="unverified_agency", severity="note",
            subject_table="index_cards", subject_key=card.feed_key,
            subject_column="agency",
            left_label="agency", left_value=card.agency,
            summary=f"{card.feed_key} declares an unverified source",
        ))
    return out


def check_frequency_vocabulary(db: Session) -> list[Finding]:
    """A declared cadence outside the known vocabulary.

    `FREQUENCIES` is what the pre-drop seeders assert against; the DB-5 loader
    stores the string as free text, so an unknown cadence loads here and would
    fail there. Reporting it keeps the two paths from drifting apart silently.
    """
    known = set(FREQUENCIES)
    out = []
    for card in db.query(IndexCard).all():
        for column in ("frequency", "source_freq"):
            value = getattr(card, column)
            if value is None or value in known:
                continue
            out.append(Finding(
                check_code="frequency_outside_vocabulary", severity="note",
                subject_table="index_cards", subject_key=card.feed_key,
                subject_column=column,
                left_label="declared", left_value=value,
                right_label="vocabulary",
                right_value="app.constants.index_metadata.FREQUENCIES",
                summary=f"{card.feed_key} declares an unknown {column}: {value!r}",
            ))
    return out


def check_region_rebadged(db: Session) -> list[Finding]:
    """A card whose region contradicts the region baked into its series key.

    Only fires when the key's trailing token is a **known region token** — the
    `-ppi` / `-wb` / `-mb` suffixes name a source, not a region, and a check
    that parsed any trailing token would report every one of them as re-badged.
    DB-5's rule stands (region lives on the card, never parsed from the key);
    this reports only where the two disagree anyway.
    """
    out = []
    rows = (db.query(IndexCard, CommodityIndex.commodity_key)
            .join(CommodityIndex, CommodityIndex.id == IndexCard.commodity_id)
            .all())
    for card, key in rows:
        if not key or "-" not in key or card.region is None:
            continue
        token = key.rsplit("-", 1)[1].lower()
        if token not in REGION_TOKENS:
            continue
        # Both sides in the drop's own vocabulary. A card filed under `multi`
        # never matches a region token, which is the point: a multi-region card
        # sitting on a region-specific series is quoting one region's numbers
        # for all of them.
        if card.region.lower() == token:
            continue
        out.append(Finding(
            check_code="region_rebadged", severity="contradiction",
            subject_table="index_cards", subject_key=card.feed_key,
            subject_column="region",
            left_label="index_cards.region", left_value=card.region,
            right_label="series key names", right_value=token.upper(),
            summary=(f"{card.feed_key} is filed under {card.region} but sits on "
                     f"series {key}, whose key names "
                     f"{REGION_TOKENS[token]} ({token.upper()})"),
        ))
    return out


DERIVED_CHECKS = (
    check_proxy_status_contradiction,
    check_ambiguous_resolution,
    check_card_series_provenance,
    check_sibling_card_disagreement,
    check_duplicate_default_region,
    check_forecast_only_series,
    check_unverified_agency,
    check_frequency_vocabulary,
    check_region_rebadged,
)

# The check code each function emits, so a run can report "ran, found nothing"
# rather than omitting the check entirely — which would be indistinguishable
# from never having run it.
CHECK_CODES = {
    check_proxy_status_contradiction: "proxy_status_contradiction",
    check_ambiguous_resolution: "resolution_ambiguous",
    check_card_series_provenance: "card_series_provenance",
    check_sibling_card_disagreement: "sibling_card_disagreement",
    check_duplicate_default_region: "duplicate_default_region",
    check_forecast_only_series: "forecast_only_series",
    check_unverified_agency: "unverified_agency",
    check_frequency_vocabulary: "frequency_outside_vocabulary",
    check_region_rebadged: "region_rebadged",
}
DECLARED_CHECK_CODE = "drop_issue"


# ── Carried through, never recomputed ────────────────────────────────────────

def carry_declared(db: Session) -> list[Finding]:
    """The drop's `_issues.csv`, already loaded as `drop_issues`.

    Its own classification is preserved rather than reinterpreted: `blocking` is
    a real NOT NULL / FK failure, `awaiting_decision` waits on a human filling
    in one of the two forms in `decisions/`, and the rest is provenance.
    """
    out = []
    for rec in db.query(DropIssueRecord).all():
        severity = ("contradiction" if rec.blocking
                    else "gap" if rec.awaiting_decision else "note")
        out.append(Finding(
            origin="declared", check_code=DECLARED_CHECK_CODE, severity=severity,
            subject_table=rec.source_table, subject_key=rec.source_key,
            subject_column=rec.source_column,
            left_label="declared problem", left_value=rec.problem,
            summary=f"{rec.source_table}.{rec.source_column}: {rec.problem}",
            detail={"awaiting_decision": rec.awaiting_decision,
                    "blocking": rec.blocking},
        ))
    return out


# ── The run ──────────────────────────────────────────────────────────────────

def collect(db: Session, *, include_declared: bool = True) -> RunReport:
    """Every finding, without touching the store.

    The dry path and the real path share this, so a report is a rehearsal
    rather than a second implementation that could drift.
    """
    report = RunReport()
    for check in DERIVED_CHECKS:
        # Seed the count first: a check that ran and found nothing must report
        # zero, not be absent.
        report.checks.setdefault(CHECK_CODES[check], 0)
        found = check(db)
        for finding in found:
            report.checks[finding.check_code] = \
                report.checks.get(finding.check_code, 0) + 1
        report.findings.extend(found)
    if include_declared:
        declared = carry_declared(db)
        report.checks[DECLARED_CHECK_CODE] = len(declared)
        report.findings.extend(declared)
    return report


def run_validation(db: Session, *, include_declared: bool = True,
                   note: str | None = None) -> RunReport:
    """Collect, then reconcile against the stored findings.

    Re-runnable without duplicating: a finding is matched by fingerprint, so a
    standing one is touched rather than inserted. A stored finding the run did
    not re-observe is **stamped resolved, not deleted** — the evidence that a
    fix landed is worth more than a tidy table, and a finding that comes back
    un-resolves rather than appearing to be new.
    """
    now = datetime.now(timezone.utc)
    run = IndexValidationRun(started_at=now)
    db.add(run)
    db.flush()

    report = collect(db, include_declared=include_declared)
    report.run_id = run.id

    existing = {f.fingerprint: f for f in db.query(IndexValidationFinding).all()}
    seen: set[str] = set()
    for finding in report.findings:
        fp = finding.fingerprint()
        if fp in seen:
            # Two identical findings in one run is the fingerprint doing its
            # job; storing the second would violate the unique constraint.
            continue
        seen.add(fp)
        stored = existing.get(fp)
        if stored is None:
            db.add(IndexValidationFinding(
                fingerprint=fp, origin=finding.origin,
                check_code=finding.check_code, severity=finding.severity,
                subject_table=finding.subject_table,
                subject_key=finding.subject_key[:255],
                subject_column=finding.subject_column,
                left_label=finding.left_label, left_value=finding.left_value,
                right_label=finding.right_label, right_value=finding.right_value,
                summary=finding.summary, detail=finding.detail,
                first_seen_run_id=run.id, last_seen_run_id=run.id,
                first_seen_at=now, last_seen_at=now))
            report.n_new += 1
        else:
            stored.last_seen_run_id = run.id
            stored.last_seen_at = now
            # Came back: it was never fixed, or the fix regressed. Either way
            # this is the same finding, not a new one.
            stored.resolved_at = None
            # Counts and prose move without the defect changing; keep them fresh.
            stored.summary = finding.summary
            stored.detail = finding.detail

    if include_declared:
        stale = [f for fp, f in existing.items() if fp not in seen]
    else:
        # A derived-only run has no opinion about declared findings, so it must
        # not resolve them by silence.
        stale = [f for fp, f in existing.items()
                 if fp not in seen and f.origin != "declared"]
    for stored in stale:
        if stored.resolved_at is None:
            stored.resolved_at = now
            report.n_resolved += 1

    run.finished_at = datetime.now(timezone.utc)
    run.checks = report.checks
    run.n_findings = len(seen)
    run.n_new = report.n_new
    run.n_resolved = report.n_resolved
    run.note = note
    db.flush()
    return report
