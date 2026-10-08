"""Catalogue loader: the formula catalogue of the content drop (design §2.3, §3.2).

Loads, as platform rows (`team_id` NULL), into the tables the existing engine
(`app/services/intelligence.py`) already reads:

    formula_templates            one per card: every FORMULA_COMBOS record,
                                 every AUTO_GROUPS group and every
                                 CURATED_CONTENT key with neither (a card-only
                                 key), except the `GRP-*` shells
    formula_region_coverage      one per combo (record and group combos)
    formula_template_components  one per cost line, region-tagged
    formula_region_coverage.trust_*   the existing trust grade (services/trust)
    category_placements.template_id   back-filled by code

**Kind and status.** `card_kind`, `supply_status`, `supply_status_detail` and
`redirect_to` come from `status.classify` (the decision table). Which cards
are listed is decided at read time from these columns
(`app/services/catalog_visibility.py`).

**What a template carries.** `code` = PID, `name`, `full_name` (the record's
`full`), `family_id` (the record's family), `product_line_id` (the record's
`family|||subfamily` key, which names the product line, resolved through
`product_lines.line_key` then a former key), `form` / `volatile` (stated per
combo, the same across a record's combos), `cas_number` from CAS_LOOKUP (the
literal `mixture` is a legal value), `reference_grade` and `archival_note`
from CURATED_CONTENT. A record key that is no current line leaves
`product_line_id` NULL ("product line not yet published"); the key is kept in
`internal_meta.record_line_key`, never served. A card-only key carries no
name in the drop, so its name is its PID; it has no family, line or combos.

**`catalog_meta` holds structure only** (it reaches every team through
`/api/formulas`): `source`, `regions`, `region_count`, `combos` (each combo's
repaired id, region, variant, and where its margin came from), the
VARIANT_OVERRIDES entry, `absorbed_via`, `pricing_gap` {status, line, since}
and `margin_status`. **`internal_meta`** holds the prose and provenance:
`weights_rationale`, `line_moved`, the pricing gap's `why`, `route_note`,
`reprice_note`, `line_only_note`, `record_line_key`, `grade_source`,
`refresh`, `flags_review` and the combo routes (`combo_routes`, combo id →
route text). No API schema maps `internal_meta`.

**Groups.** An AUTO_GROUPS card stands for products priced one region each:
its combo ids are its members'. It loads with `is_group`, `group_members` (the
member PIDs, from those combo ids) and one coverage row per combo, and **no
components**: its combos carry only `lines_html`, and weights are never parsed
out of markup. The markup is kept in `catalog_meta.combos[].lines_html`.

**absorbed_into** names the card that represents an absorbed product in the
grid: the group whose combos include it, else the VARIANT_OVERRIDES base whose
`varRows` list it. `catalog_meta.absorbed_via` says which.

**Cost lines.** `[share, label, tag, kind]`: `weight_pct` = share, `name` =
label, `cost_category` = kind. A line is an `index` line iff its tag is in
FCOVERED, resolved through `type_codes` to both `type_code_id` and the series;
every other line (the literal tag `fixed`, which every margin line carries) is
`fixed` with neither. The drop carries no direct-versus-proxy signal, so
`is_proxy` is False and an index line's `line_proxy_status` says
`unclassified`.

**Margin** is the margin line's share; the combo header is kept in
`catalog_meta.combos[].margin_header` when it disagrees, and is the margin
only when the combo has no margin line.

**Replace in place, per (template, region, variant).** Coverage is upserted (a
sign-off survives a reload; `base_price` / `currency` are not in the drop and
are never written over). A line set is compared and rewritten as a block only
when it differs. Reload rules (design §1.4):

* a combo the drop no longer has keeps its coverage row and cost lines (a team
  cost model may track it) and gets `withdrawn_at`; it is cleared if the combo
  comes back;
* a card the drop no longer has keeps its template and gets `card_kind =
  'withdrawn'` (only templates this loader wrote: `catalog_meta.source` is
  one of `SOURCES`);
* a region-tagged line set with no coverage row is deleted; region-NULL lines
  are never touched.

**Trust.** Every loaded coverage row is graded by `trust.assess`; the stored
grade is rewritten only when it moved. A record that flags a `pricing_gap` or
a `margin_status` has every combo capped at `medium` and put in front of a
reviewer (`needs_review`, unless signed off), with a fixed reason. A sign-off
whose fingerprint no longer matches the recipe (or was never recorded) is
stale, the rule `trust.apply_assessment` applies.

`check_engine(db)` runs `intelligence.derive` over the loaded catalogue and
counts what is evaluable. It is not part of `load` (several queries per
combo); the tests use it.

Idempotent: a second run reports zero changes. **Never commits**: the CLI
(`seed_content_drop.py`) owns the transaction.
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.constants.trust import TRUST_GRADES
from app.models.chemical_family import ChemicalFamily
from app.models.formula_template import (
    FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_layer import TypeCode
from app.models.product_line import ProductLine
from app.models.taxonomy_v2 import CategoryPlacement
from app.services.drop.common import _apply
from app.services.drop.report import LoadReport
from app.services.content_drop import status as card_status
from app.services.content_drop.reader import (
    app_region, combo_pid, loader_user_id, make_line_key, raw, repair_combo_id,
)
from app.services.content_drop.taxonomy import _diff, _upsert, line_index
from app.services.trust import (
    REASON_MARGIN_STATUS, REASON_PRICING_GAP, SOURCE_WARNING_DETAIL, TrustReason,
    apply_assessment, assess, cap_for_source_warnings, coverage_lines, fingerprint_for,
)
# Side-effect import: the region auto-register listener. APAC and MEA may not
# exist in `regions` yet; without it the first flush of a coverage row in
# either raises a raw FK violation instead of registering the code.
from app.services import regions as _region_events  # noqa: F401

# Every combo is anchored at the drop's index base: 100 = January 2023.
BASE_YEAR = 2023
BASE_QUARTER = 1

MARGIN_KIND = "margin"
# The drop carries no direct/proxy reading on its cost lines.
LINE_PROXY_UNCLASSIFIED = "unclassified"

SOURCE_RECORD = "FORMULA_COMBOS"
SOURCE_GROUP = "AUTO_GROUPS"
SOURCE_CARD = "CURATED_CONTENT"
SOURCES = (SOURCE_RECORD, SOURCE_GROUP, SOURCE_CARD)
ABSORBED_VIA_GROUP = "AUTO_GROUPS"
ABSORBED_VIA_VARIANT = "VARIANT_OVERRIDES"

# The top-level keys `catalog_meta` may hold (design §2.3). Anything else a
# record says goes to `internal_meta`.
CATALOG_META_KEYS = (
    "source", "regions", "region_count", "coverage_tier", "data_confidence", "combos",
    "variant_overrides", "absorbed_via", "pricing_gap", "margin_status",
)
# The parts of `pricing_gap` that are structure; its `why` is internal.
PRICING_GAP_KEYS = ("status", "line", "since")

# The trust reasons a record-level warning adds, with fixed wording (the
# record's own `why` is internal and never copied). They live in
# services/trust.py, whose assess() applies the same cap from the stored
# catalog_meta, so a regrade outside the loader keeps it; re-exported here.
_CAP_DETAIL = SOURCE_WARNING_DETAIL

TRUST_ROW = "formula_region_coverage.trust"
# One report row per grade, `unchanged` = the combos at that grade.
TRUST_CHECK_PREFIX = "check: trust grade "
PLACEMENT_ROW = "category_placements.template_id"


# ── What the drop says ───────────────────────────────────────────────────────

def _margin(combo: dict) -> tuple[Any, str]:
    """The combo's margin and where it came from: the margin LINE's share,
    else the header."""
    shares = [line[0] for line in combo.get("lines") or [] if line[3] == MARGIN_KIND]
    if shares:
        return shares[0], "line"
    if combo.get("margin") is not None:
        return combo["margin"], "header"
    return None, "absent"


def _absorbed() -> dict[str, tuple[str, str]]:
    """pid → (the card that represents it in the grid, how that is known),
    for every ABSORBED_FORMULA_IDS entry it can be derived for."""
    member_of = {
        combo_pid(combo["id"]): code
        for code, group in raw("AUTO_GROUPS").items()
        for combo in group.get("combos") or []
    }
    variant_of = {
        row["id"]: base
        for base, override in raw("VARIANT_OVERRIDES").items()
        for row in override.get("varRows") or []
        if row.get("id") and row["id"] != base
    }
    out: dict[str, tuple[str, str]] = {}
    for pid in raw("ABSORBED_FORMULA_IDS"):
        if pid in member_of:
            out[pid] = (member_of[pid], ABSORBED_VIA_GROUP)
        elif pid in variant_of:
            out[pid] = (variant_of[pid], ABSORBED_VIA_VARIANT)
    return out


def _regions(combos: list[dict]) -> list[str]:
    """The drop region codes a card is priced in, in combo order."""
    seen: list[str] = []
    for combo in combos:
        if combo["region"] not in seen:
            seen.append(combo["region"])
    return seen


def _curated(code: str) -> tuple[dict, dict]:
    """(template columns, internal_meta entries) from the card."""
    cc = raw("CURATED_CONTENT").get(code) or {}
    columns = {
        "reference_grade": cc.get("grade") or None,
        "archival_note": cc.get("_archival") or None,
    }
    internal = {name: cc[name] for name in ("grade_source", "refresh", "flags_review")
                if cc.get(name)}
    return columns, internal


def _record_meta(pid: str, rec: dict, absorbed: tuple[str, str] | None
                 ) -> tuple[dict, dict]:
    """(catalog_meta, internal_meta entries) of one FORMULA_COMBOS record."""
    combos = []
    routes: dict[str, str] = {}
    for combo in rec.get("combos") or []:
        combo_id = repair_combo_id(combo["id"])
        entry: dict[str, Any] = {"id": combo_id, "region": combo["region"]}
        if combo.get("variant"):
            entry["variant"] = combo["variant"]
        if combo.get("_route"):
            routes[combo_id] = combo["_route"]
        margin, source = _margin(combo)
        if source != "line":
            entry["margin_source"] = source
        elif combo.get("margin") is not None and combo["margin"] != margin:
            entry["margin_header"] = combo["margin"]
        combos.append(entry)
    regions = _regions(rec.get("combos") or [])
    meta: dict[str, Any] = {
        "source": SOURCE_RECORD,
        "regions": regions,
        "region_count": len(regions),
        "combos": combos,
    }
    override = raw("VARIANT_OVERRIDES").get(pid)
    if override:
        meta["variant_overrides"] = override
    if absorbed:
        meta["absorbed_via"] = absorbed[1]

    internal: dict[str, Any] = {}
    gap = rec.get("pricing_gap")
    if isinstance(gap, dict):
        meta["pricing_gap"] = {k: gap[k] for k in PRICING_GAP_KEYS if k in gap}
        if gap.get("why"):
            internal["pricing_gap"] = {"why": gap["why"]}
    if rec.get("margin_status"):
        meta["margin_status"] = rec["margin_status"]
    for key, name in (("weights_rationale", "weights_rationale"), ("line_moved", "line_moved"),
                      ("_line_only", "line_only_note"), ("_route_note", "route_note"),
                      ("_reprice", "reprice_note")):
        if rec.get(key):
            internal[name] = rec[key]
    if routes:
        internal["combo_routes"] = routes
    return meta, internal


def _group_meta(group: dict, records: dict) -> dict:
    combos = []
    for combo in group.get("combos") or []:
        member = combo_pid(combo["id"])
        combos.append({
            "id": repair_combo_id(combo["id"]), "region": combo["region"],
            "pid": member, "live": member in records,
            # Display only. Never parsed for weights.
            "lines_html": combo.get("lines_html"),
        })
    regions = _regions(group.get("combos") or [])
    return {"source": SOURCE_GROUP, "regions": regions, "region_count": len(regions),
            "combos": combos}


def _record_line_key(entry: dict) -> str | None:
    """A record's or group's line key: `family|||subfamily` (the drop's
    `subfamily` field holds the product line), else the first combo that
    states both."""
    family, line = entry.get("family"), entry.get("subfamily")
    if not (family and line):
        for combo in entry.get("combos") or []:
            if combo.get("family") and combo.get("subfamily"):
                family, line = combo["family"], combo["subfamily"]
                break
    return make_line_key(family, line) if family and line else None


def _desired_templates() -> dict[str, dict]:
    """code → {fields, family, line_key, combos, caps}: records, then groups,
    then card-only keys; shells skipped."""
    records = raw("FORMULA_COMBOS")
    groups = raw("AUTO_GROUPS")
    absorbed = _absorbed()
    cas = raw("CAS_LOOKUP")
    out: dict[str, dict] = {}
    for code in card_status.card_codes():
        status = card_status.classify(code)
        if status is None:
            continue  # a shell; card_codes() already leaves them out
        columns, internal = _curated(code)
        caps: list[TrustReason] = []
        if code in records:
            rec = records[code]
            combos = rec.get("combos") or []
            first = combos[0] if combos else {}
            meta, rec_internal = _record_meta(code, rec, absorbed.get(code))
            internal.update(rec_internal)
            if isinstance(rec.get("pricing_gap"), dict):
                gap_line = rec["pricing_gap"].get("line")
                caps.append(TrustReason(reason=REASON_PRICING_GAP,
                                        subjects=[gap_line] if gap_line else [],
                                        detail=_CAP_DETAIL[REASON_PRICING_GAP]))
            if rec.get("margin_status"):
                caps.append(TrustReason(reason=REASON_MARGIN_STATUS,
                                        detail=_CAP_DETAIL[REASON_MARGIN_STATUS]))
            spec = {
                "family": rec.get("family"), "line_key": _record_line_key(rec),
                "combos": combos, "fields": {
                    "name": rec["name"], "full_name": rec.get("full") or None,
                    "form": first.get("form") or None,
                    "volatile": first.get("volatile") if combos else None,
                    "is_group": False, "group_members": None,
                    "absorbed_into": absorbed[code][0] if code in absorbed else None,
                    "catalog_meta": meta,
                }}
        elif code in groups:
            group = groups[code]
            combos = group.get("combos") or []
            first = combos[0] if combos else {}
            spec = {
                "family": group.get("family"), "line_key": _record_line_key(group),
                "combos": combos, "fields": {
                    "name": group["name"], "full_name": None,
                    "form": first.get("form") or None,
                    "volatile": first.get("volatile") if combos else None,
                    "is_group": True,
                    "group_members": [combo_pid(c["id"]) for c in combos],
                    "absorbed_into": None,
                    "catalog_meta": _group_meta(group, records),
                }}
        else:
            # A card-only key: content with no record and no group. The drop
            # gives it no name, family, line or combos.
            spec = {
                "family": None, "line_key": None, "combos": [], "fields": {
                    "name": code, "full_name": None, "form": None, "volatile": None,
                    "is_group": False, "group_members": None,
                    "absorbed_into": absorbed[code][0] if code in absorbed else None,
                    "catalog_meta": {"source": SOURCE_CARD, "regions": [],
                                     "region_count": 0, "combos": []},
                }}
        spec["fields"].update(columns)
        spec["fields"].update({
            "cas_number": cas.get(code) or None,
            "card_kind": status.kind,
            "supply_status": status.supply_status,
            "supply_status_detail": status.detail,
            "redirect_to": status.redirect_to,
        })
        spec["internal"] = internal
        spec["caps"] = caps
        out[code] = spec
    return out


# ── Templates ────────────────────────────────────────────────────────────────

def _load_templates(db: Session, report: LoadReport,
                    desired: dict[str, dict]) -> dict[str, FormulaTemplate]:
    diff = _diff(report, "formula_templates")
    families = {f.name: f.id for f in db.query(ChemicalFamily)}
    lines = line_index(db)
    current_keys = {k for (k,) in db.query(ProductLine.line_key).filter(
        ProductLine.retired_at.is_(None))}
    current = {t.code: t for t in db.query(FormulaTemplate).filter(
        FormulaTemplate.team_id.is_(None), FormulaTemplate.code.isnot(None))}

    creator: list[uuid.UUID] = []

    def make(code: str, fields: dict) -> FormulaTemplate:
        # Resolved once, and only if something is created: a clean re-run
        # does not need a super admin to exist.
        if not creator:
            creator.append(loader_user_id(db))
        return FormulaTemplate(team_id=None, code=code, created_by=creator[0],
                               expression=None, **fields)

    for code, spec in desired.items():
        family_id = families.get(spec["family"]) if spec["family"] else None
        if spec["family"] and family_id is None:
            diff.skipped.append((code, f"family {spec['family']!r} is not a loaded family; "
                                       "family_id left NULL"))
        line_id = lines.get(spec["line_key"]) if spec["line_key"] else None
        internal = dict(spec["internal"])
        if spec["line_key"] and line_id is None:
            diff.skipped.append((code, "record line key is not a current product line; "
                                       "product_line_id NULL (product line not yet published)"))
        if spec["line_key"] and spec["line_key"] not in current_keys:
            # The record's own key, kept for us when it is not a current line
            # key (no line, or a line found through a former key).
            internal["record_line_key"] = spec["line_key"]
        fields = {**spec["fields"], "family_id": family_id, "product_line_id": line_id,
                  "internal_meta": internal or None}
        _upsert(db, diff, current, code, fields, lambda f, c=code: make(c, f))

    # A card this loader wrote that the drop no longer has: kept (team data
    # may point at it), hidden as withdrawn.
    for code, t in current.items():
        if code in desired:
            continue
        source = (t.catalog_meta or {}).get("source")
        if source not in SOURCES:
            diff.stale += 1      # not a drop template; never ours to change
            continue
        if t.card_kind != card_status.KIND_WITHDRAWN:
            t.card_kind = card_status.KIND_WITHDRAWN
            t.supply_status = None
            t.supply_status_detail = None
            t.redirect_to = None
            diff.updated += 1
            diff.skipped.append((code, "no longer in the drop; card_kind set to withdrawn"))
        else:
            diff.unchanged += 1

    for code in card_status.shells():
        diff.skipped.append((code, "GRP-* card with no AUTO_GROUPS entry (a shell): no template"))
    for target, merged in card_status.merged_from().items():
        diff.skipped.append((target, f"merged_from {', '.join(merged)} logged (the merged "
                                     "PIDs have no record)"))
    db.flush()
    return {code: current[code] for code in desired}


# ── Coverage and cost lines ──────────────────────────────────────────────────

def _line_specs(combo: dict, region: str, variant: str, covered: dict[str, str],
                type_codes: dict[str, TypeCode], diff) -> list[dict]:
    """The component rows one combo's lines become, in line order."""
    specs = []
    for seq, (share, label, tag, kind) in enumerate(combo.get("lines") or []):
        tc = None
        if tag in covered:
            tc = type_codes.get(tag)
            if tc is None:
                diff.skipped.append((f"{repair_combo_id(combo['id'])}#{seq}",
                                     f"tag {tag!r} is in FCOVERED but has no type_codes "
                                     "row (run the indexes loader)"))
                continue
        elif tag != "fixed":
            diff.skipped.append((f"{repair_combo_id(combo['id'])}#{seq}",
                                 f"tag {tag!r} is not in FCOVERED; loaded as a fixed line"))
        indexed = tc is not None
        specs.append({
            "name": label,
            "component_type": "index" if indexed else "fixed",
            "cost_category": kind,
            "commodity_id": tc.resolves_to_id if indexed else None,
            "type_code_id": tc.id if indexed else None,
            "region": region,
            "variant": variant,
            "weight_pct": share,
            "is_proxy": False,
            "line_proxy_status": LINE_PROXY_UNCLASSIFIED if indexed else None,
            "sort_order": seq,
        })
    return specs


def _same_recipe(current: list[FormulaTemplateComponent], desired: list[dict]) -> bool:
    """Field by field, so an unchanged recipe is not rewritten and reports
    unchanged."""
    if len(current) != len(desired):
        return False
    for row, spec in zip(current, desired):
        for name in ("name", "component_type", "cost_category", "commodity_id",
                     "type_code_id", "line_proxy_status"):
            if getattr(row, name) != spec[name]:
                return False
        if (row.variant or "") != spec["variant"]:
            return False
        if bool(row.is_proxy) != spec["is_proxy"] or row.input_template_id is not None:
            return False
        if (row.sort_order or 0) != spec["sort_order"]:
            return False
        if row.weight_pct is None or abs(float(row.weight_pct) - float(spec["weight_pct"])) > 1e-6:
            return False
    return True


def _load_combos(db: Session, report: LoadReport, desired: dict[str, dict],
                 templates: dict[str, FormulaTemplate]
                 ) -> list[tuple[FormulaRegionCoverage, list[TrustReason]]]:
    cov_diff = _diff(report, "formula_region_coverage")
    line_diff = _diff(report, "formula_template_components")
    covered = raw("FCOVERED")
    type_codes = {tc.code: tc for tc in db.query(TypeCode)}
    ids = {t.id for t in templates.values()}

    existing_cov = {
        (c.template_id, c.region, c.variant or ""): c
        for c in db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.template_id.in_(ids))
    }
    existing_lines: dict[tuple, list[FormulaTemplateComponent]] = defaultdict(list)
    for row in (db.query(FormulaTemplateComponent)
                .filter(FormulaTemplateComponent.template_id.in_(ids),
                        FormulaTemplateComponent.region.isnot(None))
                .order_by(FormulaTemplateComponent.sort_order)):
        existing_lines[(row.template_id, row.region, row.variant or "")].append(row)

    touched: set[tuple] = set()
    loaded: list[tuple[FormulaRegionCoverage, list[TrustReason]]] = []
    for code, spec in desired.items():
        template = templates[code]
        is_group = spec["fields"]["is_group"]
        for combo in spec["combos"]:
            combo_id = repair_combo_id(combo["id"])
            region = app_region(combo["region"])
            if region is None:
                cov_diff.skipped.append((combo_id, f"region {combo['region']!r} has no mapping"))
                continue
            variant = combo.get("variant") or ""
            key = (template.id, region, variant)
            if key in touched:
                cov_diff.skipped.append((combo_id, f"second combo for {code} {region} "
                                                   f"{variant!r}; first one kept"))
                continue
            touched.add(key)

            margin, _source = _margin(combo)
            fields = {"margin_pct": margin, "base_year": BASE_YEAR,
                      "base_quarter": BASE_QUARTER, "withdrawn_at": None}
            coverage = existing_cov.get(key)
            if coverage is None:
                # base_price / currency: the drop states neither. NULL on
                # create, and never written over afterwards.
                coverage = FormulaRegionCoverage(template_id=template.id, region=region,
                                                 variant=variant, **fields)
                db.add(coverage)
                existing_cov[key] = coverage
                cov_diff.created += 1
            else:
                changes: list = []
                for name, value in fields.items():
                    _apply(coverage, name, value, changes)
                # Review state (needs_review, reviewed_*, provenance) belongs to
                # whoever signed the combo off; a reload never writes it.
                if changes:
                    cov_diff.updated += 1
                else:
                    cov_diff.unchanged += 1
            loaded.append((coverage, spec["caps"]))

            if is_group:
                continue  # a group card has coverage and no components
            specs = _line_specs(combo, region, variant, covered, type_codes, line_diff)
            current = existing_lines.get(key, [])
            if _same_recipe(current, specs):
                line_diff.unchanged += len(current)
                continue
            for row in current:
                db.delete(row)
                line_diff.deleted += 1
            for s in specs:
                db.add(FormulaTemplateComponent(template_id=template.id, **s))
                line_diff.created += 1
    db.flush()

    # A combo the drop no longer has: kept with its cost lines, withdrawn.
    now = datetime.now(timezone.utc)
    code_of = {t.id: code for code, t in templates.items()}
    for key, coverage in existing_cov.items():
        if key in touched:
            continue
        cov_diff.stale += 1
        if coverage.withdrawn_at is None:
            coverage.withdrawn_at = now
            cov_diff.updated += 1
            label = " ".join(p for p in (code_of.get(key[0], str(key[0])), key[1], key[2]) if p)
            cov_diff.skipped.append((label, "combo no longer in the drop; withdrawn_at set"))

    # A region-tagged line set with no coverage row cannot be priced and
    # would shadow nothing real: deleted. Groups never have line sets.
    group_ids = {templates[code].id for code, spec in desired.items()
                 if spec["fields"]["is_group"]}
    for key, rows in existing_lines.items():
        if key not in existing_cov or key[0] in group_ids:
            for row in rows:
                db.delete(row)
                line_diff.deleted += 1
    db.flush()
    return loaded


# ── Trust ────────────────────────────────────────────────────────────────────

def _capped(assessment, caps: list[TrustReason]):
    """The assessment of a combo whose record flags a warning: at most
    `medium`, always in front of a reviewer, with the fixed reasons added."""
    # assess() already caps from the template's stored catalog_meta; this
    # adds nothing then (a reason is never repeated). It stays so the loader
    # caps from the record itself even before catalog_meta is written.
    return cap_for_source_warnings(assessment, caps)


def _grade(db: Session, report: LoadReport,
           coverages: list[tuple[FormulaRegionCoverage, list[TrustReason]]]) -> None:
    """Run the trust assessment on every loaded combo; store it only where the
    stored grade, its inputs or the review flag would move."""
    diff = _diff(report, TRUST_ROW)
    by_grade: Counter = Counter()
    for coverage, caps in coverages:
        assessment = _capped(assess(db, coverage), caps)
        by_grade[assessment.grade] += 1
        stale_sign_off = (
            coverage.reviewed_at is not None
            and coverage.review_fingerprint != fingerprint_for(coverage_lines(db, coverage))
        )
        signed_off = coverage.reviewed_at is not None and not stale_sign_off
        needs_review = assessment.needs_review and not signed_off
        if (stale_sign_off or coverage.trust_grade != assessment.grade
                or coverage.needs_review != needs_review
                or coverage.trust_inputs != assessment.as_inputs()):
            apply_assessment(db, coverage, assessment)
            diff.updated += 1
        else:
            diff.unchanged += 1
    # The distribution, as check rows (`unchanged` = combos at that grade).
    for grade in TRUST_GRADES:
        if by_grade.get(grade):
            _diff(report, f"{TRUST_CHECK_PREFIX}{grade}").unchanged = by_grade[grade]
    db.flush()


# ── Placements ───────────────────────────────────────────────────────────────

def _backfill_placements(db: Session, report: LoadReport,
                         templates: dict[str, FormulaTemplate]) -> None:
    """`category_placements.template_id` by code, so the taxonomy loader's next
    run computes the same value and reports nothing. Through the ORM rather
    than a bulk UPDATE, so placement rows another loader holds in this session
    see the new value."""
    diff = _diff(report, PLACEMENT_ROW)
    ids = {code: t.id for code, t in templates.items()}
    for placement in db.query(CategoryPlacement):
        template_id = ids.get(placement.pid)
        if template_id is None:
            diff.skipped.append((placement.pid, "placement product has no platform template"))
        elif placement.template_id != template_id:
            placement.template_id = template_id
            diff.updated += 1
        else:
            diff.unchanged += 1
    db.flush()


# ── Entry point ──────────────────────────────────────────────────────────────

def load(db: Session, report: LoadReport) -> LoadReport:
    """Load the catalogue, grade it and back-fill the placements. Flushes,
    never commits."""
    if db.query(TypeCode.id).first() is None:
        raise RuntimeError("no type codes loaded: run the indexes loader first "
                           "(cost lines resolve through them)")
    desired = _desired_templates()
    templates = _load_templates(db, report, desired)
    coverages = _load_combos(db, report, desired, templates)
    _grade(db, report, coverages)
    _backfill_placements(db, report, templates)
    return report


# ── The engine check ─────────────────────────────────────────────────────────

def check_engine(db: Session, codes: list[str] | None = None) -> dict:
    """Run `intelligence.derive` on every platform coverage row (or those of
    `codes`) at its own region, and count what comes back.

    Returns `{combos, evaluable, index_level_only, no_index_lines,
    with_data_gaps, reasons, not_evaluable}`: `reasons` counts the stated
    reason of every combo that is not evaluable, `not_evaluable` lists
    `(code, region, reason)`. `index_level_only` counts evaluable combos whose
    only caveat is the missing base price (the drop carries no prices).
    """
    from app.services.intelligence import derive

    q = (db.query(FormulaRegionCoverage, FormulaTemplate.code)
         .join(FormulaTemplate, FormulaTemplate.id == FormulaRegionCoverage.template_id)
         .filter(FormulaTemplate.team_id.is_(None)))
    if codes is not None:
        q = q.filter(FormulaTemplate.code.in_(codes))
    out: dict[str, Any] = {"combos": 0, "evaluable": 0, "index_level_only": 0,
                           "no_index_lines": 0, "with_data_gaps": 0,
                           "reasons": Counter(), "not_evaluable": []}
    for coverage, code in q.order_by(FormulaTemplate.code, FormulaRegionCoverage.region):
        result = derive(db, coverage.template_id, coverage.region)
        out["combos"] += 1
        if result.evaluable:
            out["evaluable"] += 1
            if result.base_price is None:
                out["index_level_only"] += 1
            if not any(c["component_type"] == "index" for c in result.components):
                out["no_index_lines"] += 1
            if result.data_gaps:
                out["with_data_gaps"] += 1
        else:
            out["reasons"][result.reason] += 1
            out["not_evaluable"].append((code, coverage.region, result.reason))
    return out
