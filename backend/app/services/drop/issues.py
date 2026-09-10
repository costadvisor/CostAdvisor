"""The delivered defect register (`tables/_issues.csv`), SCRUM-74 unit 1.

This file is **carried through, never recomputed**. It is the data team's
own list of what is wrong with the drop — 1,390 rows over 37 problem
templates — and re-deriving it in loader code would produce a second,
drifting opinion of the same facts. SCRUM-34's whole premise is that these
findings become queryable rather than re-discovered.

Three shapes to know about:

* `key` is **polymorphic**. It is a combo_id for `combos` rows, a
  `{combo_id}#{seq}` for `combo_lines`, a commodity_key, a `{series}|{region}`
  feed key, a type_code, or a formula_id — and for 8 rows it is a bare region
  name (`EU`, `NA`, …). A loader that FKs `key` at one table drops or errors
  on those 8.
* Most rows are **not** load-blocking. Roughly half are "awaiting a decision"
  rows that map onto the two empty forms in `decisions/`, and a further slice
  is pure provenance ("formula says X, combo says Y — used combo": the
  resolution is already applied).
* `tables/_manifest.json` carries an `issue_summary` that mirrors this file
  exactly, which gives the loader a free integrity check on its own reading.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.services.drop.reader import read_csv_rows, read_json, table_path

# Rows whose `problem` contains this are waiting on a human filling in one of
# the two forms in decisions/ — they are blocking only if the corresponding
# column is declared NOT NULL, which is exactly why the plan loads the basis
# columns nullable.
_AWAITING_DECISION = "fill the"

# Substrings that mark a genuine NOT NULL / FK failure rather than a note.
_BLOCKING_MARKERS = (
    "-> no_series",
    "no cost lines",
    "no family at combo or formula level",
    "somebody has to pick one",
)


@dataclass(frozen=True)
class DropIssue:
    table: str
    key: str
    column: str
    problem: str

    @property
    def awaiting_decision(self) -> bool:
        return _AWAITING_DECISION in self.problem

    @property
    def blocking(self) -> bool:
        """A hard load failure if ignored — as opposed to a deferred decision
        or a provenance note."""
        if self.awaiting_decision:
            return False
        return any(marker in self.problem for marker in _BLOCKING_MARKERS)


def load_issues() -> list[DropIssue]:
    rows = read_csv_rows(table_path("_issues.csv"))
    return [
        DropIssue(
            table=row.get("table", ""),
            key=row.get("key", ""),
            column=row.get("column", ""),
            problem=row.get("problem", ""),
        )
        for row in rows
    ]


def issues_by_table(issues: list[DropIssue] | None = None) -> dict[str, list[DropIssue]]:
    issues = load_issues() if issues is None else issues
    out: dict[str, list[DropIssue]] = {}
    for issue in issues:
        out.setdefault(issue.table, []).append(issue)
    return out


def issues_for(table: str, key: str, issues: list[DropIssue] | None = None) -> list[DropIssue]:
    """Every delivered finding against one row. This is what a loader attaches
    to the record it writes, so the defect travels with the data."""
    issues = load_issues() if issues is None else issues
    return [i for i in issues if i.table == table and i.key == key]


def observed_summary(issues: list[DropIssue] | None = None) -> dict[str, int]:
    """`{"table.column": count}` as counted from the file itself."""
    issues = load_issues() if issues is None else issues
    return dict(Counter(f"{i.table}.{i.column}" for i in issues))


def declared_summary() -> dict[str, int]:
    """`issue_summary` as shipped in tables/_manifest.json."""
    manifest = read_json(table_path("_manifest.json"))
    return dict(manifest.get("issue_summary") or {})


def verify_issue_summary() -> dict:
    """Cross-check our reading of `_issues.csv` against the manifest's own
    count of it.

    A mismatch means the reader is losing or inventing rows — the cheapest
    possible detector for an encoding or quoting bug, since it needs no
    knowledge of what the rows mean. Returns a report rather than raising so
    a loader can decide whether a drift is fatal.
    """
    observed = observed_summary()
    declared = declared_summary()
    keys = set(observed) | set(declared)
    mismatches = {
        k: {"declared": declared.get(k, 0), "observed": observed.get(k, 0)}
        for k in sorted(keys)
        if declared.get(k, 0) != observed.get(k, 0)
    }
    return {
        "matches": not mismatches,
        "declared_total": sum(declared.values()),
        "observed_total": sum(observed.values()),
        "mismatches": mismatches,
    }
