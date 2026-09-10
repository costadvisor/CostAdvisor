"""Repair orphaned dimension assertions (Wave 3, SCRUM-77 follow-up).

**What went wrong.** An earlier loader pass wrote assertions against aliases that
were later rebuilt. `dimension_assertions.matched_alias_id` is `ON DELETE SET
NULL`, so when those alias rows went the assertions kept pointing at whatever
term they had been given and lost the only record of how they got there. The
never-delete rule — correct in general, because the drop is authoritative for
what it covers and silent about the rest — then preserved them.

The result on the live catalogue is 141 `industry` assertions whose raw value has
nothing to do with the term they sit under: "Adhesives & Sealants" reports 143
formulas of which 2 are real, the rest carrying "Food Ingredients",
"Agrochemicals", "Mining". A facet is only as good as its mapping, so this is not
cosmetic.

**The predicate is deliberately narrow**, and it is the whole safety argument:

    matched_alias_id IS NULL          -- nothing records how this was mapped
    AND resolve_raw(kind, raw_value)  -- and the current vocabulary does not
        does not yield this term         support the claim either

Both halves are needed. Plenty of sound rows have no alias — `functionality_family`
has 56 of them, every one still resolving to its own term through the current
vocabulary — so deleting on the first half alone would destroy real data. A row
that fails *both* is unsupported by anything: no recorded provenance, and no
mapping that would produce it today. Nothing can be lost by removing it that a
re-run of the loader would not put back correctly.

A row with no `raw_value` at all is **left alone**: there is nothing to
re-resolve, so it cannot be judged, and "cannot tell" is not "wrong".

Idempotent by construction — a second run finds nothing, because the rows it
would match are gone.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.dimension import DimensionAssertion, DimensionTerm
from app.services.dimensions import resolve_raw


@dataclass
class OrphanRow:
    assertion_id: uuid.UUID
    kind: str
    term_code: str
    subject_type: str
    subject_code: str
    raw_value: str | None
    source: str


@dataclass
class RepairReport:
    scanned: int = 0
    # No alias, but the raw value still resolves to this term — sound, kept.
    alias_less_but_sound: int = 0
    # No alias and no raw value: unjudgeable, so left in place rather than
    # guessed at in either direction.
    unjudgeable: int = 0
    orphans: list[OrphanRow] = field(default_factory=list)
    deleted: int = 0
    dry_run: bool = True

    def by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.orphans:
            out[o.kind] = out.get(o.kind, 0) + 1
        return out

    def by_term(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.orphans:
            key = f"{o.kind}/{o.term_code}"
            out[key] = out.get(key, 0) + 1
        return out

    def render(self) -> str:
        lines = [
            "Dimension assertion repair",
            f"  scanned                     {self.scanned}",
            f"  no alias but still sound    {self.alias_less_but_sound}  (kept)",
            f"  no alias and no raw value   {self.unjudgeable}  (kept — nothing to judge)",
            f"  provably orphaned           {len(self.orphans)}",
        ]
        if self.orphans:
            lines.append("    by facet: " + ", ".join(
                f"{k}={n}" for k, n in sorted(self.by_kind().items())))
            lines.append("    worst-hit terms:")
            for key, n in sorted(self.by_term().items(), key=lambda kv: -kv[1])[:8]:
                lines.append(f"      {key:<44} {n}")
            lines.append("    examples:")
            for o in self.orphans[:5]:
                lines.append(
                    f"      {o.term_code:<26} <- {o.raw_value!r} on {o.subject_code}")
        lines.append(
            f"  deleted                     {self.deleted}"
            + ("  (DRY RUN — nothing written)" if self.dry_run else ""))
        return "\n".join(lines)


def find_orphans(db: Session, kind: str | None = None,
                 term_codes: list[str] | None = None) -> RepairReport:
    """Every assertion that no recorded alias and no current mapping supports.

    `term_codes` narrows to specific terms. That is an operator need in its own
    right — fixing one bad term without touching the rest of a facet — and it is
    also what makes this safe to exercise from a test, because the suite runs
    against the same database as the app and an unscoped `apply=True` would
    delete real rows as a side effect.
    """
    report = RepairReport()
    q = (
        db.query(DimensionAssertion, DimensionTerm)
        .join(DimensionTerm, DimensionTerm.id == DimensionAssertion.term_id)
        .filter(DimensionAssertion.matched_alias_id.is_(None))
    )
    scanned_q = (
        db.query(DimensionAssertion)
        .join(DimensionTerm, DimensionTerm.id == DimensionAssertion.term_id)
    )
    if kind:
        q = q.filter(DimensionTerm.kind == kind)
        scanned_q = scanned_q.filter(DimensionTerm.kind == kind)
    if term_codes:
        q = q.filter(DimensionTerm.code.in_(term_codes))
        scanned_q = scanned_q.filter(DimensionTerm.code.in_(term_codes))

    # Counting the whole population, not just the alias-less slice, so the
    # report can state what it left alone as well as what it found.
    report.scanned = scanned_q.count()

    for assertion, term in q.all():
        if not assertion.raw_value:
            report.unjudgeable += 1
            continue
        alias = resolve_raw(db, term.kind, assertion.raw_value,
                            team_id=assertion.team_id)
        if alias is not None and alias.term_id == term.id:
            report.alias_less_but_sound += 1
            continue
        report.orphans.append(OrphanRow(
            assertion_id=assertion.id, kind=term.kind, term_code=term.code,
            subject_type=assertion.subject_type, subject_code=assertion.subject_code,
            raw_value=assertion.raw_value, source=assertion.source,
        ))
    return report


def repair(db: Session, *, kind: str | None = None,
           term_codes: list[str] | None = None, apply: bool = False) -> RepairReport:
    """Report the orphans, and delete them only when explicitly asked.

    The caller commits or rolls back — the dry path is the real path with the
    transaction thrown away, so it genuinely rehearses the write.
    """
    report = find_orphans(db, kind=kind, term_codes=term_codes)
    report.dry_run = not apply
    if report.orphans:
        ids = [o.assertion_id for o in report.orphans]
        # Chunked: a single IN list of a few thousand ids is fine, but this
        # scales past that without a surprise.
        for i in range(0, len(ids), 500):
            (db.query(DimensionAssertion)
             .filter(DimensionAssertion.id.in_(ids[i:i + 500]))
             .delete(synchronize_session=False))
        report.deleted = len(ids)
        db.flush()
    return report
