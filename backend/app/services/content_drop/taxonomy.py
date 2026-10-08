"""Taxonomy loader: both axes of the content drop (design §2.2, §2.5, §3.2).

Loads, as platform rows, in FK order:

    chemical_families        the axis families, by name
    subfamilies              the sub-family tier, by (family, name), renames
                             matched through the axis `former_names`
    product_lines            the named axis lines, by platform handle (PLAT-*)
    industries               the industries with their reference buyer
    category_shared (+ members)   the shared objects
    categories (+ members, refs, build items)   the ratified categories
    industry_out             the deliberate non-placements, `to` resolved
    category_placements      DERIVED: (category, product), rebuilt every run

**A product line is its platform handle.** Laurent renames lines; the handle
stays. So a line is matched by `platform`, then by `line_key`, then by a key
in its stored `former_keys`. A rename updates `name` and `line_key` in place
and adds the old key to `former_keys`. The axis `former_names` (and
`former_name`) become `former_keys` too, as `Family|||old name`.

**What is not a row.** Axis lines left deliberately unnamed are skipped and
logged (never given a name). Record-level line keys that are not axis lines
are not rows either: their products get no line (the catalogue loader keeps
the record key in `internal_meta`). `retired_lines`, `retired_keys` and
`unplaced_products` are logged, not stored. The axis `products[]` list is
never read as membership (it lags the cards); a product's line is its
record-level `FORMULA_COMBOS[pid].family|||subfamily` key.

**A line whose platform left the axis** gets `retired_at` (hidden, kept:
team data may point at it); a line that comes back is cleared. A sub-family
the axis no longer names is deleted when no line sits on it, else reported.
A family is never deleted.

**Placements are derived.** For every category: its members (`"*"` expanded
to the products whose record-level line is the member's line), the members of
every shared object it references, then the products a chain entry
(`category.lines[].pids`) names. The tree wins over `FUNCTIONALITY`;
`check_functionality` compares the two and the load report names every
product where they differ. A difference outside `KNOWN_FUNCTIONALITY_RESIDUALS`
is reported as a warning.

**Out rows** keep the raw `to` text and resolve it into `to_industries`: the
whole text when it is one industry name (a name may contain a comma),
otherwise the industry names found in it, longest first, split only on the
separators between them; a part that names no industry is kept as text.

Idempotent by comparison: each tier reads what is there, writes only what
differs, and reports created / updated / unchanged. A second run reports zero
changes. Authored child rows (category members, shared members, refs, build
items, out rows) belong to a parent the drop states in full, so a row the
parent no longer lists is deleted; placements follow the tree the same way.

**Never commits.** The CLI owns the transaction (`seed_content_drop.py`).
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any, Callable

from sqlalchemy import inspect as sa_inspect, null
from sqlalchemy.orm import Session
from sqlalchemy.types import JSON

from app.models.chemical_family import ChemicalFamily
from app.models.formula_template import FormulaTemplate
from app.models.product_line import ProductLine
from app.models.subfamily import Subfamily
from app.models.taxonomy_v2 import (
    CATEGORY_STATUSES, Category, CategoryBuildItem, CategoryMember, CategoryPlacement,
    CategoryRef, CategoryShared, CategorySharedMember, Industry, IndustryOut,
)
from app.services.drop.report import LoadReport, TableDiff
from app.services.content_drop.reader import (
    LINE_KEY_SEP, axis, industries as industries_v2, line_key_of, make_line_key,
    pids_on_line, raw, scope, tree,
)

# The authored keys that get their own column (or their own table). Everything
# else an entry carries goes, as given, into the row's JSONB column.
_FAMILY_COLUMNS = ("family", "subfamilies")
_SUBFAMILY_COLUMNS = ("name", "why", "lines", "former_names")
# The line keys that become `product_lines.flags`.
LINE_FLAG_KEYS = ("status", "route_flag", "validation_question", "retracted", "do_not_publish")
# `products` is left out on purpose: it is not membership (see the docstring).
_LINE_COLUMNS = ("name", "platform", "confidence", "products") + LINE_FLAG_KEYS
_TREE_INDUSTRY_COLUMNS = ("status", "ratified", "categories", "out")
_V2_INDUSTRY_COLUMNS = ("industry", "slug", "anchor", "status", "buyer_one_line", "buyer",
                        "in_scope", "out_of_scope", "boundaries")
_CATEGORY_COLUMNS = ("id", "name", "alias", "fn", "status", "note", "src",
                     "members", "ref", "build")
_SHARED_COLUMNS = ("name", "fn", "owner", "why", "status", "note", "members")
_MEMBER_COLUMNS = ("line", "pids", "why")
_OUT_COLUMNS = ("pid", "code", "why", "to", "to_was")

# Report rows that carry checks or logs, not writes.
FUNCTIONALITY_CHECK = "check: FUNCTIONALITY"
AXIS_LOG = "log: supply axis"

# Products whose tree placements are known to differ from FUNCTIONALITY: the
# tree is the newer statement (a dated member note, a `"*"` member that now
# reaches the product, a chain entry FUNCTIONALITY does not mirror). Any other
# difference is reported as a warning.
KNOWN_FUNCTIONALITY_RESIDUALS = frozenset({
    "AGR-UREA-GRN", "BCI-DEHA85-LIQ", "BCI-MAAGLAC-LIQ", "HMO-6SLNA-PWD",
})


# ── Shared helpers (content, reports and playbooks import these) ─────────────

def _diff(report: LoadReport, name: str) -> TableDiff:
    """Get-or-create a table row on the report (`LoadReport.table` is only a
    lookup)."""
    existing = report.table(name)
    if existing is not None:
        return existing
    row = TableDiff(table=name)
    report.tables.append(row)
    return row


def _rest(entry: dict, columns: tuple[str, ...]) -> dict | None:
    """The authored keys that have no column, as given. An empty dict is
    stored as NULL, so the stored value is the same on every run."""
    rest = {k: v for k, v in entry.items() if k not in columns}
    return rest or None


@lru_cache(maxsize=None)
def _json_attrs(model) -> frozenset[str]:
    return frozenset(a.key for a in sa_inspect(model).column_attrs
                     if isinstance(a.columns[0].type, JSON))


def _stored(model, name: str, value):
    """The value to write. A bare None on a JSON(B) column is stored as the
    JSON literal `null`, which `IS NULL` does not match, so write SQL NULL
    (a `"*"` member must read `pids IS NULL`)."""
    return null() if value is None and name in _json_attrs(model) else value


def _sync(row, fields: dict) -> bool:
    """Set only the fields that differ, so an unchanged row stays clean and a
    second run reports it unchanged. True if anything changed."""
    changed = False
    for name, value in fields.items():
        if getattr(row, name) != value:
            setattr(row, name, _stored(type(row), name, value))
            changed = True
    return changed


def _upsert(db: Session, diff: TableDiff, current: dict, key, fields: dict,
            make: Callable[[dict], Any]):
    """Create the row for `key`, or bring it in line with `fields`."""
    row = current.get(key)
    if row is None:
        row = make(fields)
        for name in _json_attrs(type(row)) & fields.keys():
            setattr(row, name, _stored(type(row), name, fields[name]))
        db.add(row)
        current[key] = row
        diff.created += 1
    elif _sync(row, fields):
        diff.updated += 1
    else:
        diff.unchanged += 1
    return row


def _sync_children(db: Session, diff: TableDiff, current: dict, desired: dict,
                   make: Callable[[Any, dict], Any]) -> None:
    """Make one parent's child rows exactly `desired` (key → fields): upsert
    every desired key, delete every other row. For child rows the parent
    states in full (see the module docstring for why they are deleted)."""
    for key, fields in desired.items():
        _upsert(db, diff, current, key, fields, lambda f, k=key: make(k, f))
    for key in [k for k in current if k not in desired]:
        db.delete(current.pop(key))
        diff.deleted += 1


def _members(entries: list[dict] | None) -> list[dict]:
    """An authored member list as row fields. `"*"` means the whole line:
    `is_whole_line` and `pids` NULL."""
    rows = []
    for m in entries or []:
        whole = m["pids"] == "*"
        rows.append({
            "line_key": m["line"],
            "pids": None if whole else list(m["pids"]),
            "is_whole_line": whole,
            "why": m.get("why"),
            "extra": _rest(m, _MEMBER_COLUMNS),
        })
    return rows


def _former_names(node: dict) -> list[str]:
    """A node's earlier names: `former_names` (a list) and `former_name`."""
    names = list(node.get("former_names") or [])
    if node.get("former_name"):
        names.append(node["former_name"])
    return [n for n in names if isinstance(n, str) and n.strip()]


def _merge_former(*groups) -> list | None:
    """One sorted, de-duplicated list (NULL when empty), so a second run
    computes the identical value."""
    out = {v for group in groups for v in (group or ()) if v}
    return sorted(out) or None


# ── Supply axis: families and sub-families ───────────────────────────────────

def _load_families(db: Session, report: LoadReport) -> dict[str, ChemicalFamily]:
    """The axis families, matched by name. A family the axis no longer names
    is reported stale and kept (never deleted)."""
    diff = _diff(report, "chemical_families")
    current = {r.name: r for r in db.query(ChemicalFamily)}
    nodes = axis()["families"]
    for pos, fam in enumerate(nodes):
        name = fam["family"]
        fields = {"sort_order": pos, "meta": _rest(fam, _FAMILY_COLUMNS)}
        _upsert(db, diff, current, name, fields, lambda f, n=name: ChemicalFamily(name=n, **f))
    wanted = [f["family"] for f in nodes]
    diff.stale += len(set(current) - set(wanted))
    db.flush()
    return {name: current[name] for name in wanted}


def _load_subfamilies(db: Session, report: LoadReport, families: dict[str, ChemicalFamily]
                      ) -> tuple[dict[tuple[str, str | None], Subfamily], list[Subfamily]]:
    """The sub-family nodes, keyed (family, name).

    Two passes. First every node takes the stored row with its own (family,
    name). Then a node left over takes an unclaimed stored row of the same
    family that it names in its `former_names`, or that lists the node's name
    among its own former names (a rename back). A matched rename keeps the
    row's id, takes the new name, and the old name joins `former_names`. The
    one unnamed node keys on `name` None, which matches `name IS NULL`.

    Returns ({(family, name): row}, the stored rows the axis no longer names).
    """
    diff = _diff(report, "subfamilies")
    rows = db.query(Subfamily).all()
    by_key = {(r.family_id, r.name): r for r in rows}
    nodes = [(fam["family"], families[fam["family"]], pos, node)
             for fam in axis()["families"] for pos, node in enumerate(fam["subfamilies"])]

    matched: dict[int, Subfamily] = {}
    claimed: set[int] = set()
    for i, (fname, family, _pos, node) in enumerate(nodes):
        row = by_key.get((family.id, node.get("name")))
        if row is not None and id(row) not in claimed:
            matched[i] = row
            claimed.add(id(row))
    for i, (fname, family, _pos, node) in enumerate(nodes):
        if i in matched:
            continue
        name, formers = node.get("name"), set(_former_names(node))
        row = next((r for r in rows if id(r) not in claimed and r.family_id == family.id
                    and (r.name in formers or (name and name in (r.former_names or [])))), None)
        if row is not None:
            matched[i] = row
            claimed.add(id(row))

    out: dict[tuple[str, str | None], Subfamily] = {}
    for i, (fname, family, pos, node) in enumerate(nodes):
        name = node.get("name")
        if (fname, name) in out:
            diff.skipped.append((f"{fname} / {name}", "named twice in the axis; "
                                                      "second node not loaded"))
            continue
        row = matched.get(i)
        renamed_from = row.name if row is not None and row.name != name else None
        former = _merge_former(_former_names(node), row.former_names if row is not None else None,
                               [renamed_from] if renamed_from else None)
        fields = {
            "family_id": family.id,
            "name": name,
            "why": node.get("why"),
            "former_names": [n for n in former or [] if n != name] or None,
            "sort_order": pos,
            "meta": _rest(node, _SUBFAMILY_COLUMNS),
        }
        if row is None:
            row = Subfamily(**{k: v for k, v in fields.items()
                               if k not in _json_attrs(Subfamily)})
            for k in _json_attrs(Subfamily) & fields.keys():
                setattr(row, k, _stored(Subfamily, k, fields[k]))
            db.add(row)
            diff.created += 1
        elif _sync(row, fields):
            diff.updated += 1
            if renamed_from:
                diff.skipped.append((f"{fname} / {name}", f"renamed from {renamed_from!r} "
                                                          "(same row; old name kept)"))
        else:
            diff.unchanged += 1
        out[(fname, name)] = row
    db.flush()
    gone = [r for r in rows if id(r) not in claimed]
    return out, gone


def _drop_gone_subfamilies(db: Session, report: LoadReport, gone: list[Subfamily]) -> None:
    """A sub-family the axis no longer names: deleted when no line sits on it
    (after the lines moved), else reported and kept."""
    diff = _diff(report, "subfamilies")
    if not gone:
        return
    used = {sid for (sid,) in db.query(ProductLine.subfamily_id).filter(
        ProductLine.subfamily_id.in_([r.id for r in gone]))}
    for row in gone:
        if row.id in used:
            diff.stale += 1
            diff.skipped.append((f"{row.family_id} / {row.name}",
                                 "no longer in the axis but a line still sits on it; kept"))
        else:
            db.delete(row)
            diff.deleted += 1
    db.flush()


# ── Supply axis: product lines ───────────────────────────────────────────────

def _desired_lines(report: LoadReport) -> list[dict]:
    """Every named axis line as {key, family, subfamily, platform, fields},
    in axis order. Unnamed lines are skipped and logged."""
    diff = _diff(report, "product_lines")
    out: list[dict] = []
    for fam in axis()["families"]:
        pos = 0
        for node in fam["subfamilies"]:
            for line in node["lines"]:
                if not line.get("name"):
                    diff.skipped.append((
                        f"{fam['family']} / {line.get('platform')}",
                        "axis line deliberately unnamed: not a row (a name is never inferred); "
                        "its products keep no line"))
                    continue
                key = make_line_key(fam["family"], line["name"])
                flags = {k: line[k] for k in LINE_FLAG_KEYS if k in line}
                out.append({
                    "key": key, "family": fam["family"], "subfamily": node.get("name"),
                    "platform": line.get("platform"),
                    "former_keys": [n if LINE_KEY_SEP in n else make_line_key(fam["family"], n)
                                    for n in _former_names(line)],
                    "fields": {
                        "name": line["name"],
                        "line_key": key,
                        "platform": line.get("platform"),
                        "confidence": line.get("confidence"),
                        "flags": flags or None,
                        "axis_meta": _rest(line, _LINE_COLUMNS),
                        "sort_order": pos,
                    },
                })
                pos += 1
    return out


def _log_axis(report: LoadReport) -> None:
    """What the axis says but no table stores: retired lines and keys, and the
    products the axis has not placed yet."""
    log = _diff(report, AXIS_LOG)
    doc = axis()
    retired = doc.get("retired_lines") or []
    if retired:
        log.skipped.append(("retired_lines", f"{len(retired)} retired axis lines, not stored"))
    keys = doc.get("retired_keys") or []
    if keys:
        log.skipped.append(("retired_keys", f"{len(keys)} retired line keys, not stored"))
    unplaced = doc.get("unplaced_products") or []
    if unplaced:
        log.skipped.append(("unplaced_products", "no product line yet: "
                            + ", ".join(sorted(str(u.get("pid")) for u in unplaced))))


def _load_lines(db: Session, report: LoadReport, families: dict[str, ChemicalFamily],
                subfamilies: dict[tuple[str, str | None], Subfamily]) -> dict[str, ProductLine]:
    """The named axis lines into `product_lines`.

    Matching, in passes so an exact match is never stolen by a weaker one:
    the platform handle; then the line key; then a key in a stored line's
    `former_keys` (only when exactly one line claims it); then a stored line
    whose key the axis lists among this line's former names. A matched row
    keeps its id; a new name moves the old key into `former_keys`. A line that
    now sits under another family or sub-family is updated in place, family
    and sub-family together (the composite key forbids a mismatch).

    `in_v1_scope` and `report_old_line` come from `scope/v1_scope.json` and
    are written on every line (False / NULL outside the scope). `report_slug`
    is the reports loader's own rule (`reports.derive_line_report_slugs`),
    written on every line, so neither loader undoes the other on a second run.
    """
    # Imported here: reports.py imports this module's helpers at load time.
    from app.services.content_drop.reports import derive_line_report_slugs

    diff = _diff(report, "product_lines")
    desired = _desired_lines(report)
    line_slugs, _ = derive_line_report_slugs()
    v1 = {entry["line"]: entry for entry in scope().get("lines") or []}

    rows = db.query(ProductLine).all()
    by_platform = {r.platform: r for r in rows if r.platform}
    by_key = {r.line_key: r for r in rows}
    by_former: dict[str, list[ProductLine]] = defaultdict(list)
    for r in rows:
        for old in r.former_keys or []:
            by_former[old].append(r)

    matched: dict[int, ProductLine] = {}
    claimed: set[int] = set()

    def claim(i: int, row: ProductLine | None) -> None:
        if row is not None and id(row) not in claimed and i not in matched:
            matched[i] = row
            claimed.add(id(row))

    for i, spec in enumerate(desired):
        if spec["platform"]:
            claim(i, by_platform.get(spec["platform"]))
    for i, spec in enumerate(desired):
        claim(i, by_key.get(spec["key"]))
    for i, spec in enumerate(desired):
        candidates = [r for r in by_former.get(spec["key"], []) if id(r) not in claimed]
        if len(candidates) == 1:
            claim(i, candidates[0])
    for i, spec in enumerate(desired):
        for old in spec["former_keys"]:
            claim(i, by_key.get(old))

    out: dict[str, ProductLine] = {}
    for i, spec in enumerate(desired):
        key = spec["key"]
        if key in out:
            diff.skipped.append((key, "named twice in the axis; second line not loaded"))
            continue
        row = matched.get(i)
        family = families[spec["family"]]
        subfamily = subfamilies.get((spec["family"], spec["subfamily"]))
        scoped = v1.get(key) or next((v1[k] for k in spec["former_keys"] if k in v1), None)
        renamed_from = row.line_key if row is not None and row.line_key != key else None
        former = _merge_former(spec["former_keys"], row.former_keys if row is not None else None,
                               [renamed_from] if renamed_from else None)
        fields = {
            **spec["fields"],
            "family_id": family.id,
            "subfamily_id": subfamily.id if subfamily is not None else None,
            "former_keys": [k for k in former or [] if k != key] or None,
            "in_v1_scope": scoped is not None,
            "report_slug": line_slugs.get(key),
            "report_old_line": scoped.get("report_old_line") if scoped else None,
            "retired_at": None,
        }
        if row is None:
            row = ProductLine(**{k: v for k, v in fields.items()
                                 if k not in _json_attrs(ProductLine)})
            for k in _json_attrs(ProductLine) & fields.keys():
                setattr(row, k, _stored(ProductLine, k, fields[k]))
            db.add(row)
            diff.created += 1
        elif _sync(row, fields):
            diff.updated += 1
            if renamed_from:
                diff.skipped.append((key, f"renamed from {renamed_from} (same row; old key "
                                          "kept in former_keys)"))
        else:
            diff.unchanged += 1
        out[key] = row
    db.flush()

    for key in v1:
        if key not in out and not any(key in (r.former_keys or []) for r in out.values()):
            diff.skipped.append((key, "v1 scope line is not a product line in this drop"))

    # A stored line whose platform left the axis: hidden, never deleted.
    now = datetime.now(timezone.utc)
    for row in rows:
        if id(row) in claimed:
            continue
        diff.stale += 1
        if row.retired_at is None:
            row.retired_at = now
            diff.updated += 1
            diff.skipped.append((row.line_key, "platform no longer in the axis; retired_at set"))
    db.flush()
    return out


def line_index(db: Session) -> dict[str, int]:
    """line key → `product_lines.id` for every current (not retired) line,
    plus each former key that exactly one current line claims and that is not
    itself a current key. The catalogue and the placements resolve record and
    tree keys through it."""
    current: dict[str, int] = {}
    claims: dict[str, set[int]] = defaultdict(set)
    for lid, key, formers in db.query(ProductLine.id, ProductLine.line_key,
                                      ProductLine.former_keys).filter(
            ProductLine.retired_at.is_(None)):
        current[key] = lid
        for old in formers or []:
            claims[old].add(lid)
    out = dict(current)
    for old, ids in claims.items():
        if old not in current and len(ids) == 1:
            out[old] = next(iter(ids))
    return out


# ── Demand axis ──────────────────────────────────────────────────────────────

def _load_industries(db: Session, report: LoadReport) -> dict[str, Industry]:
    """The industries: ratification state from the tree, the reference buyer
    from `scope/industries_v2.json`."""
    diff = _diff(report, "industries")
    buyers = {b["industry"]: b for b in industries_v2()}
    current = {r.name: r for r in db.query(Industry)}
    branches = tree()["industries"]
    for pos, (name, branch) in enumerate(branches.items()):
        buyer = buyers.get(name)
        if buyer is None:
            diff.skipped.append((name, "no reference buyer in industries_v2.json (slug unknown)"))
            continue
        ratified = branch.get("ratified")
        meta = {**(_rest(branch, _TREE_INDUSTRY_COLUMNS) or {}),
                **(_rest(buyer, _V2_INDUSTRY_COLUMNS) or {})}
        fields = {
            "slug": buyer["slug"],
            "anchor": buyer.get("anchor"),
            "status": branch.get("status"),
            "ratified_on": date.fromisoformat(ratified) if ratified else None,
            "scope_status": buyer.get("status"),
            "buyer_one_line": buyer.get("buyer_one_line"),
            "buyer": buyer.get("buyer"),
            "in_scope": buyer.get("in_scope"),
            "out_of_scope": buyer.get("out_of_scope"),
            "boundaries": buyer.get("boundaries"),
            "meta": meta or None,
            "sort_order": pos,
        }
        _upsert(db, diff, current, name, fields, lambda f, n=name: Industry(name=n, **f))
    diff.stale += len(set(current) - set(branches))
    db.flush()
    return {name: current[name] for name in branches if name in current}


def _load_shared(db: Session, report: LoadReport) -> dict[str, CategoryShared]:
    """The shared objects and their authored member rows."""
    diff = _diff(report, "category_shared")
    mdiff = _diff(report, "category_shared_members")
    shared = tree()["shared"]
    current = {r.code: r for r in db.query(CategoryShared)}
    for pos, (code, obj) in enumerate(shared.items()):
        fields = {
            "name": obj["name"], "fn": obj.get("fn"), "owner": obj.get("owner"),
            "why": obj.get("why"), "status": obj.get("status"), "note": obj.get("note"),
            "extra": _rest(obj, _SHARED_COLUMNS), "sort_order": pos,
        }
        _upsert(db, diff, current, code, fields, lambda f, c=code: CategoryShared(code=c, **f))
    diff.stale += len(set(current) - set(shared))
    db.flush()

    children: dict[int, dict] = defaultdict(dict)
    for row in db.query(CategorySharedMember):
        children[row.shared_id][row.sort_order] = row
    for code, obj in shared.items():
        parent = current[code]
        desired = {
            pos: {k: m[k] for k in ("line_key", "pids", "is_whole_line")}
            for pos, m in enumerate(_members(obj.get("members")))
        }
        _sync_children(db, mdiff, children[parent.id], desired,
                       lambda pos, f, sid=parent.id: CategorySharedMember(
                           shared_id=sid, sort_order=pos, **f))
    db.flush()
    return {code: current[code] for code in shared}


def _load_categories(db: Session, report: LoadReport, industries: dict[str, Industry],
                     shared: dict[str, CategoryShared]) -> dict[str, Category]:
    """The categories with every authored key, plus their members, refs and
    build items, and each industry's `out` rows."""
    diff = _diff(report, "categories")
    branches = tree()["industries"]
    current = {r.code: r for r in db.query(Category)}
    wanted: dict[str, dict] = {}
    for name, branch in branches.items():
        industry = industries.get(name)
        if industry is None:
            continue  # skipped (and said why) by _load_industries
        for pos, cat in enumerate(branch.get("categories") or []):
            code = cat["id"]
            if cat.get("status") not in CATEGORY_STATUSES:
                diff.skipped.append((code, f"status {cat.get('status')!r} is not one of "
                                           f"{', '.join(CATEGORY_STATUSES)}"))
                continue
            if code in wanted:
                diff.skipped.append((code, "category code appears twice in the tree"))
                continue
            wanted[code] = cat
            fields = {
                "industry_id": industry.id, "name": cat["name"], "alias": cat.get("alias"),
                "fn": cat.get("fn"), "status": cat["status"], "note": cat.get("note"),
                "src": cat.get("src"), "extra": _rest(cat, _CATEGORY_COLUMNS),
                "sort_order": pos,
            }
            _upsert(db, diff, current, code, fields, lambda f, c=code: Category(code=c, **f))
    gone = [c for c in current if c not in wanted]
    for code in gone:
        # A gone category is deleted (§1.4): nothing team-side points at it,
        # and its members, refs, build items and placements cascade.
        db.delete(current.pop(code))
        diff.deleted += 1
    db.flush()

    _load_category_children(db, report, {c: current[c] for c in wanted}, wanted, shared)
    _load_out(db, report, industries)
    return {code: current[code] for code in wanted}


def _load_category_children(db: Session, report: LoadReport, categories: dict[str, Category],
                            authored: dict[str, dict], shared: dict[str, CategoryShared]) -> None:
    mdiff = _diff(report, "category_members")
    rdiff = _diff(report, "category_refs")
    bdiff = _diff(report, "category_build_items")

    members: dict[int, dict] = defaultdict(dict)
    for row in db.query(CategoryMember):
        members[row.category_id][row.sort_order] = row
    refs: dict[int, dict] = defaultdict(dict)
    for row in db.query(CategoryRef):
        refs[row.category_id][row.shared_id] = row
    builds: dict[int, dict] = defaultdict(dict)
    for row in db.query(CategoryBuildItem):
        builds[row.category_id][row.seq] = row

    for code, cat in authored.items():
        cid = categories[code].id
        _sync_children(
            db, mdiff, members[cid], dict(enumerate(_members(cat.get("members")))),
            lambda pos, f, cid=cid: CategoryMember(category_id=cid, sort_order=pos, **f))

        desired_refs: dict[int, dict] = {}
        for pos, ref in enumerate(cat.get("ref") or []):
            target = shared.get(ref)
            if target is None:
                rdiff.skipped.append((f"{code} → {ref}", "not a shared object in the tree"))
            elif target.id in desired_refs:
                rdiff.skipped.append((f"{code} → {ref}", "referenced twice"))
            else:
                # Refs are bare codes: the whole shared object (pids NULL).
                desired_refs[target.id] = {"pids": None, "sort_order": pos}
        _sync_children(db, rdiff, refs[cid], desired_refs,
                       lambda sid, f, cid=cid: CategoryRef(category_id=cid, shared_id=sid, **f))

        _sync_children(
            db, bdiff, builds[cid],
            {seq: {"text": text} for seq, text in enumerate(cat.get("build") or [])},
            lambda seq, f, cid=cid: CategoryBuildItem(category_id=cid, seq=seq, **f))
    db.flush()


_OUT_SEPARATOR = re.compile(r"\s*[,;]\s*")


def split_out_target(text: str | None, names) -> list[str] | None:
    """An out row's `to`, resolved to industry names (design §2.5).

    1. The whole text, when it is one industry name (some names contain a
       comma, so a plain split would break them).
    2. Otherwise the industry names found in it, matched longest first at
       each position, split only on the separators between them.
    3. A part that names no industry is kept as text.

    None when there is no `to`.
    """
    if text is None or not text.strip():
        return None
    known = set(names)
    whole = text.strip()
    if whole in known:
        return [whole]
    longest_first = sorted(known, key=len, reverse=True)
    parts: list[str] = []
    pos = 0
    while pos < len(whole):
        sep = _OUT_SEPARATOR.match(whole, pos)
        if sep:
            pos = sep.end()
            continue
        hit = next((n for n in longest_first if whole.startswith(n, pos)), None)
        if hit is not None:
            parts.append(hit)
            pos += len(hit)
            continue
        nxt = _OUT_SEPARATOR.search(whole, pos)
        end = nxt.start() if nxt else len(whole)
        piece = whole[pos:end].strip()
        if piece:
            parts.append(piece)
        pos = end
    return parts or None


def _load_out(db: Session, report: LoadReport, industries: dict[str, Industry]) -> None:
    """Each industry's `out` rows: a product it deliberately does not buy, and
    the industry that does (`to`, kept raw and resolved)."""
    diff = _diff(report, "industry_out")
    names = list(tree()["industries"])
    existing: dict[int, dict] = defaultdict(dict)
    for row in db.query(IndustryOut):
        existing[row.industry_id][row.pid] = row
    for name, branch in tree()["industries"].items():
        industry = industries.get(name)
        if industry is None:
            continue
        desired: dict[str, dict] = {}
        for o in branch.get("out") or []:
            if o["pid"] in desired:
                diff.skipped.append((f"{name} / {o['pid']}", "out row listed twice"))
                continue
            targets = split_out_target(o.get("to"), names)
            for part in targets or []:
                if part not in industries:
                    diff.skipped.append((f"{name} / {o['pid']}",
                                         "a `to` part names no industry; kept as text"))
            desired[o["pid"]] = {
                "code": o.get("code"), "why": o.get("why"), "to_industry": o.get("to"),
                "to_industries": targets, "to_was": o.get("to_was"),
                "meta": _rest(o, _OUT_COLUMNS),
            }
        _sync_children(db, diff, existing[industry.id], desired,
                       lambda pid, f, iid=industry.id: IndustryOut(industry_id=iid, pid=pid, **f))
    db.flush()


# ── Placements (derived) ─────────────────────────────────────────────────────

def derive_placements(doc: dict | None = None) -> dict[tuple[str, str], dict]:
    """The placement projection of the tree, without a database.

    (category code, pid) → {industry, category, fn, name, line_key, via}.
    Members first, then each referenced shared object in `ref` order (`"*"`
    expands to every product whose record-level line is the member's line),
    then the products a chain entry (`lines[].pids`) names. `via` is the
    shared object that supplied the product, or None. `line_key` is the
    member's authored line, or for a chain product its record-level line. The
    first source wins.

    A chain entry whose `pids` is not a list adds nothing: a chain entry
    names a feedstock, not a product line, so there is no line to expand a
    wildcard on.
    """
    doc = doc or tree()
    shared = doc["shared"]
    out: dict[tuple[str, str], dict] = {}
    for industry, branch in doc["industries"].items():
        for cat in branch.get("categories") or []:
            def add(pid: str, line_key: str | None, via: str | None) -> None:
                out.setdefault((cat["id"], pid), {
                    "industry": industry, "category": cat["id"], "fn": cat.get("fn"),
                    "name": cat["name"], "line_key": line_key, "via": via,
                })

            sources = [(None, m) for m in cat.get("members") or []]
            for ref in cat.get("ref") or []:
                sources += [(ref, m) for m in (shared.get(ref) or {}).get("members") or []]
            for via, member in sources:
                pids = pids_on_line(member["line"]) if member["pids"] == "*" else member["pids"]
                for pid in pids:
                    add(pid, member["line"], via)
            for chain in cat.get("lines") or []:
                if isinstance(chain, dict) and isinstance(chain.get("pids"), list):
                    for pid in chain["pids"]:
                        add(pid, line_key_of(pid), None)
    return out


def _load_placements(db: Session, report: LoadReport, industries: dict[str, Industry],
                     categories: dict[str, Category], shared: dict[str, CategoryShared]) -> None:
    diff = _diff(report, "category_placements")
    projection = derive_placements()
    pids = {pid for _code, pid in projection}
    templates = dict(db.query(FormulaTemplate.code, FormulaTemplate.id).filter(
        FormulaTemplate.team_id.is_(None), FormulaTemplate.code.in_(pids)))
    lines = line_index(db)

    desired: dict[tuple[int, str], dict] = {}
    for (code, pid), p in projection.items():
        category = categories.get(code)
        industry = industries.get(p["industry"])
        if category is None or industry is None:
            diff.skipped.append((f"{code} / {pid}", "category not loaded"))
            continue
        desired[(category.id, pid)] = {
            "industry_id": industry.id,
            "line_key": p["line_key"],
            # A key that is not a current line (an unpublished or legacy
            # key) resolves to no line.
            "product_line_id": lines.get(p["line_key"]) if p["line_key"] else None,
            "template_id": templates.get(pid),
            "fn": p["fn"],
            "name": p["name"],
            "via_shared_id": shared[p["via"]].id if p["via"] else None,
        }
    current = {(r.category_id, r.pid): r for r in db.query(CategoryPlacement)}
    _sync_children(db, diff, current, desired,
                   lambda key, f: CategoryPlacement(category_id=key[0], pid=key[1], **f))
    db.flush()


def check_functionality(db: Session) -> dict:
    """Compare the stored placements with `raw/FUNCTIONALITY.json`, per
    product, on the {industry, fn, name, category} set.

    Returns `{pids, entries, expected_pids, expected_entries, matched,
    mismatched}`: `matched` counts the FUNCTIONALITY products whose set is
    identical, `mismatched` maps each differing pid to `(missing, extra)`.
    """
    expected: dict[str, set] = {
        pid: {(e["industry"], e["fn"], e["name"], e["category"]) for e in entries}
        for pid, entries in raw("FUNCTIONALITY").items()
    }
    stored: dict[str, set] = defaultdict(set)
    rows = (db.query(CategoryPlacement.pid, Industry.name, CategoryPlacement.fn,
                     CategoryPlacement.name, Category.code)
            .join(Category, CategoryPlacement.category_id == Category.id)
            .join(Industry, CategoryPlacement.industry_id == Industry.id))
    for pid, industry, fn, name, code in rows:
        stored[pid].add((industry, fn, name, code))
    mismatched = {}
    for pid in sorted(set(expected) | set(stored)):
        want, have = expected.get(pid, set()), stored.get(pid, set())
        if want != have:
            mismatched[pid] = (sorted(want - have, key=str), sorted(have - want, key=str))
    return {
        "pids": len(stored),
        "entries": sum(len(v) for v in stored.values()),
        "expected_pids": len(expected),
        "expected_entries": sum(len(v) for v in expected.values()),
        "matched": sum(1 for pid in expected if pid not in mismatched),
        "mismatched": mismatched,
    }


def _report_functionality(db: Session, report: LoadReport) -> None:
    """The comparison as a report row: `unchanged` = products whose placements
    match FUNCTIONALITY; one skip line per product that does not, a warning
    when it is not a known residual."""
    diff = _diff(report, FUNCTIONALITY_CHECK)
    result = check_functionality(db)
    diff.unchanged = result["matched"]
    for pid, (missing, extra) in result["mismatched"].items():
        known = pid in KNOWN_FUNCTIONALITY_RESIDUALS
        diff.skipped.append((pid, ("" if known else "WARNING: unexpected; ")
                             + f"tree placements differ from FUNCTIONALITY: "
                               f"{len(missing)} only there, {len(extra)} only in the tree"))


# ── Entry point ──────────────────────────────────────────────────────────────

def load(db: Session, report: LoadReport) -> LoadReport:
    """Load both axes and rebuild the placements. Flushes, never commits."""
    families = _load_families(db, report)
    subfamilies, gone = _load_subfamilies(db, report, families)
    _load_lines(db, report, families, subfamilies)
    _drop_gone_subfamilies(db, report, gone)
    _log_axis(report)
    industries = _load_industries(db, report)
    shared = _load_shared(db, report)
    categories = _load_categories(db, report, industries, shared)
    _load_placements(db, report, industries, categories, shared)
    _report_functionality(db, report)
    return report
