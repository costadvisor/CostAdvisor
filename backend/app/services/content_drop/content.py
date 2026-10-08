"""Content loader: the cards' prose, the facets and the makers (design §3.2, §2.4).

Loads, as platform rows (`team_id` NULL):

    editorial_blocks (+ versions)   one block per prose structure per subject
    dimension_terms / _aliases      the tree's industries and functions
    dimension_assertions            DERIVED from category_placements
    producers / _aliases / producer_formulas   the canonical makers and their evidence

**Which cards.** A card gets content only if it gets a template: every
CURATED_CONTENT key, FORMULA_COMBOS record and AUTO_GROUPS group, minus the
`GRP-*` shells (a `GRP-*` card with no AUTO_GROUPS entry: no formula, no
group, nothing to render). The shells' blocks and supplier rows are skipped
and named on the report (`content_codes`, `shells`).

**Editorial blocks.** Keyed `subject_type='formula'`, `subject_code` = PID,
written through the editorial service's own `create_block` / `add_version`, so
the version numbers and the `current_version_id` pointer are the ones the card
read expects. Provenance `imported`, version 1.

    CURATED_CONTENT.functionalities   functionalities    json
    CURATED_CONTENT.applications      applications       json
    CURATED_CONTENT.supplierNote      supplier_note      text
    CURATED_CONTENT.compliance        compliance         json
    FUTURE_OUTLOOK.macroDrivers       macro_drivers      json (FUTURE_OUTLOOK only)
    FUTURE_OUTLOOK.substitution       substitution       json (else CURATED_CONTENT's)
    SYNTHESIS_ROUTES                  synthesis_route    json
    CURRENT_EVENTS_OUTLOOK['*']       current_events     text, region NULL (the wildcard)
    CURATED_CONTENT.negotiationNote   negotiation_note   text
    INDEX_NARRATIVES                  index_narrative    json, subject_type 'index'
    INDEX_SOURCE_META                 index_source_meta  json, subject_type 'index'

Not written: `suppliers` (the makers live in `producer_formulas` only, so no
quote, share or audit note sits in a block an API serves raw), and `supply` /
`demand` (the source ruled those splits invented). Platform blocks of these
three retired types are deleted with their versions on every run.
CURATED_CONTENT's older `macroDrivers` are skipped and named on the report:
FUTURE_OUTLOOK covers every card and carries `direction`.

**Every block is written in its served form** (`sanitize.served_body`):
`_`-prefixed keys stripped at any depth, applications flagged `_no_receiver`
dropped, the supplier note without the data-team instruction about `share:0`,
the current-events text with "sibling PID" read as "related product", a
bare-string compliance entry given the entry shape (`flag: None`,
`bare: true`). A block left empty by cleaning is not written. The two
CURRENT_EVENTS_OUTLOOK entries that carry only `_removed` are withdrawn
outlooks and are skipped with a report line.

A reload compares the block's current version with the drop. Equal: nothing
happens. Different: a new version is appended (`add_version`), so the old text
stays readable. A block someone edited or approved on the platform since the
import (provenance no longer `imported`) keeps its text; the report names it.
A platform block of a written type that the drop no longer carries is
reported `stale`, never deleted.

**Dimensions.** `industry` terms are the tree's industries (code = slug,
label = name), `functionality` terms the distinct `fn` of the demand tree
(code = `_slug`, so an earlier term with the same label is the same row).
Every term's label is its own alias. A platform `taxonomy` term of either kind
that the tree no longer names is kept with `is_active` false. Assertions are
the placement projection: one per (PID, industry) and one per (PID, fn),
region NULL, `detail = {"categories": [codes]}`. Like the placements they come
from, they are rebuilt on every load: a loader assertion on these terms that
the projection no longer produces is deleted (the tree states every placement
of every product, so a missing one is a removal, not silence). Assertions from
a decision file or the API are never touched.

**Producers.** `canonicalize_supplier` is `raw/_functions/CANONICALIZE_SUPPLIER.js`
ported verbatim: split on `" / "` and `", "`, strip from the first `" ("`,
apply SUPPLIER_ALIASES twice, return a list. One addition: a fragment that is
only a legal suffix is put back on the fragment before it, so `Maker Co., Ltd.`
is one company and never mints `Ltd.`. SUPPLIER_BUCKETS placeholders are
producers with `is_bucket`. An alias key that holds a separator is never
reached (the function splits before it looks up); that is the source's own
behaviour, and the report lists those keys.

One `producer_formulas` row per (producer, PID), with the maker evidence of
§2.4 (see `_link_fields`): role, `maker_evidence`, `counted`, the source's own
floor predicate (`counts_toward_floor`), a short `evidence_label` from the
structured flags only, `weak_reading`, `corp_group`, the integration reading
(`integration_basis` falls back to `integration_why`), one
`origin_restriction` marker for both mechanisms, `floor_eligible_eu`,
`region_uncertain`, the manufacturing regions as given, `tags`, normalised
`sites`, and `row_order` (the position in the card's supplier list). **Not
stored**: quotes, sources (`maker_quote`, `maker_source`, the sites' `quote`
and `source`), shares (`share_pct` NULL, `share_disclosed` false on every
row), `role_changed`, `count_why` and the other audit-trail fields.

When several rows on one card name the same producer, the first row's fields
and position stand, regions, tags and sites are united, and the report names
the later rows. A loader row for a drop PID whose supplier list no longer
names that producer is deleted (the drop states each PID's list in full; a
shell's list is not loaded, so its rows go too).

A canonical name no producer carries yet, but that exactly one existing
producer answers to through its aliases (the dossier loader mints a qualified
name such as `Maker (Full Legal Name)` under the bare alias key), is given to
that producer rather than minted as a twin (`_adopt`).

`producer_aliases` record each raw string and each fragment against the
producer it names, created only where missing. `match_key` is the raw string
with the parenthetical dropped, the model's rule, except where the
parenthetical changes the company (an alias maps `Maker (now part of Other)`
to Other): there the full string is the key, so a lookup of the bare name is
not sent to another company.

Idempotent: a second run reports zero changes. **Never commits**: the CLI
(`seed_content_drop.py`) owns the transaction.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.models.dimension import (
    KIND_FUNCTIONALITY, KIND_INDUSTRY, DimensionAlias, DimensionAssertion, DimensionTerm,
    normalize_value,
)
from app.models.editorial import PROVENANCE_IMPORTED, EditorialBlock, EditorialBlockVersion
from app.models.formula_template import FormulaTemplate
from app.models.index_data import CommodityIndex
from app.models.producer import ORIGIN_RESTRICTION, Producer, ProducerAlias, ProducerFormula
from app.models.taxonomy_v2 import Category, CategoryPlacement, Industry
from app.models.user import User
from app.services.drop.common import _slug
from app.services.drop.report import LoadReport
from app.services.content_drop.reader import app_region, loader_user_id, raw, tree
from app.services.content_drop.sanitize import served_body
from app.services.content_drop.taxonomy import _diff, _upsert
from app.services.editorial import add_version, create_block
from app.services.producers import match_form

# ── Vocabulary ───────────────────────────────────────────────────────────────

BLOCK_ROW_PREFIX = "editorial_blocks · "
VERSION_ROW = "editorial_block_versions"
RELOAD_NOTE = "reloaded from the content drop"

# (block_type, body_format), in report order. The public formula types: the
# editorial API serves exactly these for platform formula subjects.
FORMULA_BLOCKS = (
    ("functionalities", "json"),
    ("applications", "json"),
    ("supplier_note", "text"),
    ("compliance", "json"),
    ("macro_drivers", "json"),
    ("substitution", "json"),
    ("synthesis_route", "json"),
    ("current_events", "text"),
    ("negotiation_note", "text"),
)
INDEX_BLOCKS = (
    ("index_narrative", "json"),
    ("index_source_meta", "json"),
)
# Platform blocks of these types are deleted, with their versions, on every
# run: `suppliers` (producer_formulas is the only store of supplier rows) and
# `supply` / `demand` (splits the source ruled invented).
RETIRED_BLOCK_TYPES = ("suppliers", "supply", "demand")

# Where each dimension row and term comes from (dimension.SOURCES).
TERM_SOURCE = "taxonomy"
ASSERTION_SOURCE = "loader"
# producer_formulas / producers written by this loader.
PRODUCER_SOURCE = "loader"

CHECK_SHELLS = "check: shells (no template, no content)"
CHECK_SUPPLIER_ROWS = "check: supplier rows"
CHECK_COUNTING = "check: rows counting toward the floor"
CHECK_CANONICAL = "check: canonical producers"
CHECK_BUCKETS = "check: bucket producers"
CHECK_DEAD_ALIASES = "check: alias keys never reached"
CHECK_ADOPTED = "check: producers given their canonical name"
CHECK_INDEX_SUBJECTS = "check: index blocks with no series"


# ── Which cards get content ──────────────────────────────────────────────────

def shells() -> list[str]:
    """`GRP-*` cards with no AUTO_GROUPS entry: no template, so no content."""
    groups = raw("AUTO_GROUPS")
    return sorted(p for p in raw("CURATED_CONTENT") if p.startswith("GRP-") and p not in groups)


def content_codes() -> set[str]:
    """The PIDs whose blocks and supplier rows load: every template code
    (CURATED_CONTENT keys, FORMULA_COMBOS records, AUTO_GROUPS groups) minus
    the shells."""
    every = set(raw("CURATED_CONTENT")) | set(raw("FORMULA_COMBOS")) | set(raw("AUTO_GROUPS"))
    return every - set(shells())


# ── The canonicaliser (raw/_functions/CANONICALIZE_SUPPLIER.js) ──────────────

# `new RegExp(" " + String.fromCharCode(47) + " |, ")` — " / " or ", ".
SUPPLIER_SEP = re.compile(r" / |, ")
_SEP_KEEP = re.compile(r"( / |, )")

# Legal-form suffixes, compared with dots and spaces removed and casefolded.
# A fragment that is only one of these belongs to the fragment before it.
_LEGAL_SUFFIXES = frozenset({
    "ltd", "limited", "inc", "incorporated", "llc", "llp", "lp", "co", "corp",
    "corporation", "company", "plc", "ag", "se", "sa", "sas", "nv", "bv", "gmbh",
    "kgaa", "spa", "srl", "sl", "kk", "pte", "pteltd", "pvtltd", "privatelimited",
    "sdnbhd", "bhd", "tbk", "oyj", "asa", "a/s", "ab", "ltda", "sadecv", "decv",
    "coltd",
})


def _is_legal_suffix(fragment: str) -> bool:
    return re.sub(r"[.\s]", "", fragment.casefold()) in _LEGAL_SUFFIXES


def _split(base: str) -> list[str]:
    """`base.split(SEP)`, with a bare legal-suffix fragment put back on the
    fragment before it (with the separator it had)."""
    tokens = _SEP_KEEP.split(base)
    parts = [tokens[0]]
    for sep, fragment in zip(tokens[1::2], tokens[2::2]):
        if parts and parts[-1].strip() and _is_legal_suffix(fragment.strip()):
            parts[-1] = parts[-1] + sep + fragment
        else:
            parts.append(fragment)
    return parts


def supplier_fragments(raw_name: str, aliases: dict[str, str] | None = None
                       ) -> list[tuple[str, str]]:
    """`(fragment, canonical)` for every company a raw supplier string names —
    the canonicaliser's walk, keeping the string each name was read from.

    Same steps and order as the JS: trim; `base` is everything before the
    first `" ("`; a base holding a separator is split and each piece walked
    again; otherwise `A[n] || A[base] || base`, then one more `A[x] || x`.
    """
    a = aliases or {}
    n = (raw_name or "").strip()
    if not n:
        return []
    i = n.find(" (")
    base = n[:i].strip() if i > 0 else n
    pieces = _split(base)
    if len(pieces) > 1:
        out: list[tuple[str, str]] = []
        for piece in pieces:
            out += supplier_fragments(piece.strip(), a)
        return out
    x = a.get(n) or a.get(base) or base
    x = a.get(x) or x
    return [(n, x)]


def canonicalize_supplier(raw_name: str, aliases: dict[str, str] | None = None) -> list[str]:
    """CANONICALIZE_SUPPLIER(raw, ALIASES): the canonical company names a raw
    supplier string names, in order. Always a list."""
    return [canonical for _, canonical in supplier_fragments(raw_name, aliases)]


def dead_alias_keys(aliases: dict[str, str]) -> list[str]:
    """Alias keys the canonicaliser can never look up: their base holds a
    separator, so it splits them before any lookup."""
    dead = []
    for key in aliases:
        i = key.find(" (")
        base = key[:i].strip() if i > 0 else key
        if len(_split(base)) > 1:
            dead.append(key)
    return dead


def _alias_match_key(value: str, aliases: dict[str, str]) -> str:
    """`match_form(value)`, unless dropping the parenthetical changes the
    company — then the full normalised string."""
    i = value.find(" (")
    if i <= 0:
        return match_form(value)
    bare = value[:i].strip()
    with_paren = [normalize_value(c) for c in canonicalize_supplier(value, aliases)]
    without = [normalize_value(c) for c in canonicalize_supplier(bare, aliases)]
    return match_form(value) if with_paren == without else normalize_value(value)


# ── Maker evidence (design §2.4) ─────────────────────────────────────────────

def _floor_eu_false(row: dict) -> bool:
    fe = row.get("floor_eligibility")
    return isinstance(fe, dict) and fe.get("EU") is False


def counts_toward_floor(row: dict) -> bool:
    """The source's own floor predicate (its tools share it): a named maker,
    not a distributor, not EU-ineligible, not `counted: false`, evidence
    absent or VERIFIED."""
    return (bool((row.get("n") or "").strip())
            and row.get("role") != "distributor"
            and not _floor_eu_false(row)
            and row.get("counted") is not False
            and row.get("maker_evidence") in (None, "VERIFIED"))


def evidence_label(row: dict) -> str:
    """The short label code (models.producer.EVIDENCE_LABELS), from structured
    fields only, first match wins. Never parses the audit prose."""
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
    """One marker for both mechanisms: `origin_restriction`, or a row the
    source makes ineligible for the EU floor."""
    return ORIGIN_RESTRICTION if (row.get("origin_restriction") or _floor_eu_false(row)) else None


# The keys a stored site keeps. `area` only when the source states it (a
# derived area is the least-checked field); never `quote`, `source`, the
# original label or the basis fields.
SITE_KEYS = ("plant", "town", "country", "region", "scope", "status")


def normalise_sites(sites: Any) -> list[dict] | None:
    """The row's sites with only the keys above (and a stated `area`), empty
    values left out. None when no site is left."""
    out = []
    for site in sites or []:
        if not isinstance(site, dict):
            continue
        entry = {k: site[k] for k in SITE_KEYS if site.get(k) not in (None, "")}
        if site.get("area_basis") == "stated" and site.get("area") not in (None, ""):
            entry["area"] = site["area"]
        if entry and entry not in out:
            out.append(entry)
    return out or None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _link_fields(row: dict, row_order: int, template_id) -> dict:
    """The stored columns of one supplier row. Nothing else of the row is kept."""
    fe = row.get("floor_eligibility")
    return {
        "template_id": template_id,
        # `share: 0` means "not disclosed" and the rest are unsourced: no
        # share is stored.
        "share_pct": None,
        "share_disclosed": False,
        "hq_country": row.get("hq") or None,
        # Manufacturing regions, as given.
        "regions_raw": list(row.get("regs") or []) or None,
        "tags": list(row.get("tags") or []) or None,
        "raw_name": row["n"],
        "role": row.get("role") or None,
        "maker_evidence": row.get("maker_evidence") or None,
        "counted": _bool_or_none(row.get("counted")),
        "counts_toward_floor": counts_toward_floor(row),
        "evidence_label": evidence_label(row),
        "weak_reading": _bool_or_none(row.get("weak_reading")),
        "corp_group": row.get("group") or None,
        "integration_status": row.get("integration_status") or None,
        "integrated": _bool_or_none(row.get("integrated")),
        "integration_basis": row.get("integration_basis") or row.get("integration_why") or None,
        "origin_restriction": origin_restriction(row),
        "floor_eligible_eu": _bool_or_none(fe.get("EU")) if isinstance(fe, dict) else None,
        "region_uncertain": _bool_or_none(row.get("region_uncertain")),
        "sites": normalise_sites(row.get("sites")),
        "row_order": row_order,
        "source": PRODUCER_SOURCE,
    }


# ── What the drop says: editorial blocks ─────────────────────────────────────

def _union(*lists: list | None) -> list:
    """Concatenate, dropping exact repeats, first occurrence wins."""
    out: list = []
    for items in lists:
        for item in items or []:
            if item not in out:
                out.append(item)
    return out


def desired_blocks() -> tuple[list[dict], list[tuple[str | None, str, str]]]:
    """Every block the drop carries, in served form, and
    `(block_type, key, reason)` for what it withholds (`block_type` None: a
    shell, which gets no block of any type).

    Each block: `{subject_type, subject_code, block_type, region, body_format,
    body, source_note}`.
    """
    cc = raw("CURATED_CONTENT")
    fo = raw("FUTURE_OUTLOOK")
    routes = raw("SYNTHESIS_ROUTES")
    events = raw("CURRENT_EVENTS_OUTLOOK")
    formats = dict(FORMULA_BLOCKS + INDEX_BLOCKS)
    codes = content_codes()

    blocks: list[dict] = []
    withheld: list[tuple[str | None, str, str]] = []
    older_drivers: list[str] = []

    def add(subject_type: str, code: str, block_type: str, body, source: str,
            region: str | None = None) -> None:
        served = served_body(block_type, body)
        if served is None:
            if body is not None and served_body("_any", body) is not None:
                withheld.append((block_type, f"{code} {block_type}", "empty once cleaned"))
            return
        blocks.append({
            "subject_type": subject_type, "subject_code": code, "block_type": block_type,
            "region": region, "body_format": formats[block_type], "body": served,
            "source_note": source,
        })

    for pid in sorted(set(cc) | set(fo) | set(routes) | set(events)):
        if pid not in codes:
            withheld.append((None, pid, "a shell: no template, no content"))
            continue
        c, f = cc.get(pid) or {}, fo.get(pid) or {}
        add("formula", pid, "functionalities", c.get("functionalities"),
            "CURATED_CONTENT.functionalities")
        add("formula", pid, "applications", c.get("applications"),
            "CURATED_CONTENT.applications")
        add("formula", pid, "supplier_note", c.get("supplierNote"),
            "CURATED_CONTENT.supplierNote")
        add("formula", pid, "compliance", c.get("compliance"), "CURATED_CONTENT.compliance")

        # Macro drivers: FUTURE_OUTLOOK only (it covers every card and carries
        # `direction`); the older CURATED_CONTENT copies are not loaded.
        add("formula", pid, "macro_drivers", f.get("macroDrivers"), "FUTURE_OUTLOOK.macroDrivers")
        if served_body("macro_drivers", c.get("macroDrivers")) is not None:
            older_drivers.append(pid)

        # Substitution: FUTURE_OUTLOOK, else CURATED_CONTENT's copy.
        if served_body("substitution", f.get("substitution")) is not None:
            add("formula", pid, "substitution", f["substitution"], "FUTURE_OUTLOOK.substitution")
            if (served_body("substitution", c.get("substitution")) is not None
                    and c["substitution"] != f["substitution"]):
                withheld.append(("substitution", f"{pid} substitution",
                                 "CURATED_CONTENT differs from FUTURE_OUTLOOK; "
                                 "FUTURE_OUTLOOK loaded"))
        else:
            add("formula", pid, "substitution", c.get("substitution"),
                "CURATED_CONTENT.substitution")

        add("formula", pid, "synthesis_route", routes.get(pid), "SYNTHESIS_ROUTES")

        for region_key, text in (events.get(pid) or {}).items():
            if region_key.startswith("_"):
                withheld.append(("current_events", f"{pid} current_events",
                                 f"withdrawn in the drop ({region_key})"))
                continue
            region = None if region_key == "*" else app_region(region_key)
            if region_key != "*" and region is None:
                withheld.append(("current_events", f"{pid} current_events",
                                 f"region {region_key!r} has no app region"))
                continue
            add("formula", pid, "current_events", text,
                f"CURRENT_EVENTS_OUTLOOK[{region_key!r}]", region=region)

        add("formula", pid, "negotiation_note", c.get("negotiationNote"),
            "CURATED_CONTENT.negotiationNote")

    if older_drivers:
        # One line for them all: the cards are listed by `curated_macro_driver_pids`.
        withheld.append(("macro_drivers", "CURATED_CONTENT.macroDrivers",
                         f"{len(older_drivers)} cards: not loaded; FUTURE_OUTLOOK wins"))

    for key, entry in raw("INDEX_NARRATIVES").items():
        add("index", key, "index_narrative", entry, "INDEX_NARRATIVES")
    for key, entry in raw("INDEX_SOURCE_META").items():
        add("index", key, "index_source_meta", entry, "INDEX_SOURCE_META")
    return blocks, withheld


def curated_macro_driver_pids() -> list[str]:
    """The loaded cards whose older CURATED_CONTENT.macroDrivers are skipped."""
    cc = raw("CURATED_CONTENT")
    return sorted(p for p in content_codes()
                  if served_body("macro_drivers", (cc.get(p) or {}).get("macroDrivers")) is not None)


def _block_key(subject_type: str, subject_code: str, block_type: str, region: str | None):
    # The unique index folds the NULL region to '*'; so does this key.
    return (subject_type, subject_code, block_type, region or "*")


# ── Editorial blocks ─────────────────────────────────────────────────────────

def _retire_blocks(db: Session, report: LoadReport) -> None:
    """Delete every platform block of a retired type, with its versions.

    One statement per table. The block's versions go with it (their
    `block_id` cascades); a team fork keeps its own text (`origin_id` is set
    NULL by the database).
    """
    versions = _diff(report, VERSION_ROW)
    for block_type in RETIRED_BLOCK_TYPES:
        ids = [i for (i,) in db.query(EditorialBlock.id).filter(
            EditorialBlock.team_id.is_(None), EditorialBlock.block_type == block_type)]
        if not ids:
            continue
        # "fetch": any of these rows already in the session leave it too.
        versions.deleted += (db.query(EditorialBlockVersion)
                             .filter(EditorialBlockVersion.block_id.in_(ids))
                             .delete(synchronize_session="fetch"))
        _diff(report, f"{BLOCK_ROW_PREFIX}{block_type}").deleted += (
            db.query(EditorialBlock).filter(EditorialBlock.id.in_(ids))
            .delete(synchronize_session="fetch"))
    db.flush()


def _load_blocks(db: Session, report: LoadReport, author: User,
                 templates: dict[str, Any]) -> None:
    for block_type, _ in FORMULA_BLOCKS + INDEX_BLOCKS:
        _diff(report, f"{BLOCK_ROW_PREFIX}{block_type}")
    versions = _diff(report, VERSION_ROW)
    _retire_blocks(db, report)

    blocks, withheld = desired_blocks()
    shell_skips = [key for block_type, key, _why in withheld if block_type is None]
    check = _diff(report, CHECK_SHELLS)
    check.unchanged = len(shell_skips)
    check.skipped = [(key, "GRP-* card with no AUTO_GROUPS entry: no template, so no blocks "
                           "and no supplier rows") for key in shell_skips]
    for block_type, key, why in withheld:
        if block_type is not None:
            _diff(report, f"{BLOCK_ROW_PREFIX}{block_type}").skipped.append((key, why))

    series = dict(db.query(CommodityIndex.commodity_key, CommodityIndex.id).filter(
        CommodityIndex.commodity_key.isnot(None)))
    # The current version by the pointer column, not the viewonly relationship:
    # the relationship is not refreshed when add_version repoints a block that
    # is already in the session.
    current: dict[tuple, EditorialBlock] = {}
    current_version: dict[tuple, EditorialBlockVersion | None] = {}
    for b, v in (db.query(EditorialBlock, EditorialBlockVersion)
                 .outerjoin(EditorialBlockVersion,
                            EditorialBlockVersion.id == EditorialBlock.current_version_id)
                 .filter(EditorialBlock.team_id.is_(None))):
        key = _block_key(b.subject_type, b.subject_code, b.block_type, b.region)
        current[key] = b
        current_version[key] = v

    no_series = []
    for spec in blocks:
        diff = _diff(report, f"{BLOCK_ROW_PREFIX}{spec['block_type']}")
        is_json = spec["body_format"] == "json"
        body_text = None if is_json else spec["body"]
        body_json = spec["body"] if is_json else None
        key = _block_key(spec["subject_type"], spec["subject_code"], spec["block_type"],
                         spec["region"])
        if spec["subject_type"] == "index" and spec["subject_code"] not in series:
            no_series.append(spec["subject_code"])

        block = current.get(key)
        if block is None:
            # Never a duplicate here (`current` holds every platform block), so
            # create_block's IntegrityError path, which rolls the session back,
            # is never reached.
            block = create_block(
                db, team_id=None, subject_type=spec["subject_type"],
                subject_code=spec["subject_code"], block_type=spec["block_type"],
                region=spec["region"], body_text=body_text, body_json=body_json,
                body_format=spec["body_format"], provenance=PROVENANCE_IMPORTED,
                source_note=spec["source_note"], author=author,
            )
            current[key] = block
            diff.created += 1
            versions.created += 1
            continue

        changed = False
        # The convenience joins follow the catalogue and the series as loaded.
        if spec["subject_type"] == "formula":
            links = {"template_id": templates.get(spec["subject_code"])}
        else:
            links = {"commodity_id": series.get(spec["subject_code"])}
        for name, value in links.items():
            if getattr(block, name) != value:
                setattr(block, name, value)
                changed = True

        if block.provenance != PROVENANCE_IMPORTED:
            diff.skipped.append((f"{spec['subject_code']} {spec['block_type']}",
                                 f"{block.provenance} on the platform since import; "
                                 "text left as is"))
        else:
            if block.source_note != spec["source_note"]:
                block.source_note = spec["source_note"]
                changed = True
            version = current_version.get(key)
            if (version is None or version.body_format != spec["body_format"]
                    or version.body != spec["body"]):
                add_version(db, block, body_text=body_text, body_json=body_json,
                            body_format=spec["body_format"],
                            provenance=PROVENANCE_IMPORTED, change_note=RELOAD_NOTE,
                            author=author)
                versions.created += 1
                changed = True
        if changed:
            diff.updated += 1
        else:
            diff.unchanged += 1

    desired_keys = {_block_key(s["subject_type"], s["subject_code"], s["block_type"],
                               s["region"]) for s in blocks}
    ours = {t for t, _ in FORMULA_BLOCKS + INDEX_BLOCKS}
    for key, block in current.items():
        if key not in desired_keys and block.block_type in ours:
            _diff(report, f"{BLOCK_ROW_PREFIX}{block.block_type}").stale += 1

    check = _diff(report, CHECK_INDEX_SUBJECTS)
    check.unchanged = len(no_series)
    check.skipped = [(k, "loaded with commodity_id NULL: no FIDX series")
                     for k in sorted(set(no_series))]
    db.flush()


# ── Dimensions ───────────────────────────────────────────────────────────────

def tree_functions() -> list[str]:
    """The distinct `fn` of the demand tree's categories, sorted."""
    return sorted({cat["fn"] for branch in tree()["industries"].values()
                   for cat in branch.get("categories") or [] if cat.get("fn")})


def _load_dimensions(db: Session, report: LoadReport,
                     templates: dict[str, Any]) -> None:
    tdiff = _diff(report, "dimension_terms")
    adiff = _diff(report, "dimension_aliases")
    idiff = _diff(report, "dimension_assertions · industry")
    fdiff = _diff(report, "dimension_assertions · functionality")

    industries = db.query(Industry).order_by(Industry.sort_order, Industry.name).all()
    if not industries:
        tdiff.skipped.append(("industry", "industries table is empty; run the taxonomy "
                                          "loader first"))
    desired: dict[tuple[str, str], dict] = {}
    for ind in industries:
        desired[(KIND_INDUSTRY, ind.slug)] = {"label": ind.name, "sort_order": ind.sort_order}
    fn_code: dict[str, str] = {}
    for pos, fn in enumerate(tree_functions()):
        code = _slug(fn)
        if (KIND_FUNCTIONALITY, code) in desired:
            tdiff.skipped.append((fn, f"slug {code!r} already names another function"))
            continue
        fn_code[fn] = code
        desired[(KIND_FUNCTIONALITY, code)] = {"label": fn, "sort_order": pos}

    kinds = (KIND_INDUSTRY, KIND_FUNCTIONALITY)
    current = {(t.kind, t.code): t for t in db.query(DimensionTerm).filter(
        DimensionTerm.team_id.is_(None), DimensionTerm.kind.in_(kinds))}
    for (kind, code), spec in desired.items():
        fields = {**spec, "is_active": True, "source": TERM_SOURCE}
        _upsert(db, tdiff, current, (kind, code), fields,
                lambda f, k=kind, c=code: DimensionTerm(team_id=None, kind=k, code=c, **f))
    # Terms the tree no longer names: kept, switched off.
    for key, term in current.items():
        if key in desired:
            continue
        tdiff.stale += 1
        if term.source == TERM_SOURCE and term.is_active:
            term.is_active = False
            tdiff.updated += 1
    db.flush()
    terms = {key: current[key] for key in desired}

    # Every label is its own alias (the tree and the tables use it verbatim).
    alias_rows = {(a.kind, a.normalized): a for a in db.query(DimensionAlias).filter(
        DimensionAlias.team_id.is_(None), DimensionAlias.kind.in_(kinds))}
    wanted_aliases = set()
    for (kind, code), term in terms.items():
        norm = normalize_value(term.label)
        wanted_aliases.add((kind, norm))
        _upsert(db, adiff, alias_rows, (kind, norm),
                {"term_id": term.id, "raw_value": term.label, "source": TERM_SOURCE},
                lambda f, k=kind, n=norm: DimensionAlias(team_id=None, kind=k,
                                                         normalized=n, **f))
    adiff.stale += len(set(alias_rows) - wanted_aliases)
    db.flush()

    # The placement projection.
    slug_by_id = {ind.id: ind.slug for ind in industries}
    by_industry: dict[tuple[str, str], set] = defaultdict(set)
    by_fn: dict[tuple[str, str], set] = defaultdict(set)
    placements = (db.query(CategoryPlacement.pid, CategoryPlacement.industry_id,
                           CategoryPlacement.fn, Category.code)
                  .join(Category, Category.id == CategoryPlacement.category_id).all())
    if not placements:
        idiff.skipped.append(("category_placements", "no placements; run the taxonomy "
                                                     "loader first"))
    for pid, industry_id, fn, code in placements:
        slug = slug_by_id.get(industry_id)
        if slug is not None:
            by_industry[(pid, slug)].add(code)
        if fn in fn_code:
            by_fn[(pid, fn_code[fn])].add(code)
        elif fn:
            fdiff.skipped.append((f"{pid} / {code}", f"fn {fn!r} is not a tree function"))

    term_ids = {t.id for t in terms.values()}
    existing = {}
    for a in db.query(DimensionAssertion).filter(
            DimensionAssertion.team_id.is_(None), DimensionAssertion.term_id.in_(term_ids)):
        existing[(a.term_id, a.subject_type, a.subject_code, a.region or "*")] = a

    wanted = set()
    for kind, pairs, diff in ((KIND_INDUSTRY, by_industry, idiff),
                              (KIND_FUNCTIONALITY, by_fn, fdiff)):
        for (pid, code), categories in sorted(pairs.items()):
            term = terms.get((kind, code))
            if term is None:
                continue
            key = (term.id, "formula", pid, "*")
            wanted.add(key)
            alias = alias_rows[(kind, normalize_value(term.label))]
            fields = {
                "raw_value": term.label,
                "matched_alias_id": alias.id,
                "source": ASSERTION_SOURCE,
                "detail": {"categories": sorted(categories)},
                "template_id": templates.get(pid),
            }
            _upsert(db, diff, existing, key, fields,
                    lambda f, t=term.id, p=pid: DimensionAssertion(
                        team_id=None, term_id=t, subject_type="formula",
                        subject_code=p, region=None, **f))

    kind_of = {t.id: t.kind for t in terms.values()}
    for key, row in list(existing.items()):
        if key in wanted:
            continue
        diff = idiff if kind_of.get(row.term_id) == KIND_INDUSTRY else fdiff
        if (row.source == ASSERTION_SOURCE and row.subject_type == "formula"
                and row.region is None):
            db.delete(row)
            existing.pop(key)
            diff.deleted += 1
        else:
            diff.stale += 1
    db.flush()


# ── Producers ────────────────────────────────────────────────────────────────

def _adopt(db: Session, report: LoadReport, current: dict[str, Producer],
           names: dict[str, str]) -> None:
    """Give an existing producer its canonical name instead of minting a twin.

    Another path may already hold the company under a qualified name: the
    dossier loader's resolver keeps the parenthetical when it mints, while its
    alias row is keyed on the bare name. A second row for the canonical name
    would make every lookup of that name return two companies. So when no
    producer carries the canonical name and exactly one producer answers to it
    through `producer_aliases.match_key`, that producer is renamed to the
    canonical name and used. Never one that is itself another canonical
    company.
    """
    check = _diff(report, CHECK_ADOPTED)
    by_match: dict[str, set] = defaultdict(set)
    for match_key, producer_id in db.query(ProducerAlias.match_key, ProducerAlias.producer_id):
        by_match[match_key].add(producer_id)
    by_id = {p.id: p for p in current.values()}
    for norm, name in names.items():
        if norm in current:
            continue
        found = by_match.get(match_form(name), set())
        if len(found) != 1:
            continue
        producer = by_id.get(next(iter(found)))
        if producer is None or producer.normalized_name in names:
            continue
        check.skipped.append((producer.name, f"renamed {name!r}, its canonical name"))
        current.pop(producer.normalized_name)
        producer.normalized_name = norm
        current[norm] = producer
    check.unchanged = len(check.skipped)
    db.flush()


def _supplier_rows(codes: set[str]) -> list[tuple[str, int, dict]]:
    """`(pid, row_order, row)` for every named supplier row of the loaded cards,
    in card order then authored order."""
    cc = raw("CURATED_CONTENT")
    out = []
    for pid in sorted(codes & set(cc)):
        for pos, row in enumerate(cc[pid].get("suppliers") or []):
            if isinstance(row, dict) and (row.get("n") or "").strip():
                out.append((pid, pos, row))
    return out


def _load_producers(db: Session, report: LoadReport, templates: dict[str, Any]) -> None:
    pdiff = _diff(report, "producers")
    adiff = _diff(report, "producer_aliases")
    fdiff = _diff(report, "producer_formulas")

    aliases = raw("SUPPLIER_ALIASES")
    buckets = set(raw("SUPPLIER_BUCKETS"))
    cc = raw("CURATED_CONTENT")
    codes = content_codes()

    # 1. Every supplier row of a loaded card through the canonicaliser.
    rows: list[tuple[str, int, dict, list[tuple[str, str]]]] = []
    names: dict[str, str] = {}              # normalized canonical → display name
    hq_votes: dict[str, Counter] = defaultdict(Counter)
    for pid, pos, supplier in _supplier_rows(codes):
        fragments = supplier_fragments(supplier["n"], aliases)
        rows.append((pid, pos, supplier, fragments))
        for _, canonical in fragments:
            names.setdefault(normalize_value(canonical), canonical)
        # A producer's own HQ only from rows that name that one company.
        if len(fragments) == 1 and supplier.get("hq"):
            hq_votes[normalize_value(fragments[0][1])][supplier["hq"]] += 1
    _diff(report, CHECK_SUPPLIER_ROWS).unchanged = len(rows)
    _diff(report, CHECK_COUNTING).unchanged = sum(
        1 for _p, _o, supplier, _f in rows if counts_toward_floor(supplier))
    _diff(report, CHECK_CANONICAL).unchanged = len(names)
    bucket_keys = {normalize_value(b) for b in buckets}
    _diff(report, CHECK_BUCKETS).unchanged = len(bucket_keys & set(names))
    dead = _diff(report, CHECK_DEAD_ALIASES)
    dead.skipped = [(key, "holds a separator; the canonicaliser splits before it looks up")
                    for key in dead_alias_keys(aliases)]
    dead.unchanged = len(dead.skipped)

    # 2. One producer per canonical name.
    current = {p.normalized_name: p for p in db.query(Producer)}
    _adopt(db, report, current, names)
    for norm, name in names.items():
        fields: dict[str, Any] = {"name": name, "is_bucket": norm in bucket_keys}
        if hq_votes.get(norm):
            # Most frequent, ties to the first seen (Counter keeps insertion order).
            fields["hq_country"] = hq_votes[norm].most_common(1)[0][0]
        _upsert(db, pdiff, current, norm, fields,
                lambda f, n=norm: Producer(normalized_name=n, source=PRODUCER_SOURCE, **f))
    pdiff.stale += sum(1 for norm, p in current.items()
                       if norm not in names and p.source == PRODUCER_SOURCE)
    db.flush()
    producers = {norm: current[norm] for norm in names}

    # 3. Aliases: each raw string and each fragment → the producer it names.
    # First spelling seen wins; an existing row for the same (string,
    # producer) is the same fact and is left as it is.
    wanted: dict[tuple[str, Any], tuple[str, str]] = {}
    for _pid, _pos, supplier, fragments in rows:
        split = len(fragments) > 1
        for fragment, canonical in fragments:
            producer_id = producers[normalize_value(canonical)].id
            mapped = normalize_value(canonical) != match_form(fragment)
            wanted.setdefault((normalize_value(fragment), producer_id),
                              (fragment, "split" if split else
                               ("alias_map" if mapped else "raw")))
            if fragment != supplier["n"]:
                wanted.setdefault((normalize_value(supplier["n"]), producer_id),
                                  (supplier["n"], "split"))
    existing_aliases = {(a.normalized, a.producer_id) for a in db.query(ProducerAlias)}
    for (normalized, producer_id), (value, source) in wanted.items():
        if (normalized, producer_id) in existing_aliases:
            adiff.unchanged += 1
            continue
        db.add(ProducerAlias(producer_id=producer_id, raw_value=value, normalized=normalized,
                             match_key=_alias_match_key(value, aliases), source=source))
        adiff.created += 1
    db.flush()

    # 4. What each producer makes: one row per (producer, PID), with the
    # evidence of the first row that names it.
    desired: dict[tuple[Any, str], dict] = {}
    for pid, pos, supplier, fragments in rows:
        fields = _link_fields(supplier, pos, templates.get(pid))
        for producer_id in dict.fromkeys(producers[normalize_value(c)].id
                                         for _, c in fragments):
            key = (producer_id, pid)
            first = desired.get(key)
            if first is None:
                desired[key] = dict(fields)
                continue
            # A later row naming the same producer on this card: the first
            # row's fields and position stand; regions, tags and sites are
            # united.
            first["regions_raw"] = _union(first["regions_raw"], fields["regions_raw"]) or None
            first["tags"] = _union(first["tags"], fields["tags"]) or None
            first["sites"] = _union(first["sites"], fields["sites"]) or None
            fdiff.skipped.append((f"{pid} / {supplier['n']}",
                                  f"names the same producer as {first['raw_name']!r}; "
                                  "merged into that row (regions, tags and sites united)"))

    current_links = {(pf.producer_id, pf.subject_code): pf for pf in db.query(ProducerFormula)
                     .filter(ProducerFormula.region.is_(None))}
    for (producer_id, pid), fields in desired.items():
        _upsert(db, fdiff, current_links, (producer_id, pid), fields,
                lambda f, p=producer_id, s=pid: ProducerFormula(
                    producer_id=p, subject_code=s, region=None, **f))
    # The drop lists each card's suppliers in full: a loader row on a drop
    # card that it no longer names is gone (a shell's list is not loaded).
    stated = set(cc) | codes
    for key, row in list(current_links.items()):
        if key in desired or row.source != PRODUCER_SOURCE:
            continue
        if row.subject_code in stated:
            db.delete(row)
            current_links.pop(key)
            fdiff.deleted += 1
        else:
            fdiff.stale += 1
    db.flush()


# ── Entry point ──────────────────────────────────────────────────────────────

def load(db: Session, report: LoadReport) -> LoadReport:
    """Load the editorial blocks, the dimensions and the producers. Flushes,
    never commits."""
    # Anything another loader left pending fails here, not inside
    # create_block's IntegrityError handler (which rolls the session back).
    db.flush()
    author = db.get(User, loader_user_id(db))
    templates = dict(db.query(FormulaTemplate.code, FormulaTemplate.id).filter(
        FormulaTemplate.team_id.is_(None), FormulaTemplate.code.isnot(None)))
    _load_blocks(db, report, author, templates)
    _load_dimensions(db, report, templates)
    _load_producers(db, report, templates)
    return report
