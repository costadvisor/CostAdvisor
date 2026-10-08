"""The content loader (app/services/content_drop/content.py) and its served
form (app/services/content_drop/sanitize.py).

Editorial blocks through the editorial service, written in served form (one
per public prose structure per card, version 1, provenance `imported`); no
`suppliers`, `supply` or `demand` block; the industry and function facets with
assertions projected from the stored placements; the canonical producers with
the maker evidence of design §2.4 on every supplier row, and nothing else of
the row; a second load changes nothing.

Expected figures come from `tests/content_drop_expect.py`, which reads the drop
files with its own implementation of the rules, never from the loader. No drop
text and no count lives in this file: names, codes and texts are read from the
drop at run time, and the pure tests use made-up strings.

The database tests need a database built from the current drop
(`content_loaded`). The module works in one transaction and rolls it back at
the end, so the reload and repair tests leave nothing behind.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict

import pytest

import seed_content_drop
from app.database import SessionLocal, bypass_rls_var
from app.models.dimension import (
    KIND_FUNCTIONALITY, KIND_INDUSTRY, DimensionAlias, DimensionAssertion, DimensionTerm,
    normalize_value,
)
from app.models.editorial import EditorialBlock, EditorialBlockVersion
from app.models.formula_template import FormulaTemplate
from app.models.index_data import CommodityIndex
from app.models.producer import EVIDENCE_LABELS, Producer, ProducerAlias, ProducerFormula
from app.models.taxonomy_v2 import Category, CategoryPlacement, Industry
from app.services.drop.report import LoadReport
from app.services.content_drop import content, sanitize
from app.services.editorial import add_version, read_card
# Side-effect import: region auto-register (APAC / MEA on flush).
from app.services import regions as _region_events  # noqa: F401
from tests import content_drop_expect as expect

PUBLIC_TYPES = {t for t, _ in content.FORMULA_BLOCKS}
INDEX_TYPES = {t for t, _ in content.INDEX_BLOCKS}
# The columns a loader row may fill (design §2.4), besides its keys.
LINK_COLUMNS = {
    "template_id", "share_pct", "share_disclosed", "hq_country", "regions_raw", "tags",
    "raw_name", "role", "maker_evidence", "counted", "counts_toward_floor", "evidence_label",
    "weak_reading", "corp_group", "integration_status", "integrated", "integration_basis",
    "origin_restriction", "floor_eligible_eu", "region_uncertain", "sites", "row_order",
    "source",
}
SITE_KEYS = {"plant", "town", "country", "region", "scope", "status", "area"}
# Supplier-row fields that are never stored (quotes, sources, shares, audit trail).
NEVER_STORED = (
    "maker_quote", "maker_source", "count_why", "role_changed", "resolve_note", "weak_note",
    "browser_note", "evidence_note", "grade_note", "integration_note", "added", "n_was",
    "role_why", "role_source", "integration_source", "renamed", "merged",
)
# Authored text kept as written (T6): may share words with a quote.
AUTHORED_TEXT = ("tags", "integration_basis", "sites")
URL = re.compile(r"https?://|www\.", re.IGNORECASE)
MIN_QUOTE_LEN = 20


# ── Drop helpers (read at run time) ─────────────────────────────────────────

def _cards() -> dict:
    return expect.raw("CURATED_CONTENT")


def _rows(pid: str) -> list[dict]:
    return [r for r in _cards()[pid].get("suppliers") or []
            if isinstance(r, dict) and (r.get("n") or "").strip()]


def _canon(raw_name: str) -> list[str]:
    return content.canonicalize_supplier(raw_name, expect.raw("SUPPLIER_ALIASES"))


def _expected_links() -> dict[tuple[str, str], int]:
    """(pid, normalised producer) → the row_order of the first row naming it."""
    out: dict[tuple[str, str], int] = {}
    for pid in expect.content_codes():
        for row in expect.supplier_rows(pid):
            if not row.raw_name.strip():
                continue
            for name in _canon(row.raw_name):
                out.setdefault((pid, normalize_value(name)), row.row_order)
    return out


def _merged_rows() -> set[str]:
    """`pid / raw name` of every row naming a producer an earlier row on the
    same card already named (the loader merges it into the first)."""
    out = set()
    for pid in expect.content_codes():
        seen: set[str] = set()
        for row in _rows(pid):
            names = {normalize_value(n) for n in _canon(row["n"])}
            if seen & names:
                out.add(f"{pid} / {row['n']}")
            seen |= names
    return out


def _quotes_and_sources(pid: str) -> set[str]:
    """Every quote and source value on a card's supplier rows."""
    out = set()
    for row in _cards().get(pid, {}).get("suppliers") or []:
        for key in ("maker_quote", "maker_source"):
            if isinstance(row.get(key), str) and row[key].strip():
                out.add(row[key].strip())
        for site in row.get("sites") or []:
            for key in ("quote", "source"):
                if isinstance(site, dict) and isinstance(site.get(key), str) and site[key].strip():
                    out.add(site[key].strip())
    return out


def _audit_values(pid: str) -> set[str]:
    """The prose leaves (20 characters or more) of every never-stored audit
    field on a card's rows. Short leaves are codes (`producer`, `VERIFIED`)
    that a stored column may rightly hold."""
    out: set[str] = set()

    def leaves(value):
        if isinstance(value, str) and len(value.strip()) >= MIN_QUOTE_LEN:
            out.add(value.strip())
        elif isinstance(value, dict):
            for v in value.values():
                leaves(v)
        elif isinstance(value, list):
            for v in value:
                leaves(v)

    for row in _cards().get(pid, {}).get("suppliers") or []:
        for key in NEVER_STORED:
            if key not in ("maker_quote", "maker_source"):
                leaves(row.get(key))
    return out


def _authored_values(pid: str) -> set[str]:
    """The string values a card's rows give the stored columns. A quote that is
    only a plant name, say, is also a stored value: no leak."""
    out: set[str] = set()
    for row in _cards().get(pid, {}).get("suppliers") or []:
        for key in ("n", "hq", "group", "role", "maker_evidence", "integration_status",
                    "integration_basis", "integration_why"):
            if isinstance(row.get(key), str):
                out.add(row[key].strip())
        out |= {t.strip() for t in row.get("tags") or [] if isinstance(t, str)}
        for site in row.get("sites") or []:
            if isinstance(site, dict):
                out |= {str(site[k]).strip() for k in SITE_KEYS if site.get(k) is not None}
    return out


def _string_leaves(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _string_leaves(v)]
    if isinstance(value, list):
        return [s for v in value for s in _string_leaves(v)]
    return []


def _private_key(value) -> bool:
    if isinstance(value, dict):
        return any(str(k).startswith("_") or _private_key(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_private_key(v) for v in value)
    return False


# ── Database helpers ─────────────────────────────────────────────────────────

def _total(report: LoadReport, table: str) -> int:
    diff = report.table(table)
    return diff.created + diff.updated + diff.unchanged


def _block(db, subject_type: str, code: str, block_type: str, region=None) -> EditorialBlock:
    q = db.query(EditorialBlock).filter(
        EditorialBlock.team_id.is_(None), EditorialBlock.subject_type == subject_type,
        EditorialBlock.subject_code == code, EditorialBlock.block_type == block_type)
    q = q.filter(EditorialBlock.region.is_(None)) if region is None else \
        q.filter(EditorialBlock.region == region)
    return q.one()


def _body(db, block: EditorialBlock):
    return db.get(EditorialBlockVersion, block.current_version_id).body


def _template(db, code: str) -> FormulaTemplate:
    return db.query(FormulaTemplate).filter(FormulaTemplate.team_id.is_(None),
                                            FormulaTemplate.code == code).one()


def _producer(db, name: str) -> Producer:
    return db.query(Producer).filter(Producer.normalized_name == normalize_value(name)).one()


def _links(db, pid: str) -> dict[str, ProducerFormula]:
    rows = (db.query(ProducerFormula, Producer.normalized_name)
            .join(Producer, Producer.id == ProducerFormula.producer_id)
            .filter(ProducerFormula.subject_code == pid, ProducerFormula.region.is_(None)))
    return {name: pf for pf, name in rows}


def _loader_links(db):
    return (db.query(ProducerFormula, Producer.normalized_name)
            .join(Producer, Producer.id == ProducerFormula.producer_id)
            .filter(ProducerFormula.source == content.PRODUCER_SOURCE,
                    ProducerFormula.region.is_(None)).all())


@pytest.fixture(scope="module")
def loaded(content_loaded):
    """One session and one transaction for the module, on a database built
    from this drop: load the content twice more, yield, roll back."""
    token = bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        first = content.load(db, LoadReport(title="test content 1"))
        db.flush()
        second = content.load(db, LoadReport(title="test content 2"))
        db.flush()
        yield db, first, second
    finally:
        db.rollback()
        db.close()
        bypass_rls_var.reset(token)


# ── The served form (pure, made-up text) ─────────────────────────────────────

def test_private_keys_go_at_any_depth():
    body = {"a": 1, "_b": 2, "c": [{"d": 1, "_e": {"f": 1}}, "_g"], "h": {"_i": 1, "j": [1]}}
    assert sanitize.strip_private(body) == {"a": 1, "c": [{"d": 1}, "_g"], "h": {"j": [1]}}


def test_applications_without_a_receiver_are_dropped():
    apps = [{"industry": "Made-up industry A", "items": ["x"], "_added": "made-up note"},
            {"industry": "Made-up industry B", "_no_receiver": True},
            "a bare string stays"]
    assert sanitize.served_applications(apps) == [
        {"industry": "Made-up industry A", "items": ["x"]}, "a bare string stays"]
    assert sanitize.served_body("applications",
                                [{"industry": "Made-up", "_no_receiver": True}]) is None


def test_a_bare_compliance_string_gets_the_entry_shape():
    entries = ["Made-up register entry", {"flag": "Listed", "type": "ok", "name": "Made-up",
                                          "_note": "internal"}]
    assert sanitize.served_compliance(entries) == [
        {"flag": None, "type": "info", "name": "Made-up register entry", "desc": None,
         "bare": True},
        {"flag": "Listed", "type": "ok", "name": "Made-up"},
    ]


@pytest.mark.parametrize("raw, shown", [
    ("Made-up maker leads. Exact shares are not public; treat share:0 as \"not disclosed\".",
     "Made-up maker leads. Exact shares are not public."),
    ("Made-up maker leads. Treat share:0 as not disclosed.", "Made-up maker leads."),
    ("Made-up maker leads; share:0 means not disclosed throughout.", "Made-up maker leads."),
    ("Made-up maker leads - share:0 = not disclosed.", "Made-up maker leads."),
    ("Shares are private, so share:0 applies to every row, and note that a made-up "
     "plant closed.", "Shares are private. Note that a made-up plant closed."),
    ("Treat share:0 as not disclosed.", None),
    ("Nothing to remove here.", "Nothing to remove here."),
    (None, None),
])
def test_the_supplier_note_loses_the_share_instruction(raw, shown):
    assert sanitize.clean_supplier_note(raw) == shown
    # Idempotent: the readers may apply it again.
    assert sanitize.clean_supplier_note(shown) == shown


def test_current_events_read_related_product():
    assert sanitize.clean_current_events("Made-up: a sibling PID and two sibling PIDs.") == \
        "Made-up: a related product and two related products."
    assert sanitize.served_body("current_events", "  ") is None


def test_no_loaded_note_keeps_the_share_instruction():
    """Every loaded supplier note, once cleaned, is free of the instruction."""
    for pid in expect.content_codes():
        note = sanitize.clean_supplier_note(_cards()[pid].get("supplierNote"))
        assert not re.search(r"share\s*:\s*0", note or "", re.IGNORECASE), pid


# ── The canonicaliser (pure) ─────────────────────────────────────────────────

def test_the_canonicaliser_is_the_drops_own_function():
    # Made-up names and a made-up alias map; the drop's own map is checked below.
    aliases = {"Maker Q AG": "Maker Q", "Maker R": "Maker R", "Maker S / Maker T": "Maker S",
               "Maker U Co., Ltd.": "Maker U"}
    canon = content.canonicalize_supplier
    # Split on " / " and ", ", every piece through the alias map.
    assert canon("Maker A, Maker B, Maker C", aliases) == ["Maker A", "Maker B", "Maker C"]
    assert canon("Maker D / Maker E", aliases) == ["Maker D", "Maker E"]
    # The parenthetical goes before the lookup; the map runs on the full string first.
    assert canon("Maker Q AG (Test Division)", aliases) == ["Maker Q"]
    assert canon("Maker R (test line)", aliases) == ["Maker R"]
    # A key holding a separator is never reached: the function splits first.
    assert canon("Maker S / Maker T", aliases) == ["Maker S", "Maker T"]
    # The one addition: a bare legal suffix is not a company.
    assert canon("Maker U Co., Ltd.", aliases) == ["Maker U"]
    assert canon("Acme, Inc.", {}) == ["Acme, Inc."]
    assert canon("Foo Chemical Co., Ltd. / Bar", {}) == ["Foo Chemical Co., Ltd.", "Bar"]
    assert canon("", aliases) == [] and canon(None, aliases) == [] and canon("  ", aliases) == []

    dead = content.dead_alias_keys(aliases)
    assert "Maker S / Maker T" in dead and "Maker U Co., Ltd." not in dead
    drop_aliases = expect.raw("SUPPLIER_ALIASES")
    assert all(k in drop_aliases and (" / " in k or ", " in k)
               for k in content.dead_alias_keys(drop_aliases))


def test_the_loader_canonicaliser_differs_from_the_drops_only_on_legal_suffixes():
    """On every loaded supplier row, the loader's names equal the drop's own
    canonicaliser's, except where the drop's split mints a bare legal suffix."""
    aliases = expect.raw("SUPPLIER_ALIASES")
    for pid in expect.content_codes():
        for row in _rows(pid):
            ours = content.canonicalize_supplier(row["n"], aliases)
            theirs = expect.canonicalize_supplier(row["n"], aliases)
            if ours != theirs:
                assert any(content._is_legal_suffix(n) for n in theirs), (pid, row["n"])


def test_the_harness_finds_the_loader():
    assert seed_content_drop.resolve_loader("content") is content


# ── What the loader means to write (pure) ────────────────────────────────────

def test_shells_get_no_content():
    assert content.shells() == expect.shells()
    assert content.content_codes() == set(expect.content_codes())


def test_the_desired_blocks_are_the_expected_ones_in_served_form():
    blocks, _ = content.desired_blocks()
    keys = {(b["subject_type"], b["subject_code"], b["block_type"], b["region"]) for b in blocks}
    assert len(keys) == len(blocks)
    assert keys == expect.expected_block_keys()
    assert Counter(b["block_type"] for b in blocks) == expect.expected_block_counts()
    assert not {b["block_type"] for b in blocks} & set(content.RETIRED_BLOCK_TYPES)
    assert not [b["subject_code"] for b in blocks if _private_key(b["body"])]


def test_maker_evidence_follows_the_expectation_on_every_row():
    for pid in expect.content_codes():
        authored = _cards()[pid].get("suppliers") or []
        for row in expect.supplier_rows(pid):
            if not row.raw_name.strip():
                continue
            fields = content._link_fields(authored[row.row_order], row.row_order, None)
            got = {k: fields[k] for k in ("counts_toward_floor", "evidence_label",
                                          "origin_restriction", "floor_eligible_eu", "counted",
                                          "maker_evidence", "weak_reading", "region_uncertain",
                                          "row_order", "raw_name")}
            want = {"counts_toward_floor": row.counts_toward_floor,
                    "evidence_label": row.evidence_label,
                    "origin_restriction": row.origin_restriction,
                    "floor_eligible_eu": row.floor_eligible_eu, "counted": row.counted,
                    "maker_evidence": row.maker_evidence, "weak_reading": row.weak_reading,
                    "region_uncertain": row.region_uncertain, "row_order": row.row_order,
                    "raw_name": row.raw_name}
            assert got == want, (pid, row.row_order)
            assert fields["evidence_label"] in EVIDENCE_LABELS


def test_a_link_stores_nothing_but_its_columns():
    """A made-up row carrying every field that must not be stored."""
    row = {
        "n": "Maker Z", "hq": "Nowhere", "share": 37, "regs": ["EU"], "tags": ["made-up tag"],
        "role": "producer", "integrated": True, "integration_status": "PARTIALLY_INTEGRATED",
        "integration_why": "made-up why", "maker_evidence": "VERIFIED", "group": "Maker Z Group",
        "region_uncertain": False, "counted": True, "weak_reading": False,
        "floor_eligibility": {"EU": False, "why": "made-up ruling text"},
        "maker_quote": "made-up quote text from a page", "maker_source": "https://made.up/page",
        "count_why": "made-up audit narration", "role_changed": [{"by": "someone"}],
        "resolve_note": "made-up", "weak_note": "made-up", "role_why": "made-up",
        "role_source": "https://made.up/role", "integration_source": "https://made.up/int",
        "sites": [{"plant": "P1", "town": "T", "country": "C", "region": "EU",
                   "scope": "product", "area": "A", "area_basis": "stated",
                   "country_basis": "stated", "site": "a label", "quote": "made-up quote",
                   "source": "https://made.up/site", "status": "planned"},
                  {"town": "U", "country": "C", "area": "derived area", "area_basis": "derived",
                   "scope": "range"}],
    }
    fields = content._link_fields(row, 3, None)
    assert set(fields) == LINK_COLUMNS
    assert fields["share_pct"] is None and fields["share_disclosed"] is False
    assert fields["integration_basis"] == "made-up why"
    assert fields["origin_restriction"] == "sanctioned_origin"
    assert fields["floor_eligible_eu"] is False and fields["counts_toward_floor"] is False
    assert fields["corp_group"] == "Maker Z Group" and fields["row_order"] == 3
    assert fields["sites"] == [
        {"plant": "P1", "town": "T", "country": "C", "region": "EU", "scope": "product",
         "status": "planned", "area": "A"},
        {"town": "U", "country": "C", "scope": "range"},
    ]
    stored = json.dumps(fields, default=str)
    for value in ("made-up quote", "https://", "made-up audit", "made-up ruling", "someone"):
        assert value not in stored, value


# ── The load ─────────────────────────────────────────────────────────────────

def test_load_reaches_the_expected_counts(loaded):
    db, first, _ = loaded
    for block_type, n in expect.expected_block_counts().items():
        assert _total(first, f"{content.BLOCK_ROW_PREFIX}{block_type}") == n, block_type
        stored = db.query(EditorialBlock).filter(
            EditorialBlock.team_id.is_(None), EditorialBlock.block_type == block_type,
            EditorialBlock.source_note.isnot(None)).count()
        assert stored == n, block_type
    rows = list(expect.all_supplier_rows())
    assert first.table(content.CHECK_SUPPLIER_ROWS).unchanged == \
        sum(1 for r in rows if r.raw_name.strip())
    assert first.table(content.CHECK_COUNTING).unchanged == \
        sum(1 for r in rows if r.counts_toward_floor)
    assert [k for k, _ in first.table(content.CHECK_SHELLS).skipped] == expect.shells()
    links = _expected_links()
    assert _total(first, "producer_formulas") == len(links)
    assert _total(first, "producers") == len({name for _pid, name in links})
    assert first.table(content.CHECK_CANONICAL).unchanged == len({name for _p, name in links})
    buckets = {normalize_value(b) for b in expect.raw("SUPPLIER_BUCKETS")}
    assert first.table(content.CHECK_BUCKETS).unchanged == \
        len(buckets & {name for _p, name in links})


def test_a_second_load_changes_nothing(loaded):
    """The design's "content --dry-run = 0 changes after the build" check,
    pulled forward: the test database is an untouched copy of the build, so
    the fixture's first load must already find everything current. If another
    module runs first and leaves platform rows changed, this is where it shows."""
    _, first, second = loaded
    assert first.changed == 0, first.render()     # the build's own load is current
    assert second.changed == 0, second.render()
    for diff in second.tables:
        assert diff.created == diff.updated == diff.deleted == 0, diff.table


# ── Editorial blocks ─────────────────────────────────────────────────────────

def test_no_retired_block_type_is_stored(loaded):
    db, _, _ = loaded
    assert db.query(EditorialBlock).filter(
        EditorialBlock.team_id.is_(None),
        EditorialBlock.block_type.in_(content.RETIRED_BLOCK_TYPES)).count() == 0


def test_no_stored_body_holds_a_private_key(loaded):
    """A walk over every version of every platform block."""
    db, _, _ = loaded
    bad = [str(block_id) for block_id, fmt, body_json in (
        db.query(EditorialBlockVersion.block_id, EditorialBlockVersion.body_format,
                 EditorialBlockVersion.body_json)
        .join(EditorialBlock, EditorialBlock.id == EditorialBlockVersion.block_id)
        .filter(EditorialBlock.team_id.is_(None)))
        if fmt == "json" and _private_key(body_json)]
    assert bad == []


def test_every_block_is_a_version_one_import_linked_to_its_template(loaded):
    db, _, _ = loaded
    pid = expect.leak_probe_card()
    template = _template(db, pid)
    card = read_card(db, "formula", pid, uuid.uuid4())
    assert {b.block_type for b in card.blocks} <= PUBLIC_TYPES
    for block in card.blocks:
        assert block.provenance == "imported" and block.template_id == template.id
        version = db.get(EditorialBlockVersion, block.current_version_id)
        assert version.version_no == 1 and version.provenance == "imported"
    # No content without a template: every formula block has one, the
    # card-only keys and the groups included.
    orphans = db.query(EditorialBlock.subject_code).filter(
        EditorialBlock.team_id.is_(None), EditorialBlock.subject_type == "formula",
        EditorialBlock.template_id.is_(None)).all()
    assert orphans == []
    group = next(p for p in expect.template_codes() if p in expect.raw("AUTO_GROUPS"))
    assert _block(db, "formula", group, "functionalities").template_id == \
        _template(db, group).id
    for code in expect.card_only_codes():
        assert all(b.template_id == _template(db, code).id for b in db.query(EditorialBlock)
                   .filter(EditorialBlock.team_id.is_(None),
                           EditorialBlock.subject_code == code))


def test_bodies_are_stored_in_served_form(loaded):
    db, _, _ = loaded
    cards = _cards()
    # Applications: the entries with no receiver are gone, the rest stripped.
    pid = next(p for p in expect.content_codes()
               if any(isinstance(a, dict) and a.get("_no_receiver")
                      for a in cards[p].get("applications") or [])
               and sanitize.served_body("applications", cards[p]["applications"]))
    apps = _body(db, _block(db, "formula", pid, "applications"))
    assert apps == sanitize.served_applications(cards[pid]["applications"])
    assert not _private_key(apps)
    # The supplier note: cleaned, text format.
    pid = next(p for p in expect.content_codes()
               if re.search(r"share\s*:\s*0", cards[p].get("supplierNote") or "", re.I))
    note = _block(db, "formula", pid, "supplier_note")
    assert note.body_format == "text"
    assert _body(db, note) == sanitize.clean_supplier_note(cards[pid]["supplierNote"])
    # A bare compliance string: the entry shape, no flag.
    pid = next(p for p in expect.content_codes()
               if any(isinstance(c, str) for c in cards[p].get("compliance") or []))
    body = _body(db, _block(db, "formula", pid, "compliance"))
    bare = [c for c in body if c.get("bare")]
    assert bare and all(c["flag"] is None and c["name"] for c in bare)
    assert body == sanitize.served_compliance(cards[pid]["compliance"])


def test_macro_drivers_come_from_future_outlook(loaded):
    db, first, _ = loaded
    outlook = expect.raw("FUTURE_OUTLOOK")
    older = content.curated_macro_driver_pids()
    assert older, "the drop no longer carries older macro drivers; drop this check"
    pid = older[0]
    block = _block(db, "formula", pid, "macro_drivers")
    assert block.source_note == "FUTURE_OUTLOOK.macroDrivers"
    assert _body(db, block) == sanitize.strip_private(outlook[pid]["macroDrivers"])
    skipped = dict(first.table(f"{content.BLOCK_ROW_PREFIX}macro_drivers").skipped)
    assert skipped["CURATED_CONTENT.macroDrivers"].startswith(f"{len(older)} cards")


def test_outlooks_load_on_the_wildcard_and_withdrawn_ones_do_not(loaded):
    db, first, _ = loaded
    events = expect.raw("CURRENT_EVENTS_OUTLOOK")
    live = next(k for k, v in events.items() if isinstance(v.get("*"), str) and v["*"].strip()
                and k in expect.content_codes())
    block = _block(db, "formula", live, "current_events")
    assert block.region is None and block.body_format == "text"
    assert _body(db, block) == sanitize.clean_current_events(events[live]["*"])
    withdrawn = sorted(k for k, v in events.items() if v and all(r.startswith("_") for r in v))
    assert withdrawn
    for pid in withdrawn:
        assert db.query(EditorialBlock).filter(
            EditorialBlock.team_id.is_(None), EditorialBlock.subject_code == pid,
            EditorialBlock.block_type == "current_events").count() == 0
    skipped = first.table(f"{content.BLOCK_ROW_PREFIX}current_events").skipped
    assert {k for k, _ in skipped} == {f"{pid} current_events" for pid in withdrawn}


def test_index_blocks_sit_on_their_series(loaded):
    db, first, _ = loaded
    series = dict(db.query(CommodityIndex.commodity_key, CommodityIndex.id).filter(
        CommodityIndex.commodity_key.isnot(None)))
    blocks = db.query(EditorialBlock).filter(EditorialBlock.team_id.is_(None),
                                             EditorialBlock.subject_type == "index").all()
    assert {(b.subject_code, b.block_type) for b in blocks} == \
        {(c, t) for s, c, t, _r in expect.expected_block_keys() if s == "index"}
    assert any(b.commodity_id is not None for b in blocks)
    for b in blocks:
        assert b.commodity_id == series.get(b.subject_code), b.subject_code
    assert [k for k, _ in first.table(content.CHECK_INDEX_SUBJECTS).skipped] == \
        sorted({b.subject_code for b in blocks if b.subject_code not in series})
    narrative = next(b for b in blocks if b.block_type == "index_narrative")
    assert _body(db, narrative) == \
        sanitize.strip_private(expect.raw("INDEX_NARRATIVES")[narrative.subject_code])


# ── Dimensions ───────────────────────────────────────────────────────────────

def test_the_facets_are_the_tree_industries_and_functions(loaded):
    db, _, _ = loaded
    terms = db.query(DimensionTerm).filter(DimensionTerm.team_id.is_(None),
                                           DimensionTerm.is_active.is_(True))
    industry = {t.code: t.label for t in terms.filter(DimensionTerm.kind == KIND_INDUSTRY)}
    assert industry == {i.slug: i.name for i in db.query(Industry)}
    assert set(industry.values()) == set(expect.industries())
    functions = {t.label for t in terms.filter(DimensionTerm.kind == KIND_FUNCTIONALITY)}
    assert functions == set(content.tree_functions())
    assert functions == {c["fn"] for c in expect.categories() if c.get("fn")}
    # Every label is its own alias.
    for kind, label in ((KIND_INDUSTRY, sorted(industry.values())[0]),
                        (KIND_FUNCTIONALITY, sorted(functions)[0])):
        alias = db.query(DimensionAlias).filter(
            DimensionAlias.team_id.is_(None), DimensionAlias.kind == kind,
            DimensionAlias.normalized == normalize_value(label)).one()
        assert alias.term.label == label


def test_assertions_are_the_placement_projection(loaded):
    db, first, _ = loaded
    rows = (db.query(CategoryPlacement.pid, Industry.slug, CategoryPlacement.fn, Category.code)
            .join(Industry, Industry.id == CategoryPlacement.industry_id)
            .join(Category, Category.id == CategoryPlacement.category_id).all())
    assert rows, "no placements: the taxonomy loader has not run"
    by_industry, by_fn = defaultdict(set), defaultdict(set)
    for pid, slug, fn, code in rows:
        by_industry[(pid, slug)].add(code)
        if fn:
            by_fn[(pid, fn)].add(code)
    assert _total(first, "dimension_assertions · industry") == len(by_industry)
    assert _total(first, "dimension_assertions · functionality") == len(by_fn)

    stored = (db.query(DimensionAssertion, DimensionTerm)
              .join(DimensionTerm, DimensionTerm.id == DimensionAssertion.term_id)
              .filter(DimensionAssertion.team_id.is_(None),
                      DimensionTerm.team_id.is_(None),
                      DimensionTerm.kind.in_((KIND_INDUSTRY, KIND_FUNCTIONALITY)),
                      DimensionAssertion.source == "loader").all())
    got_industry = {(a.subject_code, t.code): set(a.detail["categories"])
                    for a, t in stored if t.kind == KIND_INDUSTRY}
    got_fn = {(a.subject_code, t.label): set(a.detail["categories"])
              for a, t in stored if t.kind == KIND_FUNCTIONALITY}
    assert got_industry == dict(by_industry)
    assert got_fn == dict(by_fn)
    assert all(a.region is None and a.subject_type == "formula" and a.matched_alias_id
               for a, _ in stored)
    templates = dict(db.query(FormulaTemplate.code, FormulaTemplate.id)
                     .filter(FormulaTemplate.team_id.is_(None)))
    assert all(a.template_id == templates.get(a.subject_code) for a, _ in stored)


# ── Producers ────────────────────────────────────────────────────────────────

def test_every_link_carries_the_evidence_of_its_first_row(loaded):
    """The predicate, the label and row_order match the expectation on every
    row: each link is compared with the supplier row at its row_order."""
    db, _, _ = loaded
    want = _expected_links()
    templates = dict(db.query(FormulaTemplate.code, FormulaTemplate.id)
                     .filter(FormulaTemplate.team_id.is_(None)))
    got = {}
    rows_by_pid = {}
    for pf, name in _loader_links(db):
        got[(pf.subject_code, name)] = pf
        if pf.subject_code not in rows_by_pid:
            rows_by_pid[pf.subject_code] = expect.supplier_rows(pf.subject_code)
        row = rows_by_pid[pf.subject_code][pf.row_order]
        assert (pf.counts_toward_floor, pf.evidence_label, pf.origin_restriction,
                pf.floor_eligible_eu, pf.counted, pf.maker_evidence, pf.weak_reading,
                pf.region_uncertain, pf.raw_name) == \
            (row.counts_toward_floor, row.evidence_label, row.origin_restriction,
             row.floor_eligible_eu, row.counted, row.maker_evidence, row.weak_reading,
             row.region_uncertain, row.raw_name), (pf.subject_code, pf.row_order)
        assert pf.template_id is not None and pf.template_id == templates[pf.subject_code]
    assert {k: pf.row_order for k, pf in got.items()} == want
    # Every authored row is on a link unless all its producers were named earlier.
    merged = _merged_rows()
    for pid in expect.content_codes():
        orders = {pf.row_order for (p, _n), pf in got.items() if p == pid}
        for row in expect.supplier_rows(pid):
            if row.raw_name.strip() and f"{pid} / {row.raw_name}" not in merged:
                assert row.row_order in orders, (pid, row.row_order)


def test_producer_formulas_hold_no_quote_source_share_or_audit_text(loaded):
    """A scan of every loader row. `tags`, `integration_basis` and the sites'
    plant and town are authored text that may repeat words of a quote (a plant
    name is taken from the quote that proves it); they are exempt from the
    substring check only, never from the whole-value one."""
    db, _, _ = loaded
    rows = _loader_links(db)
    assert rows
    assert all(pf.share_pct is None and pf.share_disclosed is False for pf, _ in rows)
    by_pid: dict[str, list[ProducerFormula]] = defaultdict(list)
    for pf, _name in rows:
        by_pid[pf.subject_code].append(pf)
    for pid, links in by_pid.items():
        authored = _authored_values(pid)
        secrets = (_quotes_and_sources(pid) | _audit_values(pid)) - authored
        quotes = {q for q in _quotes_and_sources(pid) - authored if len(q) >= MIN_QUOTE_LEN}
        for pf in links:
            for column in LINK_COLUMNS:
                leaves = _string_leaves(getattr(pf, column))
                assert not [s for s in leaves if s.strip() in secrets], (pid, column)
                if column in AUTHORED_TEXT:
                    continue
                assert not [s for s in leaves if URL.search(s)], (pid, column)
                assert not [s for s in leaves for q in quotes if q in s], (pid, column)
            for site in pf.sites or []:
                assert set(site) <= SITE_KEYS, (pid, sorted(site))


def test_the_leak_probe_card_stores_none_of_its_quotes(loaded):
    db, _, _ = loaded
    pid = expect.leak_probe_card()
    quotes = [q for q in expect.card_quotes(pid) if len(q) >= MIN_QUOTE_LEN]
    assert quotes
    blob = json.dumps([{c: getattr(pf, c) for c in LINK_COLUMNS}
                       for pf in _links(db, pid).values()], default=str)
    assert not [q for q in quotes if q in blob]


def test_one_raw_string_links_every_company_it_names(loaded):
    db, _, _ = loaded
    pid, row = next((p, r) for p in expect.content_codes() for r in _rows(p)
                    if len({normalize_value(n) for n in _canon(r["n"])}) > 1
                    and f"{p} / {r['n']}" not in _merged_rows())
    links = _links(db, pid)
    for name in _canon(row["n"]):
        assert links[normalize_value(name)].raw_name == row["n"]


def test_two_rows_naming_one_producer_merge_into_the_first(loaded):
    db, first, _ = loaded
    merged = {k for k, _ in first.table("producer_formulas").skipped}
    assert merged == _merged_rows()
    # The first row keeps its raw name and position; the regions are united.
    pid = sorted(merged)[0].split(" / ", 1)[0]
    rows = [(r, [normalize_value(n) for n in _canon(r["n"])]) for r in _rows(pid)]
    dup = next(n for i, (_r, names) in enumerate(rows) for n in names
               if any(n in earlier for _e, earlier in rows[:i]))
    naming = [r for r, names in rows if dup in names]
    regions: list[str] = []
    for r in naming:
        regions += [g for g in (r.get("regs") or []) if g not in regions]
    link = _links(db, pid)[dup]
    assert link.raw_name == naming[0]["n"]
    assert link.regions_raw == (regions or None)


def test_buckets_are_marked_and_aliases_resolve_to_the_canonical_company(loaded):
    db, _, _ = loaded
    names = {n for pid in expect.content_codes() for r in _rows(pid) for n in _canon(r["n"])}
    buckets = [b for b in expect.raw("SUPPLIER_BUCKETS") if b in names]
    assert _producer(db, buckets[0]).is_bucket is True
    real = next(n for n in sorted(names) if n not in set(expect.raw("SUPPLIER_BUCKETS")))
    assert _producer(db, real).is_bucket is False
    aliases = expect.raw("SUPPLIER_ALIASES")
    all_rows = [r for pid in expect.content_codes() for r in _rows(pid)]
    # A raw name with a qualifying parenthetical resolves to the bare company.
    raw = next(r["n"] for r in all_rows
               if r["n"].endswith(")") and " / " not in r["n"] and ", " not in r["n"]
               and r["n"] not in aliases and _canon(r["n"]) == [r["n"].split(" (")[0]])
    company = _producer(db, _canon(raw)[0])
    qualified = db.query(ProducerAlias).filter(ProducerAlias.normalized == normalize_value(raw),
                                               ProducerAlias.producer_id == company.id).one()
    assert qualified.match_key == normalize_value(_canon(raw)[0])
    # A split string is recorded against every company it names.
    raw = next(r["n"] for r in all_rows if " / " in r["n"] and len(_canon(r["n"])) > 1)
    split = {a.producer.normalized_name for a in db.query(ProducerAlias).filter(
        ProducerAlias.normalized == normalize_value(raw))}
    assert {normalize_value(n) for n in _canon(raw)} <= split


# ── Reload ───────────────────────────────────────────────────────────────────

def test_a_reload_deletes_retired_blocks_with_their_versions(loaded):
    """A database loaded the older way holds `suppliers`, `supply` and `demand`
    blocks; the next load deletes them and every version they had."""
    db, _, _ = loaded
    pid = expect.leak_probe_card()
    made = []
    # Written with the models, not the editorial service: the service may
    # already refuse these types.
    for block_type in content.RETIRED_BLOCK_TYPES:
        block = EditorialBlock(team_id=None, subject_type="formula", subject_code=pid,
                               block_type=block_type, region=None, body_format="json",
                               provenance="imported", source_note="made-up older load")
        db.add(block)
        db.flush()
        for no, body in ((1, [{"n": "Made-up maker", "share": 12,
                               "maker_quote": "made-up quote"}]),
                         (2, [{"n": "Made-up maker 2"}])):
            version = EditorialBlockVersion(block_id=block.id, version_no=no, body_json=body,
                                            body_format="json", provenance="imported")
            db.add(version)
            db.flush()
            block.current_version_id = version.id
        made.append(block.id)
    db.flush()
    assert db.query(EditorialBlockVersion).filter(
        EditorialBlockVersion.block_id.in_(made)).count() == 2 * len(made)

    report = content.load(db, LoadReport(title="retire"))
    db.flush()
    assert db.query(EditorialBlock).filter(EditorialBlock.id.in_(made)).count() == 0
    assert db.query(EditorialBlockVersion).filter(
        EditorialBlockVersion.block_id.in_(made)).count() == 0
    for block_type in content.RETIRED_BLOCK_TYPES:
        assert report.table(f"{content.BLOCK_ROW_PREFIX}{block_type}").deleted == 1
    assert report.table(content.VERSION_ROW).deleted == 2 * len(made)
    assert report.changed == len(made) + 2 * len(made), report.render()


def test_a_reload_repairs_drift_and_respects_platform_edits(loaded):
    """Runs last: it changes stored rows inside the module transaction."""
    db, _, _ = loaded
    pid = expect.leak_probe_card()
    cards = _cards()
    note = _block(db, "formula", pid, "supplier_note")
    add_version(db, note, body_text="drifted", body_json=None, body_format="text",
                provenance="imported", change_note="test drift", author=None)
    edited_type = next(t for t in ("synthesis_route", "functionalities", "substitution")
                       if (pid, t) in {(c, bt) for _s, c, bt, _r in expect.expected_block_keys()})
    edited = _block(db, "formula", pid, edited_type)
    add_version(db, edited, body_text=None, body_json={"made_up": "edited by hand"},
                body_format="json", provenance="human_edited", change_note="test edit",
                author=None)

    makers = list(dict.fromkeys(normalize_value(n) for r in _rows(pid) for n in _canon(r["n"])))
    maker_a, maker_b = makers[:2]
    links = _links(db, pid)
    links[maker_a].share_disclosed = True
    links[maker_a].share_pct = 12
    links[maker_a].evidence_label = "verified" if links[maker_a].evidence_label != "verified" \
        else "not_audited"
    db.delete(links[maker_b])
    outsider = (db.query(Producer).filter(~Producer.normalized_name.in_(makers),
                                          Producer.is_bucket.is_(False))
                .order_by(Producer.normalized_name).first())
    db.add(ProducerFormula(producer_id=outsider.id, subject_code=pid, region=None,
                           source="loader", share_disclosed=False))
    shell = expect.shells()[0]
    db.add(ProducerFormula(producer_id=outsider.id, subject_code=shell, region=None,
                           source="loader", share_disclosed=False))

    functions = sorted(content.tree_functions())
    renamed = db.query(DimensionTerm).filter(DimensionTerm.team_id.is_(None),
                                             DimensionTerm.kind == KIND_FUNCTIONALITY,
                                             DimensionTerm.label == functions[0]).one()
    renamed.label = functions[0] + " (renamed)"
    industry = db.query(DimensionTerm).filter(DimensionTerm.team_id.is_(None),
                                              DimensionTerm.kind == KIND_INDUSTRY).first()
    industry.is_active = False
    db.add(DimensionAssertion(team_id=None, term_id=industry.id, subject_type="formula",
                              subject_code="NOT-A-PLACEMENT", region=None, source="loader"))
    db.add(DimensionAssertion(team_id=None, term_id=industry.id, subject_type="formula",
                              subject_code="ANALYST-CALL", region=None,
                              source="decision_file"))
    db.query(ProducerAlias).filter(ProducerAlias.normalized == maker_a).delete()
    # The company held under a qualified name (as the dossier loader mints them),
    # its alias rows still keyed on the bare name.
    second = _producer(db, maker_b)
    canonical_b = second.name
    second.name = f"{canonical_b} (qualified)"
    second.normalized_name = normalize_value(second.name)
    db.flush()

    repair = content.load(db, LoadReport(title="repair"))
    db.flush()
    assert repair.table(f"{content.BLOCK_ROW_PREFIX}supplier_note").updated == 1
    assert _body(db, note) == sanitize.clean_supplier_note(cards[pid]["supplierNote"])
    assert note.provenance == "imported"
    # Edited on the platform: kept, and named in the report.
    assert _body(db, edited) == {"made_up": "edited by hand"}
    assert any(k == f"{pid} {edited_type}"
               for k, _ in repair.table(f"{content.BLOCK_ROW_PREFIX}{edited_type}").skipped)

    links = _links(db, pid)
    assert links[maker_a].share_disclosed is False and links[maker_a].share_pct is None
    row = expect.supplier_rows(pid)[links[maker_a].row_order]
    assert links[maker_a].evidence_label == row.evidence_label
    assert maker_b in links and outsider.normalized_name not in links
    assert db.query(ProducerFormula).filter(ProducerFormula.subject_code == shell).count() == 0
    assert renamed.label == functions[0] and industry.is_active is True
    assert repair.table("dimension_assertions · industry").deleted == 1
    kept = db.query(DimensionAssertion).filter(
        DimensionAssertion.subject_code == "ANALYST-CALL").one()
    assert kept.source == "decision_file"
    assert db.query(ProducerAlias).filter(ProducerAlias.normalized == maker_a).count() == 1
    # Given its canonical name back, never minted as a twin.
    assert second.normalized_name == maker_b
    assert db.query(Producer).filter(Producer.normalized_name.like(maker_b + "%")).count() == 1
    assert [k for k, _ in repair.table(content.CHECK_ADOPTED).skipped] == \
        [f"{canonical_b} (qualified)"]

    again = content.load(db, LoadReport(title="after repair"))
    db.flush()
    assert again.changed == 0, again.render()
