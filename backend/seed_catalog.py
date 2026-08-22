"""SEED-1 (Scrum 59): load the catalog taxonomy + index feeds. Safe to re-run.

Reads sample_idea/scrum59/seed-data-reference.xlsx (generated from the
db_formula_combinations source-of-truth drop; the retired index_list.html and
its orphan IDX-CPO-CN were already excluded upstream — we still guard against
the orphan reappearing) and loads:

- 22 families           -> chemical_families   (platform rows; key: code, name fallback)
- 143 subfamilies       -> subfamilies         (platform rows; key: family + name)
- 367 formula shells    -> formula_templates   (platform rows; key: code = formula_id;
                                                 weighted components are SEED-2)
- 187 index feeds       -> commodity_indexes   (region-agnostic reconciliation
                                                 reused from seed_index_metadata)

Counts reflect the 2026-07 refreshed workbook. SEED-2 (Scrum 60 combos) is NOT
rebuilt against it — its weighted-recipe source (db_formula_combinations.html)
still carries the older 257-formula / 676-combo drop, so 80 of those formula
IDs no longer exist as shells here. That inconsistency is accepted until a
matching combos drop lands (see jvpdocs / the Scrum 59/60 notes).

Idempotent: every row is matched by its stable key and updated in place — run
twice and no row counts change; update one source value and only that row
changes. Nothing is ever deleted; platform rows missing from the source are
reported as stale, not pruned.

Before writing anything, the source is join-validated (every formula a feed
says it prices must exist; every formula must be priced by at least one feed)
so a bad reference shouts now instead of quietly becoming an unpriceable
product later.

Run:      python -m seed_catalog             # validate + load
Dry run:  python -m seed_catalog --dry-run   # validate + diff report, no writes
Custom:   python -m seed_catalog [--dry-run] path/to/workbook.xlsx
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import openpyxl
from sqlalchemy import func

from app.database import SessionLocal, bypass_rls_var
from app.models.chemical_family import ChemicalFamily
from app.models.formula_template import FormulaTemplate
from app.models.index_data import CommodityIndex
from app.models.subfamily import Subfamily
from app.models.user import User
import seed_index_metadata as sim

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "sample_idea" / "scrum59"

# Reference-drop totals. Drift is a warning, not an error — the source keeps
# getting better and counts will move; structure breaking is what hard-fails.
# Updated to the 2026-07 refreshed workbook (22 families / 143 subfamilies /
# 367 formulas / 1135 region-combos / 187 feeds). NB: that workbook's own
# "Read me" tab still shows the older 22/91/257/676/158 totals — the data sheets
# were expanded but the summary tab wasn't regenerated, so a Read-me/data
# mismatch is expected and surfaces only as count-drift warnings.
EXPECTED = {"families": 22, "subfamilies": 143, "formulas": 367, "feeds": 187, "combos": 1135}

# The retired index_list.html carried this orphan code that exists nowhere
# else; if it ever shows up in a drop, someone re-exported from the dead list.
RETIRED_ORPHANS = {"IDX-CPO-CN"}

SEED_USER_EMAIL = "jil@staminachem.com"

_FAMILY_RE = re.compile(r"^(F\d+)\s+(.+)$")


# ── Workbook location + parsing ───────────────────────────────────────────────

def resolve_workbook(path_arg: str | None = None) -> Path:
    """Find the reference workbook. Handoff copies sometimes arrive with a
    ' (1)' suffix — take the newest matching variant rather than tripping."""
    if path_arg:
        p = Path(path_arg)
        if not p.exists():
            raise SystemExit(f"Reference workbook not found: {p}")
        return p
    exact = DEFAULT_DIR / "seed-data-reference.xlsx"
    if exact.exists():
        return exact
    candidates = sorted(
        DEFAULT_DIR.glob("seed-data-reference*.xlsx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    raise SystemExit(f"No seed-data-reference*.xlsx found in {DEFAULT_DIR}")


def parse_family_cell(cell: str) -> tuple[str, str]:
    """'F01 Oleochemicals' -> ('F01', 'Oleochemicals')."""
    m = _FAMILY_RE.match(str(cell).strip())
    if not m:
        raise ValueError(f"Unparseable family cell: {cell!r}")
    return m.group(1), m.group(2).strip()


def _sheet_rows(wb, name: str) -> tuple[dict, list]:
    rows = list(wb[name].iter_rows(values_only=True))
    hdr = {col: i for i, col in enumerate(rows[0])}
    return hdr, [r for r in rows[1:] if any(v is not None for v in r)]


def parse_workbook(xlsx_path: Path) -> dict:
    """Parse the three tabs into plain dicts (no DB access)."""
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)

    hdr, rows = _sheet_rows(wb, "Families & Subfamilies")
    families: dict[str, str] = {}          # code -> name, first-appearance order
    subfamilies: list[dict] = []           # {family_code, name, formula_count}
    for r in rows:
        fcode, fname = parse_family_cell(r[hdr["Family"]])
        families.setdefault(fcode, fname)
        subfamilies.append({
            "family_code": fcode,
            "name": str(r[hdr["Subfamily"]]).strip(),
            "formula_count": r[hdr["# Formulas"]] or 0,
        })

    hdr, rows = _sheet_rows(wb, "Formulas")

    def col(r, *names):
        """First present column value among `names` (tolerates the old vs 2026-07
        Formulas layout, which renamed Form/Coverage and dropped Data confidence)."""
        for n in names:
            if n in hdr:
                return r[hdr[n]]
        return None

    formulas: list[dict] = []
    for r in rows:
        fcode, _ = parse_family_cell(r[hdr["Family"]])
        formulas.append({
            "code": str(r[hdr["Formula ID"]]).strip(),
            "name": str(r[hdr["Name"]]).strip(),
            "family_code": fcode,
            "form": col(r, "Form(s)", "Form (from ID)"),
            "coverage_tier": col(r, "Coverage (derived)", "Coverage tier"),
            "data_confidence": col(r, "Data confidence"),   # dropped in 2026-07 sheet -> None
            "region_count": col(r, "# Regions") or 0,
        })

    # Feeds via the Scrum 57 reader (same workbook shape). The 2026-07 sheet
    # dropped the per-feed "Formulas using it" list, so the feed->formula join
    # can't be expressed from it; leave used_by empty (validate() skips the join
    # checks when no feed carries link data — see there).
    feeds = sim._read_feeds(xlsx_path)
    hdr, rows = _sheet_rows(wb, "Indexes")
    has_used_by = "Formulas using it" in hdr
    for feed, r in zip(feeds, rows):
        used_by = r[hdr["Formulas using it"]] if has_used_by else None
        feed["used_by"] = [s.strip() for s in str(used_by).split(",") if s.strip()] if used_by else []
        feed["used_by_count"] = (r[hdr["# Formulas"]] or 0) if "# Formulas" in hdr else 0

    wb.close()
    return {"families": families, "subfamilies": subfamilies, "formulas": formulas, "feeds": feeds}


# ── Pre-import validation ─────────────────────────────────────────────────────

def validate(parsed: dict) -> tuple[list[str], list[str]]:
    """Structural + join validation. Errors block the load; warnings don't."""
    errors: list[str] = []
    warnings: list[str] = []
    families, subfamilies, formulas, feeds = (
        parsed["families"], parsed["subfamilies"], parsed["formulas"], parsed["feeds"])

    # Duplicate stable keys
    seen = set()
    for s in subfamilies:
        key = (s["family_code"], s["name"])
        if key in seen:
            errors.append(f"Duplicate subfamily: {key}")
        seen.add(key)
    formula_codes = [f["code"] for f in formulas]
    for code in {c for c in formula_codes if formula_codes.count(c) > 1}:
        errors.append(f"Duplicate formula id: {code}")
    feed_ids = [f["index_id"] for f in feeds]
    for fid in {i for i in feed_ids if feed_ids.count(i) > 1}:
        errors.append(f"Duplicate index id: {fid}")

    # Retired-list orphans must never come back
    for fid in feed_ids:
        if fid in RETIRED_ORPHANS:
            errors.append(f"Retired orphan index present: {fid} (re-exported from the dead index_list.html?)")

    # Formula -> family link
    for f in formulas:
        if f["family_code"] not in families:
            errors.append(f"Formula {f['code']} references unknown family {f['family_code']}")

    # Join validation both ways: a feed pricing a formula we don't have is a
    # bad reference; a formula no feed prices can never be priced — shout now.
    # Only runs when the source actually expresses the feed->formula links: the
    # 2026-07 workbook dropped the "Formulas using it" column, so used_by is
    # empty everywhere and there's nothing to cross-check (the link is asserted
    # by SEED-2's combos instead). Skipping avoids a false "all formulas unpriced".
    known = set(formula_codes)
    if any(feed["used_by"] for feed in feeds):
        referenced: set[str] = set()
        for feed in feeds:
            unknown = [c for c in feed["used_by"] if c not in known]
            if unknown:
                errors.append(f"{feed['index_id']} references unknown formula(s): {', '.join(sorted(unknown))}")
            if feed["used_by_count"] != len(feed["used_by"]):
                warnings.append(f"{feed['index_id']}: '# Formulas' says {feed['used_by_count']} "
                                f"but lists {len(feed['used_by'])}")
            referenced.update(feed["used_by"])
        unpriced = sorted(known - referenced)
        if unpriced:
            errors.append(f"{len(unpriced)} formula(s) not priced by any index feed: {', '.join(unpriced)}")
    else:
        warnings.append("Feed→formula links absent from this workbook "
                        "(no 'Formulas using it' column) — join-validation skipped.")

    # Count drift vs the reference drop (informational)
    combos = sum(f["region_count"] for f in formulas)
    actual = {"families": len(families), "subfamilies": len(subfamilies),
              "formulas": len(formulas), "feeds": len(feeds), "combos": combos}
    for k, exp in EXPECTED.items():
        if actual[k] != exp:
            warnings.append(f"Count drift: {k} = {actual[k]} (reference drop had {exp})")

    # Subfamily formula counts should roll up to each family's formula count
    per_family: dict[str, int] = {}
    for s in subfamilies:
        per_family[s["family_code"]] = per_family.get(s["family_code"], 0) + (s["formula_count"] or 0)
    from collections import Counter
    formula_per_family = Counter(f["family_code"] for f in formulas)
    for fcode in families:
        if per_family.get(fcode, 0) != formula_per_family.get(fcode, 0):
            warnings.append(f"{fcode}: subfamily formula counts sum to {per_family.get(fcode, 0)} "
                            f"but the Formulas tab has {formula_per_family.get(fcode, 0)}")

    return errors, warnings


# ── Diff + upsert (stable keys, update-in-place) ──────────────────────────────

class Tally:
    def __init__(self):
        self.created: list[str] = []
        self.updated: list[str] = []
        self.unchanged = 0
        self.stale: list[str] = []

    def line(self) -> str:
        s = f"{len(self.created)} created, {len(self.updated)} updated, {self.unchanged} unchanged"
        if self.stale:
            s += f", {len(self.stale)} stale (left in place)"
        return s


def _seed_user_id(db):
    uid = db.query(User.id).filter(User.email == SEED_USER_EMAIL).scalar()
    if uid is None:
        uid = db.query(User.id).filter(User.is_super_admin.is_(True)).order_by(User.created_at).limit(1).scalar()
    if uid is None:
        raise SystemExit(f"No seed user: neither {SEED_USER_EMAIL} nor any super-admin exists")
    return uid


def load_families(db, families: dict[str, str], dry_run: bool) -> tuple[Tally, dict[str, ChemicalFamily | None]]:
    tally = Tally()
    rows = db.query(ChemicalFamily).filter(ChemicalFamily.team_id.is_(None)).all()
    by_code = {r.code: r for r in rows if r.code}
    by_name = {r.name: r for r in rows}
    resolved: dict[str, ChemicalFamily | None] = {}
    for code, name in families.items():
        # Code is the stable key; name-match absorbs pre-existing code-less rows.
        row = by_code.get(code) or by_name.get(name)
        if row is None:
            tally.created.append(f"{code} {name}")
            if not dry_run:
                row = ChemicalFamily(code=code, name=name)
                db.add(row)
                db.flush()
        elif row.code != code or row.name != name:
            tally.updated.append(f"{code}: '{row.name}' -> '{name}'")
            if not dry_run:
                row.code, row.name = code, name
        else:
            tally.unchanged += 1
        resolved[code] = row
    source_codes, source_names = set(families), set(families.values())
    tally.stale = [f"{r.code or '?'} {r.name}" for r in rows
                   if r.code not in source_codes and r.name not in source_names]
    return tally, resolved


def load_subfamilies(db, subfamilies: list[dict], fam_rows: dict, dry_run: bool
                     ) -> tuple[Tally, dict[tuple[str, str], Subfamily | None]]:
    tally = Tally()
    resolved: dict[tuple[str, str], Subfamily | None] = {}
    source_keys = set()
    for s in subfamilies:
        key = (s["family_code"], s["name"])
        source_keys.add(key)
        fam = fam_rows.get(s["family_code"])
        row = None
        if fam is not None:
            row = db.query(Subfamily).filter(
                Subfamily.team_id.is_(None),
                Subfamily.family_id == fam.id,
                Subfamily.name == s["name"],
            ).first()
        if row is None:
            tally.created.append(f"{s['family_code']} / {s['name']}")
            if not dry_run:
                row = Subfamily(family_id=fam.id, name=s["name"])
                db.add(row)
                db.flush()
        else:
            # (family, name) IS the stable key, so there's nothing else to update.
            tally.unchanged += 1
        resolved[key] = row
    fam_ids = [f.id for f in fam_rows.values() if f is not None]
    if fam_ids:
        for r in db.query(Subfamily).filter(
                Subfamily.team_id.is_(None), Subfamily.family_id.in_(fam_ids)).all():
            fam_code = next((c for c, f in fam_rows.items() if f is not None and f.id == r.family_id), "?")
            if (fam_code, r.name) not in source_keys:
                tally.stale.append(f"{fam_code} / {r.name}")
    return tally, resolved


def load_formula_shells(db, formulas: list[dict], fam_rows: dict, dry_run: bool) -> Tally:
    tally = Tally()
    uid = None if dry_run else _seed_user_id(db)
    existing = {t.code: t for t in db.query(FormulaTemplate).filter(
        FormulaTemplate.team_id.is_(None), FormulaTemplate.code.isnot(None)).all()}
    source_codes = set()
    for f in formulas:
        source_codes.add(f["code"])
        fam = fam_rows.get(f["family_code"])
        meta = {
            "form": f["form"],
            "coverage_tier": f["coverage_tier"],
            "data_confidence": f["data_confidence"],
            "region_count": f["region_count"],
        }
        row = existing.get(f["code"])
        if row is None:
            tally.created.append(f["code"])
            if not dry_run:
                db.add(FormulaTemplate(
                    code=f["code"], name=f["name"], family_id=fam.id if fam else None,
                    catalog_meta=meta, created_by=uid, expression=None,
                ))
        else:
            fam_id = fam.id if fam is not None else None
            changes = []
            if row.name != f["name"]:
                changes.append(f"name '{row.name}' -> '{f['name']}'")
            if row.family_id != fam_id:
                changes.append("family")
            if (row.catalog_meta or {}) != meta:
                changes.append("catalog_meta")
            if changes:
                tally.updated.append(f"{f['code']}: {', '.join(changes)}")
                if not dry_run:
                    row.name, row.family_id, row.catalog_meta = f["name"], fam_id, meta
            else:
                tally.unchanged += 1
    tally.stale = sorted(c for c in existing if c not in source_codes)
    return tally


def load_indexes(db, feeds: list[dict], dry_run: bool) -> Tally:
    """Scrum 57 reconciliation (158 feeds -> region-agnostic commodities), but
    with value-compare so re-runs honestly report 'unchanged' rather than
    rewriting every row."""
    tally = Tally()
    commodities = sim.build_commodities(feeds)
    for name, meta in commodities.items():
        # Bad vocab fails loudly (same guard as seed_index_metadata). None is
        # tolerated for access_tier/role/frequency — the 2026-07 sheet dropped
        # those columns, so they arrive empty and the existing value is kept.
        assert meta["access_tier"] is None or meta["access_tier"] in sim.ACCESS_TIERS, meta["access_tier"]
        assert meta["frequency"] is None or meta["frequency"] in sim.FREQUENCIES, meta["frequency"]
        assert meta["role"] is None or meta["role"] in sim.ROLES, meta["role"]
        assert meta["retrieval_status"] in sim.RETRIEVAL_STATUSES, meta["retrieval_status"]

        ci = db.query(CommodityIndex).filter(func.lower(CommodityIndex.name) == name.lower()).first()
        target = {
            "category": meta["category"] or (ci.category if ci else None),
            "access_tier": meta["access_tier"] or (ci.access_tier if ci else None),
            "frequency": meta["frequency"] or (ci.frequency if ci else None),
            "role": meta["role"] or (ci.role if ci else None),
            "retrieval_status": meta["retrieval_status"],
            "free_source_name": meta["free_source_name"],
            "free_source_url": meta["free_source_url"],
            "proxy_logic": meta["proxy_logic"],
        }
        if ci is None:
            tally.created.append(name)
            if not dry_run:
                db.add(CommodityIndex(name=name, **target))
            continue
        changed = [k for k, v in target.items() if getattr(ci, k) != v]
        if changed:
            tally.updated.append(f"{name}: {', '.join(changed)}")
            if not dry_run:
                for k, v in target.items():
                    setattr(ci, k, v)
        else:
            tally.unchanged += 1
    return tally


# ── Entry point ───────────────────────────────────────────────────────────────

def run(db, xlsx_path: Path, dry_run: bool = False, verbose: bool = True) -> dict:
    parsed = parse_workbook(xlsx_path)
    errors, warnings = validate(parsed)

    def say(*a):
        if verbose:
            print(*a)

    say(f"== SEED-1 catalog load{' (dry run)' if dry_run else ''} — {xlsx_path.name} ==")
    for w in warnings:
        say(f"  warning: {w}")
    if errors:
        for e in errors:
            say(f"  ERROR: {e}")
        raise SystemExit(f"{len(errors)} validation error(s) — nothing was written")

    fam_tally, fam_rows = load_families(db, parsed["families"], dry_run)
    sub_tally, _ = load_subfamilies(db, parsed["subfamilies"], fam_rows, dry_run)
    for_tally = load_formula_shells(db, parsed["formulas"], fam_rows, dry_run)
    idx_tally = load_indexes(db, parsed["feeds"], dry_run)

    say(f"Families:    {len(parsed['families'])} in source -> {fam_tally.line()}")
    say(f"Subfamilies: {len(parsed['subfamilies'])} in source -> {sub_tally.line()}")
    say(f"Formulas:    {len(parsed['formulas'])} in source -> {for_tally.line()}")
    say(f"Indexes:     {len(parsed['feeds'])} feeds -> {len(sim.build_commodities(parsed['feeds']))} "
        f"commodities -> {idx_tally.line()}")
    for tally in (fam_tally, sub_tally, for_tally, idx_tally):
        for u in tally.updated:
            say(f"    ~ {u}")
        for st in tally.stale:
            say(f"    stale: {st}")
    return {
        "warnings": warnings,
        "families": fam_tally, "subfamilies": sub_tally,
        "formulas": for_tally, "indexes": idx_tally,
    }


def main():
    args = [a for a in sys.argv[1:]]
    dry_run = "--dry-run" in args
    path_args = [a for a in args if not a.startswith("--")]
    xlsx = resolve_workbook(path_args[0] if path_args else None)

    bypass_rls_var.set(True)  # platform reference data — no user context
    db = SessionLocal()
    try:
        run(db, xlsx, dry_run=dry_run)
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
