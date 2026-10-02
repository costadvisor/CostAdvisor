"""Mapping pass: derive commodity_indexes.family_id/subfamily_id from real
recipe usage (Scrum 17 follow-up).

Scope: only CommodityIndex rows with retrieval_status IS NOT NULL — the
seeded reference-catalog commodities (per seed_index_metadata.py's own
convention: "seeded reference commodities are identifiable by
retrieval_status IS NOT NULL"). Custom/team-created commodities and legacy
FX-adjacent rows are left untouched; they were never part of this taxonomy.

For each in-scope commodity, walk FormulaTemplateComponent.commodity_id ->
template_id -> FormulaTemplate.family_id/subfamily_id and take the most
common (family_id, subfamily_id) pair across all its recipe usages. A
commodity with zero recipe usage gets family_id/subfamily_id left NULL —
never fabricated. subfamily_id is only ever set alongside a family_id it
actually belongs under (never a subfamily orphaned from its own family).
"""
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.index_data import CommodityIndex
from app.models.formula_template import FormulaTemplateComponent, FormulaTemplate


@dataclass
class FamilyMappingReport:
    considered: int = 0
    mapped: int = 0        # got a family_id (± subfamily_id) this run
    unchanged: int = 0     # already had this exact family_id/subfamily_id
    no_usage: int = 0      # zero FormulaTemplateComponent references
    examples: list = field(default_factory=list)  # a few (name, family, subfamily) for the report

    def render(self) -> str:
        lines = [
            f"Considered: {self.considered}",
            f"Mapped (family set/changed): {self.mapped}",
            f"Unchanged (already correct): {self.unchanged}",
            f"No usage (left NULL): {self.no_usage}",
        ]
        if self.examples:
            lines.append("\nSample mappings:")
            for name, fam, sub in self.examples[:15]:
                lines.append(f"  {name}: family={fam or '—'} subfamily={sub or '—'}")
        return "\n".join(lines)


def map_index_families(db: Session, apply: bool = False) -> FamilyMappingReport:
    report = FamilyMappingReport()

    commodities = (
        db.query(CommodityIndex)
        .filter(CommodityIndex.retrieval_status.isnot(None))
        .all()
    )
    report.considered = len(commodities)

    for ci in commodities:
        rows = (
            db.query(FormulaTemplate.family_id, FormulaTemplate.subfamily_id)
            .join(FormulaTemplateComponent, FormulaTemplateComponent.template_id == FormulaTemplate.id)
            .filter(FormulaTemplateComponent.commodity_id == ci.id)
            .all()
        )
        if not rows:
            report.no_usage += 1
            continue

        family_counts = Counter(r[0] for r in rows if r[0] is not None)
        if not family_counts:
            report.no_usage += 1
            continue
        best_family_id, _ = family_counts.most_common(1)[0]

        # Subfamily only counted among rows that agree with the winning family,
        # so a subfamily can never be recorded under the wrong family.
        subfamily_counts = Counter(r[1] for r in rows if r[0] == best_family_id and r[1] is not None)
        best_subfamily_id = subfamily_counts.most_common(1)[0][0] if subfamily_counts else None

        if ci.family_id == best_family_id and ci.subfamily_id == best_subfamily_id:
            report.unchanged += 1
            continue

        report.mapped += 1
        report.examples.append((ci.name, best_family_id, best_subfamily_id))
        if apply:
            ci.family_id = best_family_id
            ci.subfamily_id = best_subfamily_id

    return report
