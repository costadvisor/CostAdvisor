"""Taxonomy reconciliation decision payload (Phase 2 item 1, post-Wave-3
roadmap follow-up).

The 2026-07 drop's `families.csv` names 23 chemical families and 131
(family, subfamily) pairs. Comparing them against the live platform taxonomy
(`chemical_families`/`subfamilies`, `team_id IS NULL`) by exact name turns up
real mismatches: 9 of the drop's family names have no platform counterpart,
and 78 of its (family, subfamily) pairs don't match either (59 of those
because the family itself is one of the 9; 19 are same-family
subfamily-name mismatches under an otherwise-matched family).

Some of these are genuinely the same taxonomy node renamed; some are formula
families the platform never modeled at all (or vice versa) — a real
business-taxonomy judgement call, not a typo to fuzzy-match, and some DB
families simply have no drop counterpart. So this ships as a second payload
on the Scrum 27b sheet-roundtrip mechanism, following `dimension_decision.py`'s
exact shape and discipline: export the unresolved queue, an analyst fills in
which EXISTING platform family/subfamily a drop name should be treated as
equivalent to, reimport, diff, apply. Applying never creates or renames a
platform row — the analyst creates the target first via the existing
taxonomy CRUD (Scrum 55/68) if it genuinely doesn't exist yet, then aliases
the drop name onto it here. A decision file records a mapping onto ontology
a human already created; it never invents ontology.
"""
import csv
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.chemical_family import ChemicalFamily
from app.models.subfamily import Subfamily
from app.models.taxonomy_reconciliation import TaxonomyReconciliation
from app.services.sheet_roundtrip.base import SheetColumnSpec, SheetPayloadSpec

# The 2026-07 drop's own families.csv — read fresh on every export/diff, never
# cached, so the queue always reflects the live DB against the checked-in
# drop file (same "always diff against a fresh query" discipline as diff.py).
_FAMILIES_CSV = (
    Path(__file__).resolve().parents[4]
    / "sample_idea" / "costadvisor-data" / "tables" / "families.csv"
)


def _read_drop_rows() -> list[dict]:
    with open(_FAMILIES_CSV, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("family") and r.get("subfamily")]


class TaxonomyReconciliationFilter(BaseModel):
    level: str | None = None  # "family" | "subfamily"; None = both
    include_decided: bool = False


def _parse_str(v) -> str:
    s = str(v).strip()
    if not s:
        raise ValueError("must not be empty")
    return s


def _parse_optional_str(v) -> str:
    return str(v or "").strip()


def _passthrough(v):
    return v


class TaxonomyReconciliationSpec(SheetPayloadSpec):
    key = "taxonomy_reconciliation"
    sheet_name = "Taxonomy Reconciliation"
    permission_key = "content.edit"
    filter_schema = TaxonomyReconciliationFilter

    columns = [
        SheetColumnSpec("level", "key", "Level", _parse_str),
        SheetColumnSpec("drop_family", "key", "Drop Family", _parse_str),
        SheetColumnSpec("drop_subfamily", "key", "Drop Subfamily", _parse_optional_str),
        # The two things the human owns. maps_to_family is asked on every row
        # (incl. subfamily-level ones, filled in redundantly) so applying a
        # subfamily-level row's decision never depends on another row's own
        # apply having already landed in this same pass.
        SheetColumnSpec("maps_to_family", "editable", "Maps To Platform Family", _parse_optional_str),
        SheetColumnSpec("maps_to_subfamily", "editable", "Maps To Platform Subfamily", _parse_optional_str),
        SheetColumnSpec("n_formulas", "readonly", "Drop Formula Count", _passthrough),
        SheetColumnSpec("n_combos", "readonly", "Drop Combo Count", _passthrough),
    ]

    def _decided_map(self, db: Session) -> dict:
        return {
            (r.level, r.drop_family, r.drop_subfamily): r
            for r in db.query(TaxonomyReconciliation).all()
        }

    def query_rows(self, db: Session, filter_spec: TaxonomyReconciliationFilter) -> list[dict]:
        drop_rows = _read_drop_rows()

        db_families = {
            f.name: f for f in db.query(ChemicalFamily).filter(ChemicalFamily.team_id.is_(None)).all()
        }
        fam_by_id = {f.id: f.name for f in db_families.values()}
        db_pairs = set()
        for s in db.query(Subfamily).filter(Subfamily.team_id.is_(None)).all():
            fname = fam_by_id.get(s.family_id)
            if fname:
                db_pairs.add((fname, s.name))

        decided = self._decided_map(db)

        # Family-level candidates: drop family names absent from the platform,
        # deduped, tallied by their own real n_formulas/n_combos (from the
        # drop's own data — never fabricated impact).
        fam_impact: dict[str, dict] = {}
        for r in drop_rows:
            fam = r["family"]
            agg = fam_impact.setdefault(fam, {"n_formulas": 0, "n_combos": 0})
            agg["n_formulas"] += int(r.get("n_formulas") or 0)
            agg["n_combos"] += int(r.get("n_combos") or 0)

        out = []
        if filter_spec.level in (None, "family"):
            for fam, agg in fam_impact.items():
                if fam in db_families:
                    continue
                key = ("family", fam, "")
                if key in decided and not filter_spec.include_decided:
                    continue
                dec = decided.get(key)
                out.append({
                    "level": "family",
                    "drop_family": fam,
                    "drop_subfamily": "",
                    "maps_to_family": dec.family.name if dec and dec.family else "",
                    "maps_to_subfamily": "",
                    "n_formulas": agg["n_formulas"],
                    "n_combos": agg["n_combos"],
                })

        if filter_spec.level in (None, "subfamily"):
            for r in drop_rows:
                fam, sub = r["family"], r["subfamily"]
                if (fam, sub) in db_pairs:
                    continue
                key = ("subfamily", fam, sub)
                if key in decided and not filter_spec.include_decided:
                    continue
                dec = decided.get(key)
                out.append({
                    "level": "subfamily",
                    "drop_family": fam,
                    "drop_subfamily": sub,
                    "maps_to_family": dec.family.name if dec and dec.family else "",
                    "maps_to_subfamily": dec.subfamily.name if dec and dec.subfamily else "",
                    "n_formulas": int(r.get("n_formulas") or 0),
                    "n_combos": int(r.get("n_combos") or 0),
                })

        # Ranked by real impact, so an analyst can work the top of the queue —
        # same triage principle as dimension_decision's occurrence ranking.
        out.sort(key=lambda r: (-r["n_combos"], r["level"], r["drop_family"], r["drop_subfamily"]))
        return out

    def _get_or_create(self, db: Session, row_key: dict) -> TaxonomyReconciliation:
        level = row_key["level"]
        drop_family = row_key["drop_family"]
        drop_subfamily = row_key.get("drop_subfamily") or ""
        row = (
            db.query(TaxonomyReconciliation)
            .filter(TaxonomyReconciliation.level == level,
                    TaxonomyReconciliation.drop_family == drop_family,
                    TaxonomyReconciliation.drop_subfamily == drop_subfamily)
            .first()
        )
        if row is None:
            row = TaxonomyReconciliation(level=level, drop_family=drop_family, drop_subfamily=drop_subfamily)
            db.add(row)
            db.flush()
        return row

    def apply_change(self, db: Session, row_key: dict, column: str, value) -> None:
        if column == "maps_to_family":
            name = str(value or "").strip()
            if not name:
                raise ValueError("blank decision — nothing to apply")
            family = (
                db.query(ChemicalFamily)
                .filter(ChemicalFamily.name == name, ChemicalFamily.team_id.is_(None))
                .first()
            )
            if family is None:
                raise ValueError(
                    f"no platform chemical family {name!r} — create it before mapping onto it"
                )
            row = self._get_or_create(db, row_key)
            row.family_id = family.id
            db.flush()
            return

        if column == "maps_to_subfamily":
            if row_key.get("level") != "subfamily":
                raise ValueError("maps_to_subfamily only applies to subfamily-level rows")
            name = str(value or "").strip()
            if not name:
                raise ValueError("blank decision — nothing to apply")
            row = self._get_or_create(db, row_key)
            if row.family_id is None:
                raise ValueError(
                    "maps_to_family must be decided (this run or a previous one) "
                    "before maps_to_subfamily"
                )
            subfamily = (
                db.query(Subfamily)
                .filter(Subfamily.name == name, Subfamily.family_id == row.family_id,
                        Subfamily.team_id.is_(None))
                .first()
            )
            if subfamily is None:
                raise ValueError(
                    f"no platform subfamily {name!r} under the decided family — "
                    "create it before mapping onto it"
                )
            row.subfamily_id = subfamily.id
            db.flush()
            return

        raise ValueError(f"{column} is not editable")

    def get_current_value(self, db: Session, row_key: dict, column: str):
        if column not in ("maps_to_family", "maps_to_subfamily"):
            return None
        level = row_key["level"]
        drop_family = row_key["drop_family"]
        drop_subfamily = row_key.get("drop_subfamily") or ""
        row = (
            db.query(TaxonomyReconciliation)
            .filter(TaxonomyReconciliation.level == level,
                    TaxonomyReconciliation.drop_family == drop_family,
                    TaxonomyReconciliation.drop_subfamily == drop_subfamily)
            .first()
        )
        if row is None:
            return ""
        if column == "maps_to_family":
            return row.family.name if row.family else ""
        return row.subfamily.name if row.subfamily else ""
