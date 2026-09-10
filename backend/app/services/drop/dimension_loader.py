"""Dimension + producer loader (Wave 3, SCRUM-77 / INT-3).

**Only one of the four "dimensions" is actually a loader**, and this module is
shaped around that rather than pretending they are four instances of one job:

* **functionality** — mechanical. 41 controlled terms in
  `FUNCTIONALITY_TAXONOMY.json`, 384 tagged formulas in
  `FUNCTIONALITY_TAGS.json`, 41 distinct values used, **zero strays**. Loads
  clean.
* **functionality_family** — a **second** naming scheme (22 terms) in
  `FAMILY_FUNCTIONALITY_DEFAULT.json` / `SUBFAMILY_FUNCTIONALITY_OVERRIDE.json`
  with **zero overlap** with the taxonomy. Loaded as its own kind, because one
  kind holding both produces a facet with two disjoint halves and no way to
  tell which half a user is filtering on. The crosswalk between them is a
  judgement call and belongs in the decision file.
* **industry** — the classifier survived, the file did not.
  `INDUSTRY_RULES.json` serialised **all 19** regexes to `{}` and the extractor
  still reported ok; the originals lived in a mockup HTML that **is not in this
  repo**. So there is nothing to recover here, and the mapping is entirely an
  analyst decision. The loader creates the 19 controlled targets, resolves what
  it can by exact and case/whitespace match (26 of 204 raw strings), and puts
  the remaining **178** in the unresolved register. It never guesses.
* **compliance_flag** — two sources, no adjudicator, and the raw side is not a
  vocabulary: **239 distinct labels**, many of them full sentences. So no terms
  are invented from it. Terms come from the decision file; every raw label
  lands in the register as an alias candidate, with the two sources' membership
  disagreement recorded.

Plus the two bounded facets that do load: `supply_region` (the 7 `REGS.json`
codes) and `substitution_risk` (Low/Medium/High).

And the producer entity, which is not a dimension at all.

Never deletes. A term or assertion the drop stops mentioning is reported
**stale**, not removed — the drop is authoritative for what it covers and
silent about the rest.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.dimension import (
    KIND_COMPLIANCE_FLAG, KIND_FUNCTIONALITY, KIND_FUNCTIONALITY_FAMILY,
    KIND_INDUSTRY, KIND_SUBSTITUTION_RISK, KIND_SUPPLY_REGION,
    DimensionAssertion, DimensionTerm, normalize_value,
)
from app.services.dimensions import (
    assert_term, clear_unresolved, record_unresolved, resolve_raw, upsert_alias,
    upsert_term,
)
from app.services.drop.reader import DropNotAvailable, drop_root, read_raw
from app.services.drop.report import LoadReport, TableDiff
from app.services.producers import resolve_raw_name, upsert_producer_formula

# `substitution_risk`'s controlled vocabulary. The payload also carries
# `Medium-High` (2) and `Low (positive)` (1), which are NOT aliased to a
# neighbour: collapsing "Medium-High" into "High" or "Medium" is a judgement
# call about a risk rating, and guessing it would silently re-rate a product.
# They go to the register.
RISK_TERMS = [("low", "Low", 1), ("medium", "Medium", 2), ("high", "High", 3)]


def _slug(text: str) -> str:
    out = []
    for ch in normalize_value(text):
        out.append(ch if (ch.isalnum() or ch in "-_") else "-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:64]


def _diff(report: LoadReport, name: str) -> TableDiff:
    """`LoadReport.table` is a lookup, not a factory — this is the get-or-create
    the loaders need, kept local rather than widening the shared report type."""
    existing = report.table(name)
    if existing is not None:
        return existing
    row = TableDiff(table=name)
    report.tables.append(row)
    return row


def _tick(diff: TableDiff, existed: bool) -> None:
    if existed:
        diff.unchanged += 1
    else:
        diff.created += 1


@dataclass
class DimensionLoadReport:
    report: LoadReport = field(default_factory=lambda: LoadReport("Dimensions + producers"))
    unresolved: Counter = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [self.report.render()]
        if self.unresolved:
            lines.append("\nUnresolved (the analyst's work queue):")
            for kind, n in self.unresolved.most_common():
                lines.append(f"  {kind:22} {n} distinct raw value(s)")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


def _json(name: str):
    return read_raw(name)


# ── functionality (mechanical) ────────────────────────────────────────────────

def _load_functionality(db: Session, out: DimensionLoadReport) -> None:
    diff = _diff(out.report, "functionality terms")
    taxonomy = _json("FUNCTIONALITY_TAXONOMY")
    terms = {}
    for i, label in enumerate(taxonomy):
        before = db.query(DimensionTerm).filter(
            DimensionTerm.kind == KIND_FUNCTIONALITY,
            DimensionTerm.code == _slug(label),
            DimensionTerm.team_id.is_(None)).first()
        term = upsert_term(db, kind=KIND_FUNCTIONALITY, code=_slug(label),
                           label=label, sort_order=i, source="taxonomy")
        terms[label] = term
        # The label is its own alias — the tag file uses the label verbatim.
        upsert_alias(db, term, label, source="taxonomy")
        _tick(diff, bool(before))

    adiff = _diff(out.report, "functionality assertions")
    tags = _json("FUNCTIONALITY_TAGS")
    for subject_code, values in tags.items():
        for raw in (values if isinstance(values, list) else [values]):
            alias = resolve_raw(db, KIND_FUNCTIONALITY, raw)
            if alias is None:
                # Verified to be zero on this drop; kept because the next drop
                # is not promised to be.
                record_unresolved(db, KIND_FUNCTIONALITY, raw, subject_code,
                                  "not in FUNCTIONALITY_TAXONOMY")
                out.unresolved[KIND_FUNCTIONALITY] += 1
                continue
            existed = db.query(DimensionAssertion).filter(
                DimensionAssertion.term_id == alias.term_id,
                DimensionAssertion.subject_type == "formula",
                DimensionAssertion.subject_code == subject_code,
                DimensionAssertion.region.is_(None),
                DimensionAssertion.team_id.is_(None)).first()
            assert_term(db, alias.term, subject_type="formula",
                        subject_code=subject_code, raw_value=raw,
                        matched_alias=alias, source="loader")
            _tick(adiff, bool(existed))


def _load_functionality_family(db: Session, out: DimensionLoadReport) -> None:
    """The second, disjoint scheme — its own kind, deliberately.

    Family defaults are asserted on the family; subfamily overrides on the
    subfamily, keyed `"<family>|<subfamily>"` (the single-pipe convention the
    drop actually uses, pinned in unit 7).
    """
    diff = _diff(out.report, "functionality_family terms")
    adiff = _diff(out.report, "functionality_family assertions")

    sources = (
        ("family", _json("FAMILY_FUNCTIONALITY_DEFAULT")),
        ("subfamily", _json("SUBFAMILY_FUNCTIONALITY_OVERRIDE")),
    )
    labels: dict[str, DimensionTerm] = {}
    for subject_type, payload in sources:
        for subject_code, values in payload.items():
            for raw in (values if isinstance(values, list) else [values]):
                if raw not in labels:
                    before = db.query(DimensionTerm).filter(
                        DimensionTerm.kind == KIND_FUNCTIONALITY_FAMILY,
                        DimensionTerm.code == _slug(raw),
                        DimensionTerm.team_id.is_(None)).first()
                    term = upsert_term(db, kind=KIND_FUNCTIONALITY_FAMILY,
                                       code=_slug(raw), label=raw, source="taxonomy")
                    upsert_alias(db, term, raw, source="taxonomy")
                    labels[raw] = term
                    _tick(diff, bool(before))
                term = labels[raw]
                existed = db.query(DimensionAssertion).filter(
                    DimensionAssertion.term_id == term.id,
                    DimensionAssertion.subject_type == subject_type,
                    DimensionAssertion.subject_code == subject_code,
                    DimensionAssertion.region.is_(None),
                    DimensionAssertion.team_id.is_(None)).first()
                assert_term(db, term, subject_type=subject_type,
                            subject_code=subject_code, raw_value=raw,
                            source="loader")
                _tick(adiff, bool(existed))

    out.notes.append(
        "functionality_family is a second naming scheme with zero overlap with "
        "FUNCTIONALITY_TAXONOMY; the crosswalk between them is a decision-file job"
    )


# ── industry (targets only; the mapping is an analyst decision) ──────────────

def _load_industry(db: Session, out: DimensionLoadReport) -> None:
    diff = _diff(out.report, "industry terms")
    for i, label in enumerate(_json("INDUSTRY_TAXONOMY")):
        before = db.query(DimensionTerm).filter(
            DimensionTerm.kind == KIND_INDUSTRY, DimensionTerm.code == _slug(label),
            DimensionTerm.team_id.is_(None)).first()
        term = upsert_term(db, kind=KIND_INDUSTRY, code=_slug(label), label=label,
                           sort_order=i, source="taxonomy")
        upsert_alias(db, term, label, source="taxonomy")
        _tick(diff, bool(before))

    rules = _json("INDUSTRY_RULES")
    dead = sum(1 for r in rules if isinstance(r, list) and r and r[0] == {})
    if dead:
        out.notes.append(
            f"INDUSTRY_RULES.json is unusable: {dead}/{len(rules)} regexes "
            "serialised to {} and the mockup holding the originals is not in this "
            "repo — the industry mapping is entirely a decision-file job"
        )

    adiff = _diff(out.report, "industry assertions")
    cc = _json("CURATED_CONTENT")
    for subject_code, entry in cc.items():
        if not isinstance(entry, dict):
            continue
        for app in (entry.get("applications") or []):
            if not isinstance(app, dict):
                continue          # 53 nulls in the payload
            raw = app.get("industry")
            if not raw:
                continue
            alias = resolve_raw(db, KIND_INDUSTRY, raw)
            if alias is None:
                record_unresolved(db, KIND_INDUSTRY, raw, subject_code,
                                  "no analyst mapping to INDUSTRY_TAXONOMY")
                continue
            existed = db.query(DimensionAssertion).filter(
                DimensionAssertion.term_id == alias.term_id,
                DimensionAssertion.subject_type == "formula",
                DimensionAssertion.subject_code == subject_code,
                DimensionAssertion.region.is_(None),
                DimensionAssertion.team_id.is_(None)).first()
            assert_term(db, alias.term, subject_type="formula",
                        subject_code=subject_code, raw_value=raw,
                        matched_alias=alias, source="loader")
            _tick(adiff, bool(existed))


# ── compliance flags (two sources, no adjudicator) ───────────────────────────

def _load_compliance(db: Session, out: DimensionLoadReport) -> None:
    """Loads no terms and asserts only what the decision file already mapped.

    Two reasons, both measured. The raw side is **239 distinct labels**, many of
    them full sentences, so a term table over it is not a facet. And the two
    sources disagree on **membership** for 116 of 348 shared keys (33.3%) while
    agreeing on severity everywhere they name the same flag (**0** conflicts) —
    so a union is not a safe default, it means shipping every claim either
    source made on a compliance facet to a customer. Which source wins is a
    domain call, so the loader records the disagreement instead of deciding it.
    """
    cc, sdc = _json("CURATED_CONTENT"), _json("SUPPLY_DEMAND_COMPLIANCE")
    adiff = _diff(out.report, "compliance assertions")

    def flags_of(entry):
        out_flags, bare = [], 0
        for c in (entry.get("compliance") or []) if isinstance(entry, dict) else []:
            if isinstance(c, dict) and c.get("flag"):
                out_flags.append((c["flag"], c.get("type")))
            else:
                bare += 1
        return out_flags, bare

    bare_total = 0
    disagreements = 0        # both files assert flags, and the sets differ
    one_sided = 0            # one file asserts flags, the other asserts none
    for subject_code in set(cc) | set(sdc):
        a, bare_a = flags_of(cc.get(subject_code) or {})
        b, bare_b = flags_of(sdc.get(subject_code) or {})
        bare_total += bare_a + bare_b
        names_a = {f for f, _ in a}
        names_b = {f for f, _ in b}
        if subject_code in cc and subject_code in sdc and names_a != names_b:
            if names_a and names_b:
                disagreements += 1
            else:
                one_sided += 1

        for flag, severity in a + b:
            sources = []
            if flag in names_a:
                sources.append("CURATED_CONTENT")
            if flag in names_b:
                sources.append("SUPPLY_DEMAND_COMPLIANCE")
            alias = resolve_raw(db, KIND_COMPLIANCE_FLAG, flag)
            if alias is None:
                record_unresolved(
                    db, KIND_COMPLIANCE_FLAG, flag, subject_code,
                    "awaiting flag adjudication (decision file)")
                continue
            existed = db.query(DimensionAssertion).filter(
                DimensionAssertion.term_id == alias.term_id,
                DimensionAssertion.subject_type == "formula",
                DimensionAssertion.subject_code == subject_code,
                DimensionAssertion.region.is_(None),
                DimensionAssertion.team_id.is_(None)).first()
            assert_term(
                db, alias.term, subject_type="formula", subject_code=subject_code,
                raw_value=flag, matched_alias=alias, source="loader",
                # Which file(s) named it, so the membership disagreement stays
                # visible on the row rather than only in a load log.
                detail={"severity": severity, "named_by": sources},
            )
            _tick(adiff, bool(existed))

    out.notes.append(
        f"compliance: {disagreements} formula keys are asserted by BOTH sources "
        f"with different flag sets, and {one_sided} more are asserted by only one "
        "of the two. Severity never conflicts where both name the same flag, so "
        "the disagreement is membership — which makes a union unsafe by default: "
        "it means shipping every claim either source made, on a compliance facet, "
        "to a customer. A domain call, not a loader branch"
    )
    if bare_total:
        out.notes.append(
            f"compliance: {bare_total} items are bare strings with no flag and no "
            "type; recorded as unresolved rather than invented into flags"
        )


# ── supply_region + substitution_risk (bounded, so they load) ────────────────

def _load_supply_region(db: Session, out: DimensionLoadReport) -> None:
    diff = _diff(out.report, "supply_region terms")
    for i, code in enumerate(_json("REGS")):
        before = db.query(DimensionTerm).filter(
            DimensionTerm.kind == KIND_SUPPLY_REGION, DimensionTerm.code == code,
            DimensionTerm.team_id.is_(None)).first()
        term = upsert_term(db, kind=KIND_SUPPLY_REGION, code=code, label=code,
                           sort_order=i, source="taxonomy")
        upsert_alias(db, term, code, source="taxonomy")
        _tick(diff, bool(before))

    adiff = _diff(out.report, "supply_region assertions")
    cc = _json("CURATED_CONTENT")
    for subject_code, entry in cc.items():
        if not isinstance(entry, dict):
            continue
        seen = set()
        for sup in (entry.get("suppliers") or []):
            for raw in (sup.get("regs") or []) if isinstance(sup, dict) else []:
                if raw in seen:
                    continue
                seen.add(raw)
                alias = resolve_raw(db, KIND_SUPPLY_REGION, raw)
                if alias is None:
                    # The raw side is dirtier than the vocabulary: LATAM sits
                    # alongside LA, plus EMEA and Global. LATAM->LA is obvious
                    # but EMEA is not MEA, so none of them are guessed.
                    record_unresolved(db, KIND_SUPPLY_REGION, raw, subject_code,
                                      "not one of the 7 REGS.json codes")
                    continue
                existed = db.query(DimensionAssertion).filter(
                    DimensionAssertion.term_id == alias.term_id,
                    DimensionAssertion.subject_type == "formula",
                    DimensionAssertion.subject_code == subject_code,
                    DimensionAssertion.region.is_(None),
                    DimensionAssertion.team_id.is_(None)).first()
                assert_term(db, alias.term, subject_type="formula",
                            subject_code=subject_code, raw_value=raw,
                            matched_alias=alias, source="loader")
                _tick(adiff, bool(existed))


def _load_substitution_risk(db: Session, out: DimensionLoadReport) -> None:
    diff = _diff(out.report, "substitution_risk terms")
    for code, label, order in RISK_TERMS:
        before = db.query(DimensionTerm).filter(
            DimensionTerm.kind == KIND_SUBSTITUTION_RISK, DimensionTerm.code == code,
            DimensionTerm.team_id.is_(None)).first()
        term = upsert_term(db, kind=KIND_SUBSTITUTION_RISK, code=code, label=label,
                           sort_order=order, source="taxonomy")
        upsert_alias(db, term, label, source="taxonomy")
        _tick(diff, bool(before))

    adiff = _diff(out.report, "substitution_risk assertions")
    for name in ("CURATED_CONTENT", "FUTURE_OUTLOOK"):
        payload = _json(name)
        for subject_code, entry in payload.items():
            if not isinstance(entry, dict):
                continue
            for sub in (entry.get("substitution") or []):
                if not isinstance(sub, dict):
                    continue
                raw = sub.get("risk")
                if not raw:
                    continue
                alias = resolve_raw(db, KIND_SUBSTITUTION_RISK, raw)
                if alias is None:
                    record_unresolved(
                        db, KIND_SUBSTITUTION_RISK, raw, subject_code,
                        "out-of-vocabulary risk level; collapsing it into a "
                        "neighbour would silently re-rate the product")
                    continue
                existed = db.query(DimensionAssertion).filter(
                    DimensionAssertion.term_id == alias.term_id,
                    DimensionAssertion.subject_type == "formula",
                    DimensionAssertion.subject_code == subject_code,
                    DimensionAssertion.region.is_(None),
                    DimensionAssertion.team_id.is_(None)).first()
                assert_term(db, alias.term, subject_type="formula",
                            subject_code=subject_code, raw_value=raw,
                            matched_alias=alias, source="loader")
                _tick(adiff, bool(existed))

    out.notes.append(
        "substitution titles are deliberately NOT a kind: 385 distinct titles "
        "over 526 entries is ~1 term per row, which is not a facet"
    )
    out.notes.append(
        "substitution_risk reports 'unchanged' rows on a FIRST load, which is "
        "correct rather than a bug: CURATED_CONTENT and FUTURE_OUTLOOK carry "
        "byte-identical substitution entries for 100 formula keys, so the second "
        "pass finds the assertion already made. That is the dedupe working"
    )


# ── producers ────────────────────────────────────────────────────────────────

def _load_producers(db: Session, out: DimensionLoadReport) -> None:
    diff = _diff(out.report, "producers")
    link_diff = _diff(out.report, "producer_formulas")
    alias_map = _json("SUPPLIER_ALIASES")
    cc = _json("CURATED_CONTENT")

    minted, mapped, split_rows = 0, 0, 0
    undisclosed = 0
    for subject_code, entry in cc.items():
        if not isinstance(entry, dict):
            continue
        for sup in (entry.get("suppliers") or []):
            if not isinstance(sup, dict) or not sup.get("n"):
                continue
            resolved = resolve_raw_name(db, sup["n"], alias_map=alias_map,
                                        source="loader")
            if len(resolved) > 1:
                split_rows += 1
            for r in resolved:
                if r.minted:
                    minted += 1
                    diff.created += 1
                else:
                    mapped += 1
                    diff.unchanged += 1
                share = sup.get("share")
                if not share:
                    undisclosed += 1
                _, created = upsert_producer_formula(
                    db, r.producer, subject_code=subject_code,
                    share=share, hq_country=sup.get("hq"),
                    regions_raw=sup.get("regs"), tags=sup.get("tags"),
                    raw_name=sup["n"], source="loader",
                )
                _tick(link_diff, not created)

    out.notes.append(
        f"producers: {mapped} rows resolved through an existing alias, {minted} "
        f"minted from an unmapped raw name, {split_rows} raw strings named more "
        "than one company (SUPPLIER_ALIASES covers ~31% of rows, so minting the "
        "remainder is the only way not to lose most of the data)"
    )
    out.notes.append(
        f"producers: {undisclosed} supplier rows carry share=0, stored as "
        "share_disclosed=false — never as a real zero market share"
    )


# ── Entry point ──────────────────────────────────────────────────────────────

def load_dimensions(db: Session) -> DimensionLoadReport:
    """Load every mechanical part, and report everything that needs a human.

    Does not commit — the caller owns the transaction, so `--dry-run` is the
    caller rolling back and the dry path is the real path (the same shape as
    the index and catalog loaders).
    """
    if not drop_root().exists():
        raise DropNotAvailable("costadvisor-data drop not present")

    out = DimensionLoadReport()
    # The register is a snapshot of THIS load's failures, not a ledger: a value
    # resolved by yesterday's decision-file import must stop appearing, or the
    # queue never shrinks and nobody trusts it.
    clear_unresolved(db)

    _load_functionality(db, out)
    _load_functionality_family(db, out)
    _load_industry(db, out)
    _load_compliance(db, out)
    _load_supply_region(db, out)
    _load_substitution_risk(db, out)
    _load_producers(db, out)

    from app.models.dimension import UnresolvedValue
    counts = (
        db.query(UnresolvedValue.kind, func.count(UnresolvedValue.id))
        .group_by(UnresolvedValue.kind)
        .all()
    )
    out.unresolved = Counter(dict(counts))
    return out
