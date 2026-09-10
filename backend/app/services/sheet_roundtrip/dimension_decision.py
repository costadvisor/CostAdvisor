"""Dimension-decision payload spec (Wave 3, SCRUM-77 / INT-3).

**The analyst-decided parts of the dimension layer live in a reviewable file,
and re-importing that file is what changes the DB — not a branch inside a
loader.** Three of the four facets need a human:

* **industry** — `INDUSTRY_RULES.json` serialised all 19 regexes to `{}`, and
  the mockup holding the originals is not in this repo, so 178 of 204 raw
  strings have no mechanical path to a term.
* **compliance_flag** — 239 near-unique raw labels, and the two sources
  disagree on membership for a third of shared keys. Which label is a real
  regulation, and which source wins, are domain calls.
* **functionality_family** — a second naming scheme with zero overlap with the
  taxonomy. Whether the two crosswalk, and how, is a judgement call.

Rather than inventing a bespoke file format, this registers as a second payload
on the sheet-roundtrip mechanism shipped in Scrum 27b: export the unresolved
queue as a styled `.xlsx`, fill in the term column offline, reimport, read the
diff, apply. The mechanism's guarantees come for free — rows re-key by business
key so a reordered sheet still matches, an import only ever computes a diff, an
edit to a key or read-only column is *reported* rather than silently absorbed,
and applying re-verifies the live value first.

The exported row is one **unresolved raw value**; the editable column is the
term code it should map to. Applying a row creates the alias, which is what
makes the next load resolve it — so the analyst's decision is expressed as
data, and the loader keeps no branches.
"""
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.dimension import (
    DIMENSION_KINDS, DimensionAlias, DimensionTerm, UnresolvedValue,
    normalize_value,
)
from app.services.sheet_roundtrip.base import SheetColumnSpec, SheetPayloadSpec


class DimensionDecisionFilter(BaseModel):
    kind: str | None = None
    # Only values that blocked at least this many source assertions — the queue
    # is ranked by occurrence precisely so an analyst can work the top of it.
    min_occurrences: int | None = None


def _parse_str(v) -> str:
    s = str(v).strip()
    if not s:
        raise ValueError("must not be empty")
    return s


def _parse_term_code(v) -> str:
    """The decision: which term this raw value means.

    Blank is allowed and means "still undecided" — an analyst working a
    200-row queue must be able to return a partially filled sheet without every
    empty cell reading as an error.
    """
    return str(v or "").strip()


def _passthrough(v):
    return v


class DimensionDecisionSpec(SheetPayloadSpec):
    key = "dimension_decision"
    sheet_name = "Dimension Decisions"
    permission_key = "dimensions.edit"
    filter_schema = DimensionDecisionFilter

    columns = [
        SheetColumnSpec("kind", "key", "Facet", _parse_str),
        SheetColumnSpec("raw_value", "key", "Raw Value", _parse_str),
        # The one thing the human owns.
        SheetColumnSpec("term_code", "editable", "Maps To Term Code", _parse_term_code),
        SheetColumnSpec("occurrences", "readonly", "Blocked Assertions", _passthrough),
        SheetColumnSpec("sample_subjects", "readonly", "Examples", _passthrough),
        SheetColumnSpec("reason", "readonly", "Why Unresolved", _passthrough),
    ]

    def query_rows(self, db: Session, filter_spec: DimensionDecisionFilter) -> list[dict]:
        q = db.query(UnresolvedValue)
        if filter_spec.kind:
            if filter_spec.kind not in DIMENSION_KINDS:
                raise ValueError(f"Unknown kind {filter_spec.kind!r}")
            q = q.filter(UnresolvedValue.kind == filter_spec.kind)
        if filter_spec.min_occurrences is not None:
            q = q.filter(UnresolvedValue.occurrences >= filter_spec.min_occurrences)

        rows = []
        for u in q.order_by(UnresolvedValue.occurrences.desc(),
                            UnresolvedValue.raw_value).all():
            rows.append({
                "kind": u.kind,
                "raw_value": u.raw_value,
                # Already-decided values are not in this register, so the
                # editable column always exports blank — the diff is the
                # analyst's answer, nothing else.
                "term_code": "",
                "occurrences": u.occurrences,
                "sample_subjects": ", ".join(u.sample_subjects or []),
                "reason": u.reason,
            })
        return rows

    def apply_change(self, db: Session, row_key: dict, column: str, value) -> None:
        """Create the alias the decision names.

        Applying does not delete the register row — the next load clears the
        whole register and rebuilds it, so a value that now resolves simply
        stops reappearing. Deleting here would make the apply step and the load
        two places that own the queue.
        """
        if column != "term_code":
            raise ValueError(f"{column} is not editable")
        code = str(value or "").strip()
        if not code:
            raise ValueError("blank decision — nothing to apply")

        kind = row_key["kind"]
        term = (
            db.query(DimensionTerm)
            .filter(DimensionTerm.kind == kind, DimensionTerm.code == code,
                    DimensionTerm.team_id.is_(None))
            .first()
        )
        if term is None:
            raise ValueError(
                f"no platform term {code!r} in facet {kind!r} — create the term "
                "before mapping a raw value onto it"
            )

        normalized = normalize_value(row_key["raw_value"])
        alias = (
            db.query(DimensionAlias)
            .filter(DimensionAlias.kind == kind,
                    DimensionAlias.normalized == normalized,
                    DimensionAlias.team_id.is_(None))
            .first()
        )
        if alias is None:
            db.add(DimensionAlias(
                team_id=None, term_id=term.id, kind=kind,
                raw_value=row_key["raw_value"], normalized=normalized,
                source="decision_file",
            ))
        else:
            # Re-pointing in place: a corrected decision must not leave two
            # aliases for one string, or the same value resolves two ways.
            alias.term_id = term.id
            alias.source = "decision_file"

    def get_current_value(self, db: Session, row_key: dict, column: str):
        """The live decision for this raw value, or "" when still undecided.

        Read back as the term code so the concurrency check compares like with
        like: `query_rows` exports "" for an undecided row, and an alias created
        by somebody else's import since the diff was computed shows up here as
        that term's code, which is exactly the stale-row case the mechanism
        exists to catch.
        """
        if column != "term_code":
            return None
        alias = (
            db.query(DimensionAlias)
            .join(DimensionTerm, DimensionTerm.id == DimensionAlias.term_id)
            .filter(DimensionAlias.kind == row_key["kind"],
                    DimensionAlias.normalized == normalize_value(row_key["raw_value"]),
                    DimensionAlias.team_id.is_(None))
            .first()
        )
        return alias.term.code if alias else ""
