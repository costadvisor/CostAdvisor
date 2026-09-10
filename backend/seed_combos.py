"""SEED-2 (Scrum 60): load the 676 weighted combos as real formula components.

Reads sample_idea/scrum60/db_formula_combinations.html (the source of truth —
combos live in its embedded HIER array; each combo's cost lines exist only as
HTML in the `lines_html` field), plus formula_tier_lookup.json (per-formula
n_combos for the completeness check) and correction_plan_log.json (the
reasoning behind every weight correction — loaded as review metadata, NEVER
re-applied: the corrections are already baked into the source lines).

What lands where (all keyed formula x region, upsert / replace-in-place):

- combo         -> formula_region_coverage   margin, data_confidence,
                                             coverage_tier, needs_review
                                             (CONF-LOW = placeholder, not
                                             fact), review_metadata
- cost lines    -> formula_template_components  region-tagged line sets;
                   idx-direct -> index, idx-proxy -> index (is_proxy),
                   idx-fixed -> fixed, and a code that is a formula_id ->
                   formula (tiered chaining)
- subfamily     -> formula_templates.subfamily_id (the mapping SEED-1's
                   workbook didn't carry)

The lines_html markup (classes cl / wt-num / cl-label / cl-idx idx-*) has
shifted shape before, so the parser is a class-driven state machine that
ignores tag names, nesting, attribute order and unknown wrappers — it only
needs the class tokens to survive a reformat.

Weight sums are checked against a tolerance band, not exact 100: the real
data runs 99.9-110 (margin and rounding ride on top of the raw lines).

Run:      python -m seed_combos             # validate + load
Dry run:  python -m seed_combos --dry-run   # validate + diff report, no writes
"""

# SUPERSEDED by the catalog retarget (Scrum 74/3b).
#
# This loads the older 257-formula / 676-combo drop into
# formula_region_coverage and formula_template_components. The 2026-07 drop now
# owns those tables via app/services/drop/catalog_loader.py, and two loaders
# cannot both be authoritative for the same rows — running either overwrites
# the other's recipes for the 340 overlapping templates.
#
# Kept, not deleted: the parser and validation logic here still documents how
# the previous drop was shaped, and its parser tests still pass. Do not run
# run() against a database that has been retargeted.
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

from sqlalchemy import func

from app.database import SessionLocal, bypass_rls_var
from app.models.chemical_family import ChemicalFamily
from app.models.formula_template import (
    FormulaRegionCoverage,
    FormulaTemplate,
    FormulaTemplateComponent,
)
from app.models.index_data import CommodityIndex
from app.models.region import Region
from app.models.subfamily import Subfamily
from app.services.formula_resolver import MAX_CHAIN_DEPTH
import seed_index_metadata as sim

SCRUM60_DIR = Path(__file__).resolve().parents[1] / "sample_idea" / "scrum60"

# The source weight sums legitimately run 99.9-110 (README: raw lines sum to
# 100; margin/rounding ride on top in some families). The band has a little
# headroom for float noise; anything outside it is a broken recipe, not noise.
WEIGHT_SUM_MIN = 99.5
WEIGHT_SUM_MAX = 110.5

CONFIDENCES = {"CONF-HIGH", "CONF-MED", "CONF-LOW"}
COVERAGE_TIERS = {"free", "good_proxy", "weak_proxy", "blocked"}

# Combo region grain -> canonical Region.code (Scrum 56 entities). The three
# rows that don't exist yet (India / APAC / MEA) are created by ensure_regions.
REGION_MAP = {
    "EU": "Europe", "NA": "NA", "CN": "China", "LA": "Latam",
    "IN": "India", "APAC": "APAC", "MEA": "MEA",
}
# (code, name, parent_code) — parent None = top-level
NEW_REGIONS = [
    ("India", "India", "Asia"),
    ("APAC", "Asia-Pacific", None),
    ("MEA", "Middle East & Africa", None),
]

_CONF_BRACKET_RE = re.compile(r"\s*\[CONF-[A-Z]+\]\s*$")


# ── File resolution ───────────────────────────────────────────────────────────

def resolve_file(stem: str, suffix: str) -> Path:
    """Exact name first; else the newest handoff variant ('name (1).ext')."""
    exact = SCRUM60_DIR / f"{stem}{suffix}"
    if exact.exists():
        return exact
    candidates = sorted(SCRUM60_DIR.glob(f"{stem}*{suffix}"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0]
    raise SystemExit(f"No {stem}*{suffix} found in {SCRUM60_DIR}")


# ── lines_html parser ─────────────────────────────────────────────────────────

class _LinesParser(HTMLParser):
    """Class-token state machine over the lines_html fragment.

    A new line opens at a `cl` container — or, if the container class ever
    disappears in a future reformat, at a `wt-num` when the current line
    already has a weight. Field elements are recognised purely by class token
    (wt-num / cl-label / cl-idx + idx-<kind>) regardless of tag name, attribute
    order, nesting or unknown wrappers.
    """

    def __init__(self):
        super().__init__()
        self.lines: list[dict] = []
        self._cur: dict | None = None
        self._capture: str | None = None

    def _flush(self):
        if self._cur is not None:
            self.lines.append(self._cur)
        self._cur = None

    def _open_line(self):
        self._flush()
        self._cur = {"weight_raw": "", "label": "", "code": "", "kind": None}

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        self._capture = None
        if "cl" in classes:
            self._open_line()
            return
        if "wt-num" in classes:
            if self._cur is None or self._cur["weight_raw"]:
                self._open_line()  # container class gone — weight starts a line
            self._capture = "weight_raw"
        elif "cl-label" in classes:
            if self._cur is None:
                self._open_line()
            self._capture = "label"
        elif "cl-idx" in classes:
            if self._cur is None:
                self._open_line()
            self._capture = "code"
            kind = next((c[4:] for c in classes if c.startswith("idx-")), None)
            self._cur["kind"] = kind

    def handle_endtag(self, tag):
        self._capture = None

    def handle_data(self, data):
        if self._capture and self._cur is not None:
            self._cur[self._capture] += data

    def close(self):
        super().close()
        self._flush()


def parse_lines_html(fragment: str) -> tuple[list[dict], list[str]]:
    """Parse a combo's lines_html into [{weight_pct, label, code, kind}].

    Returns (lines, problems); problems are human-readable descriptions of
    anything that failed to parse (missing weight/label/code, bad number).
    """
    p = _LinesParser()
    p.feed(fragment)
    p.close()
    lines, problems = [], []
    for i, raw in enumerate(p.lines):
        label = _CONF_BRACKET_RE.sub("", raw["label"].strip()).strip()
        code = raw["code"].strip()
        kind = raw["kind"]
        wt_txt = raw["weight_raw"].strip().rstrip("%").replace("+", "").strip()
        try:
            weight = float(wt_txt)
        except ValueError:
            problems.append(f"line {i + 1}: unparseable weight {raw['weight_raw']!r}")
            continue
        if not label:
            problems.append(f"line {i + 1}: empty label")
            continue
        if not code or kind not in ("direct", "proxy", "fixed"):
            problems.append(f"line {i + 1} ({label}): missing/unknown index tag "
                            f"(code={code!r}, kind={kind!r})")
            continue
        lines.append({"weight_pct": weight, "label": label, "code": code, "kind": kind})
    if not lines and not problems:
        problems.append("no cost lines found in fragment")
    return lines, problems


# ── Source extraction ─────────────────────────────────────────────────────────

def extract_combos(html: str) -> list[dict]:
    """Pull the embedded HIER array out of the combinations page and flatten
    family -> subfamily -> formula -> combos into one combo list."""
    m = re.search(r"(?:const|var|let)\s+HIER\s*=\s*\[", html)
    if m:
        start = html.index("[", m.start())
    else:
        # Markup drifted (variable renamed): take the nearest array assignment
        # before the first lines_html occurrence.
        pos = html.index("lines_html")
        starts = [mm.end() - 1 for mm in re.finditer(r"=\s*\[", html) if mm.start() < pos]
        if not starts:
            raise SystemExit("Could not locate the combos data array in the HTML")
        start = starts[-1]
    hier, _ = json.JSONDecoder().raw_decode(html[start:])

    combos = []
    for fam in hier:
        for sub in fam["subfamilies"]:
            for f in sub["formulas"]:
                for c in f["combos"]:
                    combos.append({
                        "combo_id": c["id"],
                        "formula_id": c["formula_id"],
                        "family": c["family"],
                        "subfamily": c["subfamily"],
                        "region": c["region"],
                        "margin": c["margin"],
                        "data_confidence": c["data_confidence"],
                        "coverage_tier": c["coverage_tier"],
                        "reviewed_by": c.get("reviewed_by"),
                        "reviewed_at": c.get("reviewed_at"),
                        "lines_html": c["lines_html"],
                    })
    return combos


def load_sources() -> tuple[list[dict], dict, dict]:
    html = resolve_file("db_formula_combinations", ".html").read_text(encoding="utf-8")
    combos = extract_combos(html)
    tier = json.loads(resolve_file("formula_tier_lookup", ".json").read_text(encoding="utf-8"))
    log = json.loads(resolve_file("correction_plan_log", ".json").read_text(encoding="utf-8"))
    return combos, tier, log


def feed_code_map() -> dict[str, str]:
    """Line codes join the feeds as IDX-<code>: map code -> base commodity name
    (the Scrum 57 region-agnostic reconciliation key).

    Reads the ORIGINAL Scrum-57 feed roster (scrum57/seed-data-reference.xlsx),
    NOT the refreshed 2026-07 workbook. SEED-2's combos source
    (db_formula_combinations.html) is unchanged and still uses the old IDX-<code>
    feed scheme; the new workbook dropped the IDX- prefix and reshaped the feeds,
    so reading it here would resolve zero line codes. Scrum 60 stays on the old
    roster until a matching combos drop lands (see jvpdocs)."""
    feeds = sim._read_feeds(sim.DEFAULT_XLSX)
    out = {}
    for f in feeds:
        fid = str(f["index_id"])
        if fid.startswith("IDX-"):
            out[fid[4:]] = f["base"]
    return out


# ── Validation (before anything is written) ───────────────────────────────────

def validate(combos: list[dict], tier: dict, log: dict, codes: dict[str, str]
             ) -> tuple[list[str], list[str], dict]:
    errors, warnings = [], []
    parsed: dict[str, list[dict]] = {}  # combo_id -> lines

    # Parse every combo's lines first — parser problems are load blockers.
    for c in combos:
        lines, problems = parse_lines_html(c["lines_html"])
        parsed[c["combo_id"]] = lines
        for p in problems:
            errors.append(f"{c['combo_id']}: {p}")

    # Duplicate combo keys
    seen = set()
    for c in combos:
        key = (c["formula_id"], c["region"])
        if key in seen:
            errors.append(f"Duplicate combo: {c['combo_id']}")
        seen.add(key)

    # Vocab + region grain
    for c in combos:
        if c["data_confidence"] not in CONFIDENCES:
            errors.append(f"{c['combo_id']}: unknown data_confidence {c['data_confidence']!r}")
        if c["coverage_tier"] not in COVERAGE_TIERS:
            errors.append(f"{c['combo_id']}: unknown coverage_tier {c['coverage_tier']!r}")
        if c["region"] not in REGION_MAP:
            errors.append(f"{c['combo_id']}: unmapped region {c['region']!r}")

    # Per-formula counts must match the tier lookup, and the total must too.
    from collections import Counter
    per_formula = Counter(c["formula_id"] for c in combos)
    for fid, meta in tier.items():
        if per_formula.get(fid, 0) != meta["n_combos"]:
            errors.append(f"{fid}: {per_formula.get(fid, 0)} combos in source, "
                          f"tier-lookup says {meta['n_combos']}")
    for fid in per_formula:
        if fid not in tier:
            errors.append(f"{fid}: combos present but formula missing from tier-lookup")
    expected_total = sum(m["n_combos"] for m in tier.values())
    if len(combos) != expected_total:
        errors.append(f"Total combos {len(combos)} != tier-lookup total {expected_total}")

    # Weight sums within tolerance; line codes must resolve to a feed, a
    # formula (tiered chaining), or 'fixed'.
    formula_ids = set(tier)
    for c in combos:
        lines = parsed[c["combo_id"]]
        if not lines:
            continue
        total = sum(l["weight_pct"] for l in lines)
        if not (WEIGHT_SUM_MIN <= total <= WEIGHT_SUM_MAX):
            errors.append(f"{c['combo_id']}: line weights sum to {total:.2f} "
                          f"(tolerance {WEIGHT_SUM_MIN}-{WEIGHT_SUM_MAX})")
        for l in lines:
            if len(l["label"]) > 64:
                warnings.append(f"{c['combo_id']}: label truncated to 64 chars: {l['label']!r}")
            if l["kind"] == "fixed":
                continue
            if l["code"] in codes:
                continue
            if l["code"] in formula_ids:
                continue
            errors.append(f"{c['combo_id']}: line '{l['label']}' references unknown "
                          f"index/formula code {l['code']!r}")

    # Formula-as-input chain graph: no cycles, depth within the resolver cap.
    graph: dict[str, set[str]] = {}
    for c in combos:
        for l in parsed[c["combo_id"]]:
            if l["kind"] != "fixed" and l["code"] in formula_ids:
                graph.setdefault(c["formula_id"], set()).add(l["code"])

    def depth(fid, path):
        if fid in path:
            errors.append(f"Circular formula chain: {' -> '.join(path + [fid])}")
            return 0
        return 1 + max((depth(ch, path + [fid]) for ch in graph.get(fid, ())), default=-1)

    for fid in graph:
        if depth(fid, []) > MAX_CHAIN_DEPTH:
            errors.append(f"{fid}: formula chain deeper than {MAX_CHAIN_DEPTH}")

    # Review queue expectations (informational)
    low = [c for c in combos if c["data_confidence"] == "CONF-LOW"]
    if len(low) != 99:
        warnings.append(f"CONF-LOW combos = {len(low)} (reference drop had 99)")
    for fid in log:
        if fid not in formula_ids:
            warnings.append(f"correction_plan_log entry for unknown formula {fid}")
    for c in combos:
        t = tier.get(c["formula_id"])
        if t and t["data_confidence"] != c["data_confidence"]:
            warnings.append(f"{c['combo_id']}: confidence {c['data_confidence']} != "
                            f"tier-lookup {t['data_confidence']}")

    return errors, warnings, parsed


# ── Load ──────────────────────────────────────────────────────────────────────

class Tally:
    def __init__(self):
        self.created = self.updated = self.unchanged = 0

    def line(self):
        return f"{self.created} created, {self.updated} updated, {self.unchanged} unchanged"


def ensure_regions(db, dry_run: bool) -> int:
    created = 0
    for code, name, parent_code in NEW_REGIONS:
        if db.query(Region).filter(Region.code == code).first():
            continue
        created += 1
        if not dry_run:
            parent = (db.query(Region).filter(Region.code == parent_code).first()
                      if parent_code else None)
            db.add(Region(code=code, name=name, parent_id=parent.id if parent else None))
            db.flush()
    return created


def run(db, dry_run: bool = False, verbose: bool = True) -> dict:
    combos, tier, log = load_sources()
    codes = feed_code_map()
    errors, warnings, parsed = validate(combos, tier, log, codes)

    def say(*a):
        if verbose:
            print(*a)

    say(f"== SEED-2 combo load{' (dry run)' if dry_run else ''} — "
        f"{len(combos)} combos / {len(tier)} formulas ==")
    for w in warnings:
        say(f"  warning: {w}")
    if errors:
        for e in errors[:40]:
            say(f"  ERROR: {e}")
        if len(errors) > 40:
            say(f"  ... and {len(errors) - 40} more")
        raise SystemExit(f"{len(errors)} validation error(s) — nothing was written")

    # DB prerequisites: SEED-1 shells + commodities must exist.
    shells = {t.code: t for t in db.query(FormulaTemplate).filter(
        FormulaTemplate.team_id.is_(None), FormulaTemplate.code.isnot(None)).all()}
    missing = sorted(set(c["formula_id"] for c in combos) - set(shells))
    if missing:
        raise SystemExit(f"{len(missing)} formula shell(s) missing (run seed_catalog first): "
                         f"{', '.join(missing[:8])}...")
    commodity_ids = {n.lower(): i for i, n in
                     db.query(CommodityIndex.id, CommodityIndex.name).all()}
    missing_c = sorted({codes[l['code']] for c in combos for l in parsed[c['combo_id']]
                        if l['kind'] != 'fixed' and l['code'] in codes
                        and codes[l['code']].lower() not in commodity_ids})
    if missing_c:
        raise SystemExit(f"{len(missing_c)} commodities missing (run seed_catalog first): "
                         f"{', '.join(missing_c[:8])}...")

    regions_created = ensure_regions(db, dry_run)

    # Fill the formula -> subfamily link SEED-1 couldn't derive.
    sub_tally = Tally()
    fam_by_code = {f.code: f for f in db.query(ChemicalFamily).filter(
        ChemicalFamily.team_id.is_(None), ChemicalFamily.code.isnot(None)).all()}
    sub_rows = {(s.family_id, s.name): s for s in
                db.query(Subfamily).filter(Subfamily.team_id.is_(None)).all()}
    formula_sub: dict[str, tuple[str, str]] = {}
    for c in combos:
        fam_code = c["family"].split()[0]
        formula_sub.setdefault(c["formula_id"], (fam_code, c["subfamily"]))
    for fid, (fam_code, sub_name) in sorted(formula_sub.items()):
        fam = fam_by_code.get(fam_code)
        sub = sub_rows.get((fam.id, sub_name)) if fam else None
        if sub is None:
            raise SystemExit(f"{fid}: subfamily {fam_code}/{sub_name!r} not found (run seed_catalog first)")
        shell = shells[fid]
        if shell.subfamily_id == sub.id:
            sub_tally.unchanged += 1
        else:
            sub_tally.updated += 1
            if not dry_run:
                shell.subfamily_id = sub.id

    cov_tally, comp_tally = Tally(), Tally()
    lines_written = 0
    for c in combos:
        template = shells[c["formula_id"]]
        region_code = REGION_MAP[c["region"]]
        lines = parsed[c["combo_id"]]

        # ── Coverage (the combo row): margin + trust layer ──
        target = {
            "margin_pct": float(c["margin"]),
            "data_confidence": c["data_confidence"],
            "coverage_tier": c["coverage_tier"],
            # SCRUM-78: `needs_review` is no longer derived here. It was set
            # from `data_confidence == "CONF-LOW"`; the July sheet dropped that
            # column, and the flag is now the output of `services/trust`'s
            # derived grade — computed from the resolution layer and the weight
            # set, which is information this seeder does not have at this point.
            # Run the trust recompute after a seed.
            "reviewed_by": c["reviewed_by"],
            "review_metadata": (
                {"source_combo_id": c["combo_id"], "correction_plan": log[c["formula_id"]]}
                if c["formula_id"] in log else {"source_combo_id": c["combo_id"]}
            ),
        }
        row = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.template_id == template.id,
            FormulaRegionCoverage.region == region_code,
        ).first()
        if row is None:
            cov_tally.created += 1
            if not dry_run:
                db.add(FormulaRegionCoverage(template_id=template.id,
                                             region=region_code, **target))
        else:
            if row.reviewed_at is not None:
                # An expert signed this combo off in-app; the source carries no
                # review state of its own, so a re-run must not clobber it.
                # `needs_review` is no longer written by this seeder at all
                # (SCRUM-78), so only the reviewer attribution needs protecting.
                target.pop("reviewed_by")
            changed = [k for k, v in target.items()
                       if (float(getattr(row, k)) if k == "margin_pct" and getattr(row, k) is not None
                           else getattr(row, k)) != v]
            if changed:
                cov_tally.updated += 1
                if not dry_run:
                    for k, v in target.items():
                        setattr(row, k, v)
            else:
                cov_tally.unchanged += 1

        # ── Component lines for (formula, region) ──
        desired = []
        for i, l in enumerate(lines):
            if l["kind"] == "fixed":
                ctype, commodity_id, input_id = "fixed", None, None
            elif l["code"] in codes:
                ctype = "index"
                commodity_id = commodity_ids[codes[l["code"]].lower()]
                input_id = None
            else:  # validated: a formula_id — tiered chaining
                ctype = "formula"
                commodity_id = None
                input_id = shells[l["code"]].id
            desired.append((l["label"][:64], ctype, commodity_id, input_id,
                            round(l["weight_pct"], 4), l["kind"] == "proxy", i))
        existing = (
            db.query(FormulaTemplateComponent)
            .filter(FormulaTemplateComponent.template_id == template.id,
                    FormulaTemplateComponent.region == region_code)
            .order_by(FormulaTemplateComponent.sort_order)
            .all()
        )
        current = [(e.name, e.component_type, e.commodity_id, e.input_template_id,
                    float(e.weight_pct), e.is_proxy, e.sort_order) for e in existing]
        if current == desired:
            comp_tally.unchanged += 1
        else:
            if existing:
                comp_tally.updated += 1
            else:
                comp_tally.created += 1
            if not dry_run:
                for e in existing:
                    db.delete(e)
                for name, ctype, commodity_id, input_id, wt, proxy, order in desired:
                    db.add(FormulaTemplateComponent(
                        template_id=template.id, region=region_code, name=name,
                        component_type=ctype, commodity_id=commodity_id,
                        input_template_id=input_id, weight_pct=wt,
                        is_proxy=proxy, sort_order=order,
                    ))
        lines_written += len(desired)

    low = sum(1 for c in combos if c["data_confidence"] == "CONF-LOW")
    say(f"Regions:     {regions_created} created ({', '.join(r[0] for r in NEW_REGIONS)})")
    say(f"Subfam link: {len(formula_sub)} formulas -> {sub_tally.line()}")
    say(f"Coverage:    {len(combos)} combos -> {cov_tally.line()}")
    say(f"Components:  {len(combos)} line sets ({lines_written} lines) -> {comp_tally.line()}")
    say(f"Review flag: {low} CONF-LOW combos flagged needs_review")
    return {"warnings": warnings, "combos": len(combos), "lines": lines_written,
            "regions_created": regions_created, "subfamily": sub_tally,
            "coverage": cov_tally, "components": comp_tally, "conf_low": low}


def main():
    dry_run = "--dry-run" in sys.argv[1:]
    bypass_rls_var.set(True)  # platform reference data — no user context
    db = SessionLocal()
    try:
        run(db, dry_run=dry_run)
        if dry_run:
            db.rollback()
            print("Dry run — no changes written.")
        else:
            db.commit()
            print("Committed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
