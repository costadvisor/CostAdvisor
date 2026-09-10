"""Catalog retarget — combos and recipes from the 2026-07 drop (SCRUM-74/3b).

Replaces the recipe data that came from the older 257-formula drop:

    combos.csv       1079 -> formula_region_coverage
    combo_lines.csv  5747 -> formula_template_components  (+ type_code_id)

**Scoped per template, not globally.** The drop covers 340 of the 447 platform
templates; it says nothing about the other 107, which still carry recipes from
the previous drop. So coverage and lines are replaced only for templates the
drop actually mentions, and everything else is left exactly as it is. A
global truncate-and-reload would delete 89 coverage rows and 492 lines the
drop never claimed to own — deleting on silence.

**Coverage is upserted, lines are replaced.** A coverage row carries human
review state (`needs_review`, `reviewed_by`, `reviewed_at`, `provenance`), and
an expert sign-off must survive a re-seed — the same guarantee the previous
seeder gave. The recipe lines themselves have no such state, so for a given
(template, region) they are deleted and rewritten as a block, which is the
only way a line the drop has removed actually disappears.

Two facts the source states twice, resolved once in services/drop/authority.py
and consumed here: margin comes from the line (the header disagrees on 146
combos, and the line is right because the weights close at exactly 100), and
proxy status keeps both readings rather than picking a winner.
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.formula_template import (
    FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_layer import TypeCode
from app.services.drop.authority import resolve_margin
from app.services.drop.normalize import is_blank, is_fixed_line
from app.services.drop.reader import read_table
from app.services.drop.report import LoadReport, TableDiff

# The drop's region codes onto ours.
#
# `decisions/region_basis.csv` has a `db_region` column intended to declare
# exactly this, and it is blank for all 8 regions — so the mapping is formally
# undecided. This is the mapping the previous seeder already established and
# the one the existing coverage rows use, so adopting it keeps the two
# populations comparable instead of splitting the catalog across two region
# vocabularies. When the form is filled in, its `db_region` overrides this.
REGION_MAP = {
    "EU": "Europe",
    "NA": "NA",
    "CN": "China",
    "IN": "India",
    "APAC": "APAC",
    "MEA": "MEA",
    "LA": "Latam",
    # The drop's own analysis calls this "a one-line fix, GLOBAL already
    # exists". GLOBAL does exist in our regions table — but note the drop
    # itself never uses the string, so this is a mapping decision, not a typo
    # correction.
    "GL": "GLOBAL",
}

_UNSET = object()


def _apply(obj, field_name: str, value, changes: list) -> None:
    """Set a field only when it differs. Decimal-aware, so an unchanged
    numeric column does not read as an update on every run."""
    current = getattr(obj, field_name, _UNSET)
    if current is _UNSET:
        return
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and current is not None
        and not isinstance(current, bool)
    ):
        try:
            if float(current) == float(value):
                return
        except (TypeError, ValueError):
            pass
    elif current == value:
        return
    setattr(obj, field_name, value)
    changes.append(field_name)


def _clean(value):
    return None if is_blank(value) else value


def _lines_by_combo() -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for line in read_table("combo_lines"):
        grouped[line["combo_id"]].append(line)
    for lines in grouped.values():
        lines.sort(key=lambda l: l["seq"] or 0)
    return grouped


def load_catalog(db: Session) -> LoadReport:
    """Load combos and recipes for every template the drop covers.

    Never commits — the caller owns the transaction, and a dry run is this
    call followed by a rollback.
    """
    report = LoadReport(title="Catalog retarget")
    cov_diff = TableDiff("formula_region_coverage")
    line_diff = TableDiff("formula_template_components")

    templates = {
        t.code: t
        for t in db.query(FormulaTemplate).filter(
            FormulaTemplate.team_id.is_(None), FormulaTemplate.code.isnot(None)
        )
    }
    type_codes = {tc.code: tc for tc in db.query(TypeCode)}
    lines_by_combo = _lines_by_combo()

    combos = read_table("combos")
    # Which (template, region) pairs the drop actually speaks to — anything
    # outside this set is left untouched rather than treated as removed.
    touched: set[tuple[uuid.UUID, str, str]] = set()

    existing_cov = {
        (c.template_id, c.region, c.variant or ""): c
        for c in db.query(FormulaRegionCoverage)
    }

    for combo in combos:
        combo_id = combo["combo_id"]
        formula_id = combo["formula_id"]
        template = templates.get(formula_id)
        if template is None:
            # The 17 `flat`-shaped records carry their region inside the
            # formula_id itself and have no template. Reported, not invented.
            cov_diff.skipped.append(
                (combo_id, f"no platform template with code {formula_id!r}")
            )
            continue

        raw_region = combo["region"]
        region = REGION_MAP.get(raw_region)
        if region is None:
            cov_diff.skipped.append((combo_id, f"region {raw_region!r} has no mapping"))
            continue

        variant = combo.get("variant") or ""
        lines = lines_by_combo.get(combo_id, [])
        margin = resolve_margin(combo, lines)

        fields = {
            # Margin from the LINE, not the header — see authority.py. The
            # header disagrees on 146 combos and is the stale one.
            "margin_pct": margin.margin_pct,
            # The drop's proxy-density tier, kept apart from the shipped
            # worst-retrieval-tier `coverage_tier`.
            "proxy_density_tier": _clean(combo.get("coverage_tier")),
        }
        # The basis columns come from `decisions/region_basis.csv`, which is
        # blank for every region — the form is not filled in yet. So they are
        # written ONLY where the drop actually states something: writing the
        # blank would null out values already in the database (a hand-set
        # currency on a priced combo, say) on the strength of a source that
        # has not been filled in. Absent is not the same as empty.
        for column, raw in (
            ("currency", combo.get("basis_currency")),
        ):
            if not is_blank(raw):
                fields[column] = raw

        key = (template.id, region, variant)
        touched.add(key)
        coverage = existing_cov.get(key)
        if coverage is None:
            coverage = FormulaRegionCoverage(
                template_id=template.id, region=region, variant=variant, **fields
            )
            db.add(coverage)
            existing_cov[key] = coverage
            cov_diff.created += 1
        else:
            changes: list = []
            for name, value in fields.items():
                _apply(coverage, name, value, changes)
            # Review state is deliberately NOT in `fields`: needs_review,
            # reviewed_by, reviewed_at and provenance belong to whoever signed
            # the combo off, and a re-seed must not clobber that.
            cov_diff.updated += 1 if changes else 0
            cov_diff.unchanged += 0 if changes else 1

        _load_lines(db, template, region, variant, lines, type_codes, line_diff)

    db.flush()

    drop_template_ids = {
        templates[c["formula_id"]].id for c in combos if c["formula_id"] in templates
    }

    # Coverage on drop-covered templates that the drop no longer mentions.
    # Reported as stale and left in place — the drop is authoritative for what
    # it covers, not for what it omits, and a coverage row may carry a human
    # sign-off worth more than tidiness.
    for key, _coverage in existing_cov.items():
        template_id, _region, _variant = key
        if template_id in drop_template_ids and key not in touched:
            cov_diff.stale += 1

    # Recipe LINES are different: for a template the drop covers, its
    # region-tagged line sets should be exactly what the drop says, so a set
    # the drop no longer mentions is removed rather than left to shadow the
    # real one. (Without this a (template, region, variant) key that changes
    # shape — as happened when the variant dimension was introduced — leaves
    # the previous set orphaned and invisible to the loader forever.)
    #
    # Region-NULL lines are never touched: those are the template-level set the
    # API authors, which the drop knows nothing about.
    orphans = (
        db.query(FormulaTemplateComponent)
        .filter(
            FormulaTemplateComponent.template_id.in_(drop_template_ids),
            FormulaTemplateComponent.region.isnot(None),
        )
        .all()
    )
    for row in orphans:
        if (row.template_id, row.region, row.variant or "") not in touched:
            db.delete(row)
            line_diff.deleted += 1
    db.flush()

    report.tables.append(cov_diff)
    report.tables.append(line_diff)
    return report


def _load_lines(
    db: Session,
    template: FormulaTemplate,
    region: str,
    variant: str,
    lines: list[dict],
    type_codes: dict[str, TypeCode],
    diff: TableDiff,
) -> None:
    """Replace this (template, region, variant) line set as a block.

    Variant is part of the key because the two variants of a formula are
    different recipes — keyed on (template, region) alone they overwrite each
    other on every run and one is silently lost.

    Replaced rather than upserted because a line the drop has dropped must
    actually disappear — and unlike coverage, a line carries no human state
    worth preserving. Compared before writing so an unchanged recipe still
    reports as unchanged and idempotency holds.
    """
    desired = []
    for line in lines:
        raw_code = line["type_code"]
        fixed = is_fixed_line(raw_code)
        tc = None if fixed else type_codes.get(raw_code)

        if not fixed and tc is None:
            diff.skipped.append(
                (f"{line['combo_id']}#{line['seq']}", f"unknown type code {raw_code!r}")
            )
            continue

        line_proxy = None if fixed else (_clean(line.get("proxy_status")))
        desired.append({
            # `name` is String(64); the source's labels run longer.
            "name": (line.get("label") or line.get("label_short") or raw_code or "line")[:64],
            # The source's `kind` is indexed|fixed; margin is a fixed line.
            "component_type": "index" if line["kind"] == "indexed" else "fixed",
            # Resolved through the type code rather than the line's own
            # `commodity_key`, which is blank on every unpriceable line even
            # when the code names a real series. NULL only for `ambiguous`.
            "commodity_id": tc.resolves_to_id if tc else None,
            "type_code_id": tc.id if tc else None,
            "region": region,
            "variant": variant,
            "weight_pct": line["weight_pct"],
            # The boolean stays for existing readers; it necessarily folds
            # `unclassified` into False.
            "is_proxy": line_proxy == "proxy",
            # The line's own reading, kept beside the registry's.
            "line_proxy_status": line_proxy,
            "sort_order": line["seq"] or 0,
        })

    current = (
        db.query(FormulaTemplateComponent)
        .filter(
            FormulaTemplateComponent.template_id == template.id,
            FormulaTemplateComponent.region == region,
            FormulaTemplateComponent.variant == variant,
        )
        .order_by(FormulaTemplateComponent.sort_order)
        .all()
    )

    if _same_recipe(current, desired):
        diff.unchanged += len(current)
        return

    for row in current:
        db.delete(row)
        diff.deleted += 1
    for spec in desired:
        db.add(FormulaTemplateComponent(template_id=template.id, **spec))
        diff.created += 1


def _same_recipe(current: list[FormulaTemplateComponent], desired: list[dict]) -> bool:
    """Field-by-field comparison, so an unchanged recipe is not rewritten and
    the report can honestly say nothing moved."""
    if len(current) != len(desired):
        return False
    for row, spec in zip(current, desired):
        if row.name != spec["name"]:
            return False
        if row.component_type != spec["component_type"]:
            return False
        if row.commodity_id != spec["commodity_id"]:
            return False
        if row.type_code_id != spec["type_code_id"]:
            return False
        if row.line_proxy_status != spec["line_proxy_status"]:
            return False
        if (row.variant or "") != (spec["variant"] or ""):
            return False
        if bool(row.is_proxy) != bool(spec["is_proxy"]):
            return False
        if (row.sort_order or 0) != (spec["sort_order"] or 0):
            return False
        if spec["weight_pct"] is None or row.weight_pct is None:
            if spec["weight_pct"] is not row.weight_pct:
                return False
        elif abs(float(row.weight_pct) - float(spec["weight_pct"])) > 1e-6:
            return False
    return True
