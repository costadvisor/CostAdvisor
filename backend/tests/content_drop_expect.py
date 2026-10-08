"""Expected content figures, derived from the drop files alone.

The tests' independent reading of the content drop (design §5.6, WP-1b). It
answers "what should the database hold after a fresh load of this drop?" from
the JSON files in the drop directory and nothing else:

* no database, no app settings, no loader code (it imports nothing from `app`),
  so a test that compares a loaded database with these functions is a real
  check of the loaders, not a mirror of them;
* standard library only; the drop is read as data and never executed;
* functions, not numbers. Nothing here hard-codes a count: every figure is
  walked from the files at call time, so the helper follows the drop on every
  pull. `python -m tests.content_drop_expect --summary` prints the design §8
  table from the current drop.

The drop location is `CONTENT_DROP_DIR` (the loaders' variable), else the repo's
`docs/drop_live`. Parsed files are cached per process; call `reset_caches()`
after changing the variable.

**Rules this module encodes** (design §2.2–§2.5, §3.2):

* the §2.3 decision table, first match wins (`classify`);
* the visibility rule: listed = kind product or group with at least one
  coverage row; default view = listed and live or supply_exception;
* the line a template sits on: its record-level `family|||subfamily` key
  (the drop's `subfamily` field *is* the product line), a group's key from
  AUTO_GROUPS, resolved against the named axis lines, then their former keys;
* Laurent's maker predicate and the evidence label, per supplier row;
* the placement projection (members with `"*"` expanded on the record line,
  shared refs, chain `lines[].pids`) and its comparison with FUNCTIONALITY;
* the out-row `to` split (exact name first, then greedy longest match);
* which editorial blocks the content loader writes (the public formula types,
  in served form, none left empty by cleaning; plus the index blocks).

Two choices the design leaves open are made here and named, so a loader that
disagrees fails a test instead of drifting silently:

* `SHELL_CONTENT_LOADED = False`: the six orphan `GRP-*` shells get no
  template (§2.3, "nothing to render"), so their blocks and supplier rows are
  not expected either (no content without a template).
* A chain `lines[]` entry whose `pids` is not a list (one `"*"` today) adds no
  placement: a chain entry names a feedstock, not a product line, so there is
  no line to expand the wildcard on.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator

# ── Where the drop is ────────────────────────────────────────────────────────

DROP_ENV = "CONTENT_DROP_DIR"
# backend/tests/content_drop_expect.py -> the repo root is two levels up.
DEFAULT_DROP_DIR = Path(__file__).resolve().parents[2] / "docs" / "drop_live"

LINE_KEY_SEP = "|||"
MIDDLE_DOT = "·"
LITERAL_MIDDLE_DOT = "\\u00b7"

# FIDX starts in January 2023, one point per month; FFORE follows it.
FIDX_START = (2023, 1)

# See the module docstring.
SHELL_CONTENT_LOADED = False


class DropMissing(RuntimeError):
    """The drop directory or one of its files is not there."""


def drop_dir() -> Path:
    root = Path(os.environ.get(DROP_ENV) or DEFAULT_DROP_DIR)
    if not root.is_dir():
        raise DropMissing(
            f"content drop not found at {root}; set {DROP_ENV} to the extracted drop "
            "(for example /home/alexis/costadvisor/docs/drop_live)")
    return root


@lru_cache(maxsize=None)
def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _json(*parts: str) -> Any:
    path = drop_dir().joinpath(*parts)
    if not path.is_file():
        raise DropMissing(f"drop file not found: {path}")
    return _load(str(path))


def reset_caches() -> None:
    """Forget every parsed file and derived value (after changing the drop dir)."""
    for fn in (_load, _cards, _records, _groups, _absorbed, _statuses, _lines,
               _former_line_keys, _template_lines, _placements, _out_rows,
               _expected_blocks, _report_joins, _report_structure):
        fn.cache_clear()


def raw(name: str) -> Any:
    """One constant of Laurent's database page, e.g. `raw("FORMULA_COMBOS")`."""
    return _json("raw", name if name.endswith(".json") else f"{name}.json")


def axis() -> dict:
    return _json("axis", "supply_axis.json")


def tree() -> dict:
    return _json("tree", "category_tree.json")


def scope() -> dict:
    return _json("scope", "v1_scope.json")


def manifest() -> dict:
    """The extractor's `_manifest.json` (source commit, constants, counts)."""
    return _json("_manifest.json")


def source_commit() -> str:
    """The Laurent commit this drop was extracted from."""
    return manifest()["git"]["commit"]


def report_manifest() -> list[dict]:
    return _json("reports", "MANIFEST.json")["reports"]


def playbook_files() -> list[Path]:
    folder = drop_dir() / "playbooks"
    if not folder.is_dir():
        raise DropMissing(f"drop folder not found: {folder}")
    return sorted(folder.glob("playbook_*_appdata.json"))


def make_line_key(family: str, line: str) -> str:
    return f"{family}{LINE_KEY_SEP}{line}"


def line_tail(key: str) -> str:
    return key.partition(LINE_KEY_SEP)[2]


# ── Cards and templates (§2.3) ───────────────────────────────────────────────

CARD_KINDS = ("product", "group", "absorbed", "pointer", "duplicate", "withdrawn")
SUPPLY_STATUSES = ("live", "supply_exception", "supply_pending", "not_audited")
LISTED_KINDS = ("product", "group")
DEFAULT_VIEW_STATUSES = ("live", "supply_exception")

_PID = r"[A-Z0-9]+(?:-[A-Z0-9]+)+"
_POINTER_RE = re.compile(rf"^POINTER to ({_PID})")
_PRICED_AS_RE = re.compile(rf"priced as\W*({_PID})")


@lru_cache(maxsize=None)
def _cards() -> dict:
    return raw("CURATED_CONTENT")


@lru_cache(maxsize=None)
def _records() -> dict:
    return raw("FORMULA_COMBOS")


@lru_cache(maxsize=None)
def _groups() -> dict:
    return raw("AUTO_GROUPS")


@lru_cache(maxsize=None)
def _absorbed() -> frozenset:
    return frozenset(raw("ABSORBED_FORMULA_IDS"))


def shells() -> list[str]:
    """`GRP-*` cards with no AUTO_GROUPS entry (§2.3 test 5): no template."""
    return sorted(p for p in _cards() if p.startswith("GRP-") and p not in _groups())


def template_codes() -> list[str]:
    """Every platform template code: CURATED_CONTENT keys, FORMULA_COMBOS
    records and AUTO_GROUPS groups, minus the shells."""
    every = set(_cards()) | set(_records()) | set(_groups())
    return sorted(every - set(shells()))


def card_only_codes() -> list[str]:
    """Templates with neither a record nor a group (content-only cards)."""
    return sorted(p for p in template_codes() if p not in _records() and p not in _groups())


@dataclass(frozen=True)
class CardStatus:
    kind: str
    supply_status: str | None
    # {source, value, date}: where the status came from. None when no status
    # block decided it (redirects, absorbed cards, groups, never audited).
    detail: dict | None
    redirect_to: str | None


def _classify(pid: str) -> CardStatus | None:
    card = _cards().get(pid) or {}
    archival = card.get("_archival") or ""
    if isinstance(archival, str):
        pointer = _POINTER_RE.match(archival)
        if pointer:                                                    # test 1
            return CardStatus("pointer", None, None, pointer.group(1))
        if archival.startswith("DUPLICATE"):                           # test 2
            twin = _PRICED_AS_RE.search(archival)
            return CardStatus("duplicate", None, None, twin.group(1) if twin else None)
    if pid in _absorbed():                                             # test 3 (the set, never the text)
        return CardStatus("absorbed", None, None, None)
    if pid.startswith("GRP-"):
        if pid in _groups():                                           # test 4
            return CardStatus("group", "not_audited", None, None)
        return None                                                    # test 5: a shell, not loaded
    exception = card.get("supply_exception")
    if exception:                                                      # test 6
        return CardStatus("product", "supply_exception", {
            "source": "supply_exception", "value": exception.get("status"),
            "date": exception.get("date")}, None)
    pending = card.get("supply_pending")
    if isinstance(pending, dict) and pending.get("status") in ("GRADE_PENDING", "SUPPLY_PENDING"):
        return CardStatus("product", "supply_pending", {                # test 7
            "source": "supply_pending", "value": pending["status"],
            "date": pending.get("date")}, None)
    audit = card.get("maker_audit")
    if isinstance(audit, dict) and audit.get("outcome") == "LIVE":     # test 8
        return CardStatus("product", "live", {
            "source": "maker_audit", "value": "LIVE", "date": audit.get("date")}, None)
    return CardStatus("product", "not_audited", None, None)            # test 9


@lru_cache(maxsize=None)
def _statuses() -> dict[str, CardStatus]:
    return {pid: _classify(pid) for pid in template_codes()}


def classify(pid: str) -> CardStatus | None:
    """The decision table for one PID. None for a shell or an unknown PID."""
    return _statuses().get(pid)


def card_statuses() -> dict[str, CardStatus]:
    """code → CardStatus for every template."""
    return dict(_statuses())


def kind_counts() -> Counter:
    return Counter(s.kind for s in _statuses().values())


def status_counts() -> Counter:
    return Counter(s.supply_status for s in _statuses().values() if s.supply_status)


def redirects() -> dict[str, str | None]:
    """Pointer and duplicate PIDs → their target PID."""
    return {pid: s.redirect_to for pid, s in _statuses().items()
            if s.kind in ("pointer", "duplicate")}


def merged_from() -> dict[str, list[str]]:
    """The older merge style: target PID → the merged PIDs it names (logged
    by the loader; the merged records no longer exist)."""
    out: dict[str, list[str]] = {}
    for pid, card in _cards().items():
        entries = card.get("merged_from")
        if isinstance(entries, list):
            names = [e.get("pid") for e in entries if isinstance(e, dict) and e.get("pid")]
            if names:
                out[pid] = names
    return out


def pointer_pids() -> list[str]:
    return sorted(p for p, s in _statuses().items() if s.kind == "pointer")


# ── Coverage and cost lines ──────────────────────────────────────────────────

def repair_combo_id(combo_id: str) -> str:
    return combo_id.replace(LITERAL_MIDDLE_DOT, MIDDLE_DOT)


def combos(pid: str) -> list[dict]:
    """The combos a template's coverage rows come from: a record's FORMULA_COMBOS
    combos, or a group's AUTO_GROUPS combos. Empty for a card-only template."""
    if pid in _records():
        return list(_records()[pid].get("combos") or [])
    if pid in _groups():
        return list(_groups()[pid].get("combos") or [])
    return []


def coverage_count() -> int:
    """Coverage rows on a fresh load: one per (template, region, variant)."""
    keys = {(pid, c.get("region"), c.get("variant") or "")
            for pid in template_codes() for c in combos(pid)}
    return len(keys)


def cost_line_count() -> int:
    """Cost lines on a fresh load: every line of every record combo (group
    combos carry `lines_html` only, so no cost lines)."""
    return sum(len(c.get("lines") or []) for pid in _records() for c in _records()[pid]["combos"])


def has_coverage(pid: str) -> bool:
    return bool(combos(pid))


def is_listed(pid: str) -> bool:
    s = _statuses().get(pid)
    return bool(s and s.kind in LISTED_KINDS and has_coverage(pid))


def listed_codes() -> list[str]:
    return sorted(p for p in _statuses() if is_listed(p))


def default_view_codes() -> list[str]:
    return sorted(p for p in listed_codes()
                  if _statuses()[p].supply_status in DEFAULT_VIEW_STATUSES)


# ── Supply tiers (§2.2) ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Line:
    key: str
    family: str
    subfamily: str | None
    name: str
    platform: str | None
    former_keys: tuple[str, ...]


def families() -> list[str]:
    return [f["family"] for f in axis()["families"]]


def subfamilies() -> list[tuple[str, str | None]]:
    """(family, sub-family name or None) for every axis sub-family node."""
    return [(f["family"], sf.get("name")) for f in axis()["families"] for sf in f["subfamilies"]]


def subfamily_former_names() -> dict[tuple[str, str | None], list[str]]:
    """(family, sub-family name) → its earlier names, for the nodes that have
    any (a renamed sub-family is matched through them)."""
    return {(f["family"], sf.get("name")): list(sf["former_names"])
            for f in axis()["families"] for sf in f["subfamilies"] if sf.get("former_names")}


def subfamilies_without_lines(named_only: bool = False) -> list[tuple[str, str | None]]:
    """Sub-families with no axis line; with `named_only`, those with no named
    line (one more today: a node whose only line is deliberately unnamed, so it
    has no `product_lines` row)."""
    return [(f["family"], sf.get("name")) for f in axis()["families"] for sf in f["subfamilies"]
            if not any((ln.get("name") if named_only else True) for ln in sf["lines"])]


def _former_names(node: dict) -> list[str]:
    names = list(node.get("former_names") or [])
    if node.get("former_name"):
        names.append(node["former_name"])
    return [n for n in names if isinstance(n, str) and n]


@lru_cache(maxsize=None)
def _lines() -> dict[str, Line]:
    out: dict[str, Line] = {}
    for fam in axis()["families"]:
        for sf in fam["subfamilies"]:
            for ln in sf["lines"]:
                if not ln.get("name"):
                    continue          # deliberately unnamed: not a row (§2.2)
                key = make_line_key(fam["family"], ln["name"])
                formers = tuple(n if LINE_KEY_SEP in n else make_line_key(fam["family"], n)
                                for n in _former_names(ln))
                out[key] = Line(key, fam["family"], sf.get("name"), ln["name"],
                                ln.get("platform"), formers)
    return out


def product_lines() -> dict[str, Line]:
    """line_key → Line for every named axis line (the `product_lines` rows)."""
    return dict(_lines())


def unnamed_platforms() -> list[dict]:
    """The axis lines left deliberately unnamed: `{family, platform, todays_keys}`."""
    return [{"family": fam["family"], "platform": ln.get("platform"),
             "todays_keys": list(ln.get("todays_keys") or [])}
            for fam in axis()["families"] for sf in fam["subfamilies"]
            for ln in sf["lines"] if not ln.get("name")]


def unpublished_line_names() -> list[str]:
    """The line names that must never be shown (design §2.2, frozen
    instruction): the tails of the legacy record keys of the products on the
    deliberately unnamed platforms."""
    return sorted({line_tail(k) for u in unnamed_platforms() for k in u["todays_keys"]
                   if line_tail(k)})


@lru_cache(maxsize=None)
def _former_line_keys() -> dict[str, str]:
    """former key → current key (a former key that is itself a current key,
    or that two lines claim, is not used)."""
    claims: dict[str, set[str]] = defaultdict(set)
    for line in _lines().values():
        for old in line.former_keys:
            claims[old].add(line.key)
    return {old: next(iter(keys)) for old, keys in claims.items()
            if len(keys) == 1 and old not in _lines()}


def resolve_line_key(key: str | None) -> str | None:
    """A current line key for `key` (itself, or the line that formerly had it)."""
    if not key:
        return None
    if key in _lines():
        return key
    return _former_line_keys().get(key)


def record_line_key(pid: str) -> str | None:
    """A template's authored line key: the record-level `family|||subfamily`
    (the drop's `subfamily` is the product line), or the group's. None for a
    card-only template."""
    rec = _records().get(pid) or _groups().get(pid)
    if not rec or not rec.get("family") or not rec.get("subfamily"):
        return None
    return make_line_key(rec["family"], rec["subfamily"])


@lru_cache(maxsize=None)
def _template_lines() -> dict[str, str | None]:
    return {pid: resolve_line_key(record_line_key(pid)) for pid in template_codes()}


def template_line(pid: str) -> str | None:
    """The current line key a template's `product_line_id` points at, or None."""
    return _template_lines().get(pid)


def templates_without_line() -> list[str]:
    return sorted(p for p, k in _template_lines().items() if k is None)


def off_axis_record_keys() -> dict[str, str]:
    """pid → record key, for records whose key is not a current line."""
    return {p: record_line_key(p) for p in _records()
            if record_line_key(p) and template_line(p) is None}


def lines_with_listed() -> set[str]:
    return {template_line(p) for p in listed_codes() if template_line(p)}


def template_family(pid: str) -> str | None:
    """A template's family name: its line's family, else its record's."""
    key = template_line(pid) or record_line_key(pid)
    return key.partition(LINE_KEY_SEP)[0] if key else None


# ── Makers (§2.4) ────────────────────────────────────────────────────────────

ORIGIN_RESTRICTION = "sanctioned_origin"


def _floor_eu_false(row: dict) -> bool:
    fe = row.get("floor_eligibility")
    return isinstance(fe, dict) and fe.get("EU") is False


def counts_toward_floor(row: dict) -> bool:
    """Laurent's predicate (his tools share it): a named, non-distributor
    maker, not EU-ineligible, not `counted: false`, evidence absent or
    VERIFIED."""
    return (bool((row.get("n") or "").strip())
            and row.get("role") != "distributor"
            and not _floor_eu_false(row)
            and row.get("counted") is not False
            and row.get("maker_evidence") in (None, "VERIFIED"))


def evidence_label(row: dict) -> str:
    """The short label code, from structured fields only (never `count_why`)."""
    if row.get("role") == "distributor":
        return "distributor"
    evidence = row.get("maker_evidence")
    if evidence == "INFERRED_FAMILY":
        return "family_only"
    if evidence == "UNVERIFIED":
        return "unverified"
    if row.get("weak_reading") is True:
        return "weak"
    if row.get("counted") is False:
        return "not_counted"
    if evidence == "VERIFIED":
        return "verified"
    return "not_audited"


def origin_restriction(row: dict) -> str | None:
    """One marker for both mechanisms: `origin_restriction` or EU-ineligible."""
    return ORIGIN_RESTRICTION if (row.get("origin_restriction") or _floor_eu_false(row)) else None


@dataclass(frozen=True)
class SupplierRow:
    pid: str
    row_order: int            # position in the card's `suppliers` list
    raw_name: str
    role: str | None
    maker_evidence: str | None
    counted: bool | None
    counts_toward_floor: bool
    evidence_label: str
    origin_restriction: str | None
    floor_eligible_eu: bool | None
    weak_reading: bool | None
    region_uncertain: bool | None


def supplier_rows(pid: str) -> list[SupplierRow]:
    """The card's supplier rows, in authored order, with the derived fields."""
    out = []
    for pos, row in enumerate((_cards().get(pid) or {}).get("suppliers") or []):
        fe = row.get("floor_eligibility")
        out.append(SupplierRow(
            pid=pid, row_order=pos, raw_name=row.get("n") or "", role=row.get("role"),
            maker_evidence=row.get("maker_evidence"), counted=row.get("counted"),
            counts_toward_floor=counts_toward_floor(row), evidence_label=evidence_label(row),
            origin_restriction=origin_restriction(row),
            floor_eligible_eu=fe.get("EU") if isinstance(fe, dict) else None,
            weak_reading=row.get("weak_reading"), region_uncertain=row.get("region_uncertain"),
        ))
    return out


def content_codes() -> list[str]:
    """The cards whose content (blocks, supplier rows) is loaded."""
    codes = set(template_codes())
    if SHELL_CONTENT_LOADED:
        codes |= set(shells())
    return sorted(codes)


def all_supplier_rows(codes: Iterable[str] | None = None) -> Iterator[SupplierRow]:
    for pid in (content_codes() if codes is None else codes):
        yield from supplier_rows(pid)


def card_quotes(pid: str) -> list[str]:
    """Every quote text on a card's supplier rows (`maker_quote` and the
    sites' `quote`): text that is never stored, so never served."""
    out = []
    for row in (_cards().get(pid) or {}).get("suppliers") or []:
        if isinstance(row.get("maker_quote"), str) and row["maker_quote"].strip():
            out.append(row["maker_quote"].strip())
        for site in row.get("sites") or []:
            if isinstance(site, dict) and isinstance(site.get("quote"), str) and site["quote"].strip():
                out.append(site["quote"].strip())
    return out


def leak_probe_card() -> str:
    """A listed LIVE card whose supplier rows carry everything that must never
    be served: quotes, `count_why`, `role_changed` and a share above 0. The
    first such PID in sort order."""
    for pid in default_view_codes():
        if _statuses()[pid].supply_status != "live":
            continue
        rows = (_cards().get(pid) or {}).get("suppliers") or []
        if (any(r.get("maker_quote") for r in rows) and any(r.get("count_why") for r in rows)
                and any(r.get("role_changed") for r in rows)
                and any(isinstance(r.get("share"), (int, float)) and r["share"] > 0 for r in rows)):
            return pid
    raise LookupError("no listed LIVE card carries quotes, count_why, role_changed and a share")


# The drop's own canonicaliser (raw/_functions/CANONICALIZE_SUPPLIER.js),
# ported verbatim. The content loader adds a legal-suffix repair on top, so
# producer and link counts from this port are approximate.
_SUPPLIER_SEP = re.compile(r" / |, ")


def canonicalize_supplier(raw_name: str, aliases: dict | None = None) -> list[str]:
    a = raw("SUPPLIER_ALIASES") if aliases is None else aliases

    def walk(r: str) -> list[str]:
        n = (r or "").strip()
        if not n:
            return []
        i = n.find(" (")
        base = n[:i].strip() if i > 0 else n
        if _SUPPLIER_SEP.search(base):
            out: list[str] = []
            for part in _SUPPLIER_SEP.split(base):
                out += walk(part.strip())
            return out
        x = a.get(n) or a.get(base) or base
        return [a.get(x) or x]

    return walk(raw_name)


def approximate_producers_and_links() -> tuple[int, int]:
    """(distinct canonical producers, distinct (PID, producer) links) over the
    loaded cards, with the drop's own canonicaliser. Approximate (see above)."""
    aliases = raw("SUPPLIER_ALIASES")
    producers: set[str] = set()
    links: set[tuple[str, str]] = set()
    for pid in content_codes():
        for row in (_cards().get(pid) or {}).get("suppliers") or []:
            for name in canonicalize_supplier(row.get("n") or "", aliases):
                producers.add(name)
                links.add((pid, name))
    return len(producers), len(links)


# ── Demand axis (§2.5) ───────────────────────────────────────────────────────

def industries() -> list[str]:
    return list(tree()["industries"])


def categories() -> list[dict]:
    return [c for b in tree()["industries"].values() for c in b.get("categories") or []]


def _pids_by_record_line() -> dict[str, list[str]]:
    by_line: dict[str, list[str]] = defaultdict(list)
    for pid in _records():
        key = record_line_key(pid)
        if key:
            by_line[key].append(pid)
    return by_line


@lru_cache(maxsize=None)
def _placements() -> dict[tuple[str, str], tuple[str, str | None, str, str]]:
    doc = tree()
    shared = doc["shared"]
    by_line = _pids_by_record_line()
    out: dict[tuple[str, str], tuple[str, str | None, str, str]] = {}
    for industry, branch in doc["industries"].items():
        for cat in branch.get("categories") or []:
            sources = list(cat.get("members") or [])
            for ref in cat.get("ref") or []:
                sources += (shared.get(ref) or {}).get("members") or []
            pids: list[str] = []
            for member in sources:
                pids += by_line.get(member["line"], []) if member["pids"] == "*" else member["pids"]
            for chain in cat.get("lines") or []:
                if isinstance(chain.get("pids"), list):
                    pids += chain["pids"]
            for pid in pids:
                out.setdefault((cat["id"], pid), (industry, cat.get("fn"), cat["name"], cat["id"]))
    return out


def placements() -> dict[tuple[str, str], tuple[str, str | None, str, str]]:
    """(category id, pid) → (industry, fn, category name, category id)."""
    return dict(_placements())


def functionality_mismatches() -> dict[str, tuple[list, list]]:
    """pid → (in FUNCTIONALITY only, in the tree only) where they differ."""
    want = {pid: {(e["industry"], e["fn"], e["name"], e["category"]) for e in entries}
            for pid, entries in raw("FUNCTIONALITY").items()}
    have: dict[str, set] = defaultdict(set)
    for (_cat, pid), value in _placements().items():
        have[pid].add(value)
    out = {}
    for pid in sorted(set(want) | set(have)):
        a, b = want.get(pid, set()), have.get(pid, set())
        if a != b:
            out[pid] = (sorted(a - b, key=str), sorted(b - a, key=str))
    return out


def functionality_residuals() -> list[str]:
    """The PIDs whose tree placements differ from FUNCTIONALITY."""
    return sorted(functionality_mismatches())


@dataclass(frozen=True)
class OutRow:
    industry: str
    pid: str
    code: str | None
    to_raw: str | None
    # Each part of `to`: (text, True when it is an industry name).
    to_industries: tuple[tuple[str, bool], ...]
    # none | exact | list
    split: str


_OUT_SEP = re.compile(r"\s*[,;]\s*")


def split_to(text: str | None, names: list[str]) -> tuple[tuple[tuple[str, bool], ...], str]:
    """The §2.5 rule: the whole text if it is an industry name; otherwise
    known names matched greedily, longest first, split only on the separators
    between them; a part that matches no name is kept as text."""
    if not text or not text.strip():
        return (), "none"
    if text in names:
        return ((text, True),), "exact"
    by_len = sorted(names, key=len, reverse=True)
    parts: list[tuple[str, bool]] = []
    i = 0
    while i < len(text):
        match = next((n for n in by_len if text.startswith(n, i)), None)
        if match:
            parts.append((match, True))
            i += len(match)
        else:
            nxt = _OUT_SEP.search(text, i)
            end = nxt.start() if nxt else len(text)
            if text[i:end].strip():
                parts.append((text[i:end].strip(), False))
            i = end
        sep = _OUT_SEP.match(text, i)
        if sep:
            i = sep.end()
    return tuple(parts), "list"


@lru_cache(maxsize=None)
def _out_rows() -> tuple[OutRow, ...]:
    names = industries()
    rows = []
    for industry, branch in tree()["industries"].items():
        for o in branch.get("out") or []:
            parts, kind = split_to(o.get("to"), names)
            rows.append(OutRow(industry, o["pid"], o.get("code"), o.get("to"), parts, kind))
    return tuple(rows)


def out_rows() -> list[OutRow]:
    return list(_out_rows())


def out_split_summary() -> dict[str, int]:
    """Counts of the `to` readings: none, exact (and exact_with_comma), list,
    and the parts that resolve to no industry."""
    rows = _out_rows()
    return {
        "rows": len(rows),
        "none": sum(1 for r in rows if r.split == "none"),
        "exact": sum(1 for r in rows if r.split == "exact"),
        "exact_with_comma": sum(1 for r in rows if r.split == "exact" and "," in (r.to_raw or "")),
        "list": sum(1 for r in rows if r.split == "list"),
        "unresolved_parts": sum(1 for r in rows for _t, ok in r.to_industries if not ok),
    }


# ── Editorial blocks (§3.2 content) ──────────────────────────────────────────

# The formula block types the loader writes and the editorial API serves.
# Not written: suppliers (producer_formulas is the only store), supply and
# demand (invented splits).
PUBLIC_FORMULA_BLOCKS = (
    "functionalities", "applications", "supplier_note", "compliance", "macro_drivers",
    "substitution", "synthesis_route", "current_events", "negotiation_note",
)
INDEX_BLOCKS = ("index_narrative", "index_source_meta")

_SHARE0_SENTENCE = re.compile(r"share\s*:\s*0", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def strip_private(value: Any) -> Any:
    """`_`-prefixed keys removed at any depth (the served form)."""
    if isinstance(value, dict):
        return {k: strip_private(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [strip_private(v) for v in value]
    return value


def _served_applications(entries: Any) -> Any:
    if not isinstance(entries, list):
        return entries
    return strip_private([a for a in entries if not (isinstance(a, dict) and a.get("_no_receiver"))])


def _note_is_only_share0(text: Any) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    sentences = [s for s in _SENTENCE_END.split(text.strip()) if s.strip()]
    return bool(sentences) and all(_SHARE0_SENTENCE.search(s) for s in sentences)


@lru_cache(maxsize=None)
def _expected_blocks() -> frozenset[tuple[str, str, str, str | None]]:
    cards, outlook = _cards(), raw("FUTURE_OUTLOOK")
    routes, events = raw("SYNTHESIS_ROUTES"), raw("CURRENT_EVENTS_OUTLOOK")
    keys: set[tuple[str, str, str, str | None]] = set()

    def add(code: str, block_type: str, body: Any, region: str | None = None,
            subject_type: str = "formula") -> None:
        if _present(strip_private(body)):
            keys.add((subject_type, code, block_type, region))

    for pid in content_codes():
        c, f = cards.get(pid) or {}, outlook.get(pid) or {}
        add(pid, "functionalities", c.get("functionalities"))
        add(pid, "applications", _served_applications(c.get("applications")))
        if not _note_is_only_share0(c.get("supplierNote")):
            add(pid, "supplier_note", c.get("supplierNote"))
        add(pid, "compliance", c.get("compliance"))
        add(pid, "macro_drivers", f.get("macroDrivers"))          # FUTURE_OUTLOOK only
        add(pid, "substitution", f.get("substitution") if _present(f.get("substitution"))
            else c.get("substitution"))
        add(pid, "synthesis_route", routes.get(pid))
        for region_key, text in (events.get(pid) or {}).items():
            if str(region_key).startswith("_"):
                continue                                         # a withdrawn outlook
            add(pid, "current_events", text, None if region_key == "*" else region_key)
        add(pid, "negotiation_note", c.get("negotiationNote"))

    for key, entry in raw("INDEX_NARRATIVES").items():
        add(key, "index_narrative", entry, subject_type="index")
    for key, entry in raw("INDEX_SOURCE_META").items():
        add(key, "index_source_meta", entry, subject_type="index")
    return frozenset(keys)


def expected_block_keys() -> set[tuple[str, str, str, str | None]]:
    """(subject_type, subject_code, block_type, region) of every platform block
    a fresh load writes. `region` is the drop's region key (None = `"*"`)."""
    return set(_expected_blocks())


def expected_block_counts() -> Counter:
    """block_type → count."""
    return Counter(block_type for _s, _c, block_type, _r in _expected_blocks())


# ── Reports, playbooks, indexes ──────────────────────────────────────────────

_SECTION_RE = re.compile(r"<section\b[^>]*\bid=\"([\w-]+)\"[^>]*>(.*?)</section>", re.S)
_H3_RE = re.compile(r"<h3\b[^>]*>(.*?)</h3>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
SECTION7_BLOCKS = (("pestel", "pestel"), ("porter", "porter"),
                   ("market drivers", "market_drivers"), ("kraljic", "kraljic"),
                   ("outlook", "outlook"))


@lru_cache(maxsize=None)
def _report_structure() -> tuple[int, int, int]:
    """(reports, sections, panels): seven sections per report; panels are
    sections 1–6 plus each recognised block of section 7 (by its h3 wording)."""
    reports = sections = panels = 0
    for entry in report_manifest():
        path = drop_dir() / "reports" / Path(entry["delivered"]).name
        html = path.read_text(encoding="utf-8")
        found = _SECTION_RE.findall(html)
        reports += 1
        sections += len(found)
        if found:
            panels += len(found) - 1
            seen = set()
            for h3 in _H3_RE.findall(found[-1][1]):
                text = _TAG_RE.sub("", h3).lower()
                block = next((b for word, b in SECTION7_BLOCKS if word in text), None)
                if block:
                    seen.add(block)
            panels += len(seen)
    return reports, sections, panels


@lru_cache(maxsize=None)
def _report_joins() -> frozenset[tuple[str, str]]:
    """(report slug, line key): the v1 `report_map`, plus each report's old
    keys (`family|||name`, and the `report_old_line` of the v1 lines it
    serves) through the axis `key_map`; an old key that is still a record
    line joins to itself."""
    entries = report_manifest()
    slug_by_file = {r["delivered"]: r["slug"] for r in entries}
    doc = scope()
    old: dict[str, set[str]] = defaultdict(set)
    for r in entries:
        if r.get("family"):
            old[r["slug"]].add(make_line_key(r["family"], r["name"]))
    for line in doc.get("lines") or []:
        slug = slug_by_file.get(line.get("report_file"))
        if slug and line.get("report_old_line"):
            old[slug].add(line["report_old_line"])
    key_map: dict[str, list[dict]] = defaultdict(list)
    for row in axis().get("key_map") or []:
        key_map[row["todays_key"]].append(row)
    record_keys = {record_line_key(p) for p in _records()} - {None}
    joins: set[tuple[str, str]] = set()
    for slug, keys in old.items():
        for key in keys:
            if key in record_keys:
                joins.add((slug, key))
            for row in key_map.get(key, ()):
                if row.get("new_line") and row.get("new_family"):
                    joins.add((slug, make_line_key(row["new_family"], row["new_line"])))
    for line_key, file in (doc.get("report_map") or {}).items():
        if file in slug_by_file:
            joins.add((slug_by_file[file], line_key))
    return frozenset(joins)


def report_joins() -> set[tuple[str, str]]:
    return set(_report_joins())


def report_counts() -> dict[str, int]:
    reports, sections, panels = _report_structure()
    joins = _report_joins()
    return {"reports": reports, "sections": sections, "panels": panels,
            "joins": len(joins),
            "joins_not_current_line": sum(1 for _s, k in joins if k not in _lines())}


def playbook_counts() -> dict[str, int]:
    playbooks = levers = objectives = 0
    for path in playbook_files():
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        playbooks += 1
        levers += len(doc.get("opportunities") or [])
        objectives += len(doc.get("objectives") or [])
    return {"playbooks": playbooks, "levers": levers, "objectives": objectives}


def playbook_lever_count(slug: str) -> int:
    path = drop_dir() / "playbooks" / f"playbook_{slug}_appdata.json"
    with open(path, encoding="utf-8") as fh:
        return len(json.load(fh).get("opportunities") or [])


def index_counts() -> dict[str, int]:
    fidx, ffore = raw("FIDX"), raw("FFORE")
    return {"series": len(fidx),
            "monthly_points": sum(len(v) for v in fidx.values()) + sum(len(v) for v in ffore.values()),
            "type_codes": len(raw("FCOVERED"))}


def month_index(year: int, month: int) -> int:
    """Position of a month in FIDX (January 2023 = 0)."""
    return (year - FIDX_START[0]) * 12 + (month - FIDX_START[1])


def combo_lines(pid: str, region: str, variant: str | None = None) -> list[tuple[int, str, str, str]]:
    """The (weight, label, tag, kind) cost lines of one record combo; `region`
    is the drop code (EU, NA …)."""
    for combo in (_records().get(pid) or {}).get("combos") or []:
        if combo.get("region") == region and (combo.get("variant") or None) == (variant or None):
            return [tuple(line) for line in combo.get("lines") or []]
    raise LookupError(f"{pid} has no {region} combo" + (f" ({variant})" if variant else ""))


def should_cost_index(pid: str, region: str, year: int, month: int,
                      variant: str | None = None) -> float:
    """The combo's should-cost index at a month of FIDX history: the weighted
    mean of each line's series level (base 100 = January 2023); a line whose
    tag FCOVERED does not carry (the margin's `fixed`) sits at 100."""
    covered, fidx = raw("FCOVERED"), raw("FIDX")
    m = month_index(year, month)
    lines = combo_lines(pid, region, variant)
    total = sum(w for w, *_ in lines)
    level = 0.0
    for weight, _label, tag, _kind in lines:
        key = covered.get(tag)
        level += weight * (float(fidx[key][m]) if key else 100.0)
    return level / total


def last_actual_month() -> tuple[int, int]:
    """(year, month) of the last FIDX point."""
    n = max(len(v) for v in raw("FIDX").values()) - 1
    return FIDX_START[0] + (FIDX_START[1] - 1 + n) // 12, (FIDX_START[1] - 1 + n) % 12 + 1


# ── Summary (design §8) ──────────────────────────────────────────────────────

def summary() -> list[tuple[str, str]]:
    """(item, value) rows of the design §8 table, derived from the drop."""
    kinds, statuses = kind_counts(), status_counts()
    lines = product_lines()
    rows_all = [r for pid in _cards() for r in supplier_rows(pid)]
    rows_loaded = list(all_supplier_rows())
    producers, links = approximate_producers_and_links()
    out = out_split_summary()
    rep, pb, idx = report_counts(), playbook_counts(), index_counts()
    blocks = expected_block_counts()
    shelled = len(shells())
    def n(x: int) -> str:
        return f"{x:,}"
    no_line = templates_without_line()
    return [
        ("source commit", source_commit()),
        ("families", n(len(families()))),
        ("sub-families", f"{n(len(subfamilies()))} ({len(subfamilies_without_lines())} with no line, "
                         f"{len(subfamilies_without_lines(named_only=True))} with no named line, "
                         f"{sum(1 for _f, s in subfamilies() if s is None)} unnamed)"),
        ("product lines", f"{n(len(lines))} ({len(unnamed_platforms())} unnamed platforms skipped)"),
        ("lines with >=1 listed card", f"{n(len(lines_with_listed()))} "
                                       f"({n(len(lines) - len(lines_with_listed()))} with none)"),
        ("templates", f"{n(len(template_codes()))} = {n(len(_records()))} records + "
                      f"{n(len(_groups()))} groups + {n(len(card_only_codes()))} card-only keys "
                      f"({shelled} shells skipped)"),
        ("card_kind", " · ".join(f"{k} {n(kinds.get(k, 0))}" for k in CARD_KINDS if kinds.get(k))),
        ("supply_status", " · ".join(f"{s} {n(statuses.get(s, 0))}" for s in SUPPLY_STATUSES)),
        ("templates with no product line", f"{len(no_line)} ({len(off_axis_record_keys())} off-axis "
                                           f"records + {len(card_only_codes())} card-only keys)"),
        ("listed cards", n(len(listed_codes()))),
        ("default view", n(len(default_view_codes()))),
        ("redirects", f"{len(redirects())} ({len(pointer_pids())} pointers + "
                      f"{sum(1 for s in _statuses().values() if s.kind == 'duplicate')} duplicates; "
                      f"merged_from logged: {sum(len(v) for v in merged_from().values())})"),
        ("coverage rows / cost lines", f"{n(coverage_count())} / {n(cost_line_count())}"),
        ("categories / shared / out rows", f"{n(len(categories()))} / {n(len(tree()['shared']))} / "
                                           f"{n(out['rows'])}"),
        ("out-row `to` split", f"exact {out['exact']} (with a comma {out['exact_with_comma']}) · "
                               f"lists {out['list']} · none {out['none']} · "
                               f"unresolved parts {out['unresolved_parts']}"),
        ("placements", f"{n(len(_placements()))}; FUNCTIONALITY residuals "
                       f"{len(functionality_residuals())}: {', '.join(functionality_residuals())}"),
        ("series / monthly points / type codes", f"{idx['series']} / {n(idx['monthly_points'])} / "
                                                 f"{idx['type_codes']}"),
        ("editorial blocks", f"{n(sum(blocks.values()))} ("
                             + ", ".join(f"{t} {n(blocks[t])}"
                                         for t in PUBLIC_FORMULA_BLOCKS + INDEX_BLOCKS if blocks[t])
                             + ")"),
        ("supplier rows / counting toward the floor", f"{n(len(rows_all))} / "
                                                      f"{n(sum(r.counts_toward_floor for r in rows_all))}"
                                                      f" (loaded cards: {n(len(rows_loaded))} / "
                                                      f"{n(sum(r.counts_toward_floor for r in rows_loaded))})"),
        ("producers / links (approximate)", f"~{n(producers)} / ~{n(links)}"),
        ("reports / sections / panels / joins", f"{rep['reports']} / {n(rep['sections'])} / "
                                                f"{n(rep['panels'])} / {rep['joins']}"),
        ("playbooks / levers / objectives", f"{pb['playbooks']} / {n(pb['levers'])} / "
                                            f"{n(pb['objectives'])}"),
    ]


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expected content figures from the drop.")
    parser.add_argument("--summary", action="store_true", help="print the design §8 figures")
    args = parser.parse_args(argv)
    if not args.summary:
        parser.print_help()
        return 0
    try:
        rows = summary()
    except DropMissing as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    width = max(len(item) for item, _ in rows)
    print(f"Content drop: {drop_dir()}")
    for item, value in rows:
        print(f"  {item:<{width}}  {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
