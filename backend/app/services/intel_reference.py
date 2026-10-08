"""Intelligence reference reads: product lines, market reports, the demand axis
(industries › categories) and the supplier directory (design §4.2).

Backs `routers/intel_reference.py`, and `services/taxonomy.py` builds the
`/api/taxonomy` tree from the same snapshot. Read-only platform reference:
nothing here writes, and nothing here filters by team.

**One snapshot, several views.** Every list and rollup on these pages is a
view over a handful of platform tables. `get_snapshot()` reads them in one
pass into plain Python structures and keeps the result in process, keyed by a
load-version tuple (the latest `content_loads` id plus row counts and max
timestamps, one round trip), with a time cap on top for the tables that carry
no timestamp. Endpoints then filter and slice dicts; report HTML and dossier
roles are the only things read per request.

**The definitions every view shares** (so the pages agree with each other):

* *Line* — a `product_lines` row. A line is *hidden* when it is retired
  (`retired_at`) or carries the `do_not_publish` flag: not listed, 404 on
  detail, left out of report line lists, industry members and supplier
  rollups.
* *Card* — a platform template that is **listed** (`catalog_visibility`:
  kind product or group, with a coverage row that is not withdrawn) and whose
  line is not hidden. "Product count" is always a count of cards. A tree pid
  that is not a card reaches the card that stands for it: an absorbed
  template its group, a pointer or duplicate its target (one hop, and only
  when that target is a card).
* *Counting link* — a `producer_formulas` row with `counts_toward_floor`
  (Laurent's predicate, stored by the loader) on a card. The supplier
  directory lists a producer only when it has one; rollups, top suppliers and
  competitor ranking use counting links only. Bucket producers ("Chinese
  producers", …) are never listed or ranked; they appear in the generic note.
* *Unpublished key* — a line key that is neither a current line key nor a
  former key exactly one current line claims (`content_drop.taxonomy
  .line_index`). It is never shown, nor its tail: the row says
  `UNPUBLISHED_LINE` (design §2.2, frozen instruction). The family half may
  show when it names a real family.

Never served (design §4.1): shares, supplier counts as headlines, the line's
`confidence` and `axis_meta`, authored flag texts, the sub-family `why` (most
cite rulings and authoring passes), category notes, member and shared-object
`why`, out-row `why`, producer notes.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.chemical_family import ChemicalFamily
from app.models.content_load import ContentLoad
from app.models.formula_template import (
    SUPPLY_STATUSES, FormulaRegionCoverage, FormulaTemplate,
)
from app.models.index_data import CommodityIndex
from app.models.index_dossier import IndexDossier, IndexProducerRole
from app.models.market_report import (
    MarketReport, MarketReportLine, MarketReportPanel, MarketReportSection,
)
from app.models.producer import Producer, ProducerAlias, ProducerFormula
from app.models.product_line import LINE_KEY_SEP, ProductLine
from app.models.strategy import Playbook
from app.models.subfamily import Subfamily
from app.models.taxonomy_v2 import (
    Category, CategoryBuildItem, CategoryMember, CategoryPlacement, CategoryRef,
    CategoryShared, CategorySharedMember, Industry, IndustryOut,
)
from app.services.catalog_visibility import (
    EVIDENCE_LABEL_TEXT, ORIGIN_RESTRICTION_TEXT, STATUS_RANK, in_default_view, is_listed,
    status_badge,
)
from app.services.content_drop.reader import DROP_REGION_BY_APP, REGION_MAP

# What a row says where its line key is not a current line (design §2.2).
UNPUBLISHED_LINE = "Product line not yet published"

# A line is hidden when its `flags` carry one of these keys (or it is retired).
HIDDEN_LINE_FLAGS = ("do_not_publish",)
# The one line flag served, with fixed wording. The authored flag texts are
# working notes (they name rulings, work packages and products) and are never
# served; `route_flag`, `validation_question` and `retracted` are not shown.
PENDING_FLAG = {"code": "validation_pending", "label": "Supplier validation pending"}

# Out-row reason codes (`industry_out.code`) and their buyer-facing labels.
OUT_REASONS: dict[str, str] = {
    "OUT_WRONG_BUYER": "Wrong buyer",
    "OUT_UPSTREAM": "Upstream input",
    "OUT_WRONG_GRADE": "Wrong grade",
    "OUT_SCOPE": "Out of scope",
    "OUT_BUYER_OUTPUT": "Buyer's own output",
    "PENDING_EVIDENCE": "Pending evidence",
    "PENDING_BOUNDARY": "Boundary under review",
    "BUILD": "To be built",
}
OUT_REASON_FALLBACK = "Not bought here"

# Display order for drop region codes (the drop's own REGS order).
REGION_ORDER = tuple(REGION_MAP.keys())  # EU, NA, CN, IN, APAC, MEA, LA, GL
_REGION_RANK = {code: i for i, code in enumerate(REGION_ORDER)}

TOP_LINE_SUPPLIERS = 4
TOP_PRODUCT_SUPPLIERS = 3
TOP_COMPETITORS = 15
LIST_TOP_INDUSTRIES = 8
SNAPSHOT_MAX_AGE_S = 600

REPORT_SOURCE_RANK = {"report_map": 0, "key_map": 1}
CATEGORY_STATUSES = ("servable", "partial", "build")


class BadStatusFilter(ValueError):
    """A `?status=` value that names no supply status."""


def parse_status(value: str | None) -> frozenset[str] | None:
    """`?status=` → the statuses to keep, or None for every listed card.

    A comma list of `live`, `supply_exception`, `supply_pending`,
    `not_audited`, or `all`. Absent or empty means all listed cards (the
    Products API's rule; the page sends its default explicitly)."""
    if value is None or not value.strip():
        return None
    parts = {p.strip().lower() for p in value.split(",") if p.strip()}
    if "all" in parts:
        if len(parts) > 1:
            raise BadStatusFilter("'all' cannot be combined with other statuses")
        return None
    unknown = parts - set(SUPPLY_STATUSES)
    if unknown:
        raise BadStatusFilter(
            f"unknown status {sorted(unknown)}; allowed: {list(SUPPLY_STATUSES)} or 'all'")
    return frozenset(parts)


# ── Snapshot ─────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class LineRow:
    id: int
    line_key: str
    name: str
    family_id: int
    family: str
    subfamily_id: int | None
    subfamily: str | None
    platform: str | None
    in_v1_scope: bool
    report_slug: str | None
    former_keys: list[str]
    hidden: bool
    pending: bool
    sort_order: int


@dataclass(slots=True)
class SubfamilyRow:
    id: int
    family_id: int
    name: str | None
    sort_order: int


@dataclass(slots=True)
class TemplateRow:
    id: uuid.UUID
    code: str
    name: str
    full_name: str | None
    form: str | None
    family_id: int | None
    product_line_id: int | None
    card_kind: str
    supply_status: str | None
    redirect_to: str | None
    absorbed_into: str | None
    is_group: bool
    group_members: list | None
    listed: bool = False
    default_view: bool = False
    # A card: listed, and its line is not hidden.
    visible: bool = False
    regions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LinkRow:
    producer_id: uuid.UUID
    code: str
    row_order: int
    role: str | None
    counts: bool
    evidence_label: str
    weak_reading: bool | None
    integration_status: str | None
    integrated: bool | None
    integration_basis: str | None
    origin_restriction: str | None
    region_uncertain: bool | None
    corp_group: str | None
    regions: list[str]
    tags: list
    sites: list
    hq_country: str | None
    raw_name: str | None


@dataclass
class Snapshot:
    version: tuple
    built_at: float
    families: dict[int, dict]
    family_by_name: dict[str, int]
    subfamilies: dict[int, SubfamilyRow]
    lines: dict[int, LineRow]
    # current key, and each former key one current line claims → line id
    line_index: dict[str, int]
    templates: dict[str, TemplateRow]
    # code → the card that stands for it (None when there is none)
    card_of: dict[str, str | None]
    # card → every code that reaches it (itself first)
    behind_card: dict[str, list[str]]
    # line id → every template code on it, and its cards
    line_codes: dict[int, list[str]]
    line_cards: dict[int, list[str]]
    placements_by_pid: dict[str, list[tuple[int, int, str | None]]]
    # (category id, authored line key) → placed pids, for whole-line rows on
    # keys that are not a current line
    placements_by_cat_key: dict[tuple[int, str], list[str]]
    industries: dict[int, dict]
    industry_by_slug: dict[str, int]
    industry_by_name_lower: dict[str, int]
    categories: dict[int, dict]
    categories_by_industry: dict[int, list[int]]
    members_by_category: dict[int, list[dict]]
    refs_by_category: dict[int, list[dict]]
    shared: dict[int, dict]
    shared_members: dict[int, list[dict]]
    build_by_category: dict[int, list[str]]
    out_by_industry: dict[int, list[dict]]
    producers: dict[uuid.UUID, dict]
    aliases: dict[uuid.UUID, list[str]]
    links_by_producer: dict[uuid.UUID, list[LinkRow]]
    links_by_code: dict[str, list[LinkRow]]
    reports: dict[str, dict]
    # slug → [{product_line_id, source}], sorted
    report_lines: dict[str, list[dict]]
    reports_by_line: dict[int, list[dict]]
    playbooks: dict[str, str]
    # Precomputed views
    card_industries: dict[str, Counter] = field(default_factory=dict)
    card_functions: dict[str, Counter] = field(default_factory=dict)
    line_items: list[dict] = field(default_factory=list)
    line_item_by_id: dict[int, dict] = field(default_factory=dict)
    supplier_rows: list[dict] = field(default_factory=list)
    supplier_row_by_id: dict[uuid.UUID, dict] = field(default_factory=dict)
    generic_suppliers: list[dict] = field(default_factory=list)
    industry_items: list[dict] = field(default_factory=list)
    # Views other modules build lazily on this snapshot (services/taxonomy.py),
    # so they share its version and its lifetime.
    derived: dict[str, Any] = field(default_factory=dict)
    derived_lock: Any = field(default_factory=threading.Lock)


_lock = threading.Lock()
_snapshot: Snapshot | None = None


def reset_cache() -> None:
    """Drop the in-process snapshot (tests, or after a reload)."""
    global _snapshot
    with _lock:
        _snapshot = None


def _version_key(db: Session) -> tuple:
    """One round trip: the latest content load, plus counts and max
    timestamps of every table the snapshot reads. A full load writes a new
    `content_loads` row; a partial reload changes at least one count or
    timestamp, or is picked up by the time cap."""
    def cnt(model, *where):
        q = select(func.count()).select_from(model)
        for w in where:
            q = q.where(w)
        return q.scalar_subquery()

    platform_ids = select(FormulaTemplate.id).where(FormulaTemplate.team_id.is_(None))
    row = db.execute(select(
        select(func.max(ContentLoad.id)).scalar_subquery(),
        cnt(ChemicalFamily),
        cnt(Subfamily),
        cnt(ProductLine),
        cnt(ProductLine, ProductLine.retired_at.isnot(None)),
        cnt(FormulaTemplate, FormulaTemplate.team_id.is_(None)),
        select(func.max(FormulaTemplate.updated_at))
        .where(FormulaTemplate.team_id.is_(None)).scalar_subquery(),
        cnt(ProducerFormula),
        select(func.max(ProducerFormula.created_at)).scalar_subquery(),
        cnt(Producer),
        cnt(CategoryPlacement),
        select(func.max(CategoryPlacement.id)).scalar_subquery(),
        cnt(Category),
        cnt(IndustryOut),
        cnt(MarketReportLine),
        select(func.max(MarketReport.updated_at)).scalar_subquery(),
        cnt(Playbook),
        # Scoped to platform templates: coverage is under RLS, and an
        # unscoped count would differ per user (their teams' rows) and make
        # the key flap between users.
        cnt(FormulaRegionCoverage, FormulaRegionCoverage.template_id.in_(platform_ids),
            FormulaRegionCoverage.withdrawn_at.is_(None)),
    )).one()
    return tuple(str(v) for v in row)


def get_snapshot(db: Session) -> Snapshot:
    global _snapshot
    version = _version_key(db)
    snap = _snapshot
    if snap and snap.version == version and time.monotonic() - snap.built_at < SNAPSHOT_MAX_AGE_S:
        return snap
    with _lock:
        snap = _snapshot
        if snap and snap.version == version and time.monotonic() - snap.built_at < SNAPSHOT_MAX_AGE_S:
            return snap
        snap = _build(db, version)
        _snapshot = snap
        return snap


def derived(snap: Snapshot, name: str, build) -> Any:
    """A view built once per snapshot (`build(snap)`), cached on it."""
    value = snap.derived.get(name)
    if value is None:
        with snap.derived_lock:
            value = snap.derived.get(name)
            if value is None:
                value = build(snap)
                snap.derived[name] = value
    return value


def _region_sorted(codes: Iterable[str]) -> list[str]:
    return sorted(set(c for c in codes if c), key=lambda c: (_REGION_RANK.get(c, 99), c))


def _line_hidden(flags: dict, retired_at) -> bool:
    return retired_at is not None or any(flags.get(k) not in (None, False) for k in HIDDEN_LINE_FLAGS)


def _line_pending(flags: dict) -> bool:
    status = flags.get("status")
    return isinstance(status, str) and "pending" in status.lower()


def _build(db: Session, version: tuple) -> Snapshot:
    families: dict[int, dict] = {}
    for r in db.execute(select(ChemicalFamily.id, ChemicalFamily.name, ChemicalFamily.sort_order)
                        .order_by(ChemicalFamily.sort_order, ChemicalFamily.name)).all():
        families[r.id] = {"id": r.id, "name": r.name, "sort_order": r.sort_order}
    family_by_name = {f["name"]: fid for fid, f in families.items()}

    subfamilies: dict[int, SubfamilyRow] = {}
    for r in db.execute(select(Subfamily.id, Subfamily.family_id, Subfamily.name,
                               Subfamily.sort_order)).all():
        subfamilies[r.id] = SubfamilyRow(id=r.id, family_id=r.family_id, name=r.name,
                                         sort_order=r.sort_order)

    lines: dict[int, LineRow] = {}
    current: dict[str, int] = {}
    claims: dict[str, set[int]] = defaultdict(set)
    for r in db.execute(select(
        ProductLine.id, ProductLine.line_key, ProductLine.name, ProductLine.family_id,
        ProductLine.subfamily_id, ProductLine.platform, ProductLine.in_v1_scope,
        ProductLine.report_slug, ProductLine.former_keys, ProductLine.flags,
        ProductLine.retired_at, ProductLine.sort_order,
    )).all():
        flags = r.flags or {}
        sub = subfamilies.get(r.subfamily_id) if r.subfamily_id else None
        lines[r.id] = LineRow(
            id=r.id, line_key=r.line_key, name=r.name, family_id=r.family_id,
            family=(families.get(r.family_id) or {}).get("name")
            or r.line_key.split(LINE_KEY_SEP, 1)[0],
            subfamily_id=r.subfamily_id, subfamily=sub.name if sub else None,
            platform=r.platform, in_v1_scope=bool(r.in_v1_scope), report_slug=r.report_slug,
            former_keys=[k for k in (r.former_keys or []) if isinstance(k, str)],
            hidden=_line_hidden(flags, r.retired_at), pending=_line_pending(flags),
            sort_order=r.sort_order,
        )
        # The loader's resolution rule (content_drop.taxonomy.line_index):
        # retired lines resolve nothing.
        if r.retired_at is None:
            current[r.line_key] = r.id
            for old in lines[r.id].former_keys:
                claims[old].add(r.id)
    line_index = dict(current)
    for old, ids in claims.items():
        if old not in current and len(ids) == 1:
            line_index[old] = next(iter(ids))

    templates: dict[str, TemplateRow] = {}
    by_id: dict[uuid.UUID, TemplateRow] = {}
    for r in db.execute(select(
        FormulaTemplate.id, FormulaTemplate.code, FormulaTemplate.name,
        FormulaTemplate.full_name, FormulaTemplate.form, FormulaTemplate.family_id,
        FormulaTemplate.product_line_id, FormulaTemplate.card_kind,
        FormulaTemplate.supply_status, FormulaTemplate.redirect_to,
        FormulaTemplate.absorbed_into, FormulaTemplate.is_group, FormulaTemplate.group_members,
    ).where(FormulaTemplate.team_id.is_(None), FormulaTemplate.code.isnot(None))).all():
        t = TemplateRow(
            id=r.id, code=r.code, name=r.name, full_name=r.full_name, form=r.form,
            family_id=r.family_id, product_line_id=r.product_line_id, card_kind=r.card_kind,
            supply_status=r.supply_status, redirect_to=r.redirect_to,
            absorbed_into=r.absorbed_into, is_group=bool(r.is_group),
            group_members=r.group_members,
        )
        templates[t.code] = t
        by_id[t.id] = t
    regions_by_tid: dict[uuid.UUID, set[str]] = defaultdict(set)
    for tid, region in db.execute(
        select(FormulaRegionCoverage.template_id, FormulaRegionCoverage.region)
        .where(FormulaRegionCoverage.withdrawn_at.is_(None)).distinct()
    ).all():
        if tid in by_id:
            regions_by_tid[tid].add(DROP_REGION_BY_APP.get(region, region))
    for t in templates.values():
        live = t.id in regions_by_tid
        t.regions = _region_sorted(regions_by_tid.get(t.id, ()))
        t.listed = is_listed(t.card_kind, live)
        t.default_view = in_default_view(t.card_kind, t.supply_status, live)
        ln = lines.get(t.product_line_id) if t.product_line_id else None
        t.visible = t.listed and not (ln and ln.hidden)

    card_of: dict[str, str | None] = {}
    behind_card: dict[str, list[str]] = defaultdict(list)
    for code, t in sorted(templates.items()):
        card_of[code] = _card_for(templates, t)
    for code in sorted(templates):
        card = card_of[code]
        if card:
            if code == card:
                behind_card[card].insert(0, code)
            else:
                behind_card[card].append(code)

    line_codes: dict[int, list[str]] = defaultdict(list)
    line_cards: dict[int, list[str]] = defaultdict(list)
    for code, t in sorted(templates.items()):
        if t.product_line_id in lines:
            line_codes[t.product_line_id].append(code)
            if t.visible:
                line_cards[t.product_line_id].append(code)

    industries: dict[int, dict] = {}
    for r in db.execute(select(Industry).order_by(Industry.sort_order, Industry.name)).scalars():
        industries[r.id] = {
            "id": r.id, "name": r.name, "slug": r.slug, "status": r.status,
            "ratified_on": r.ratified_on, "scope_status": r.scope_status,
            "buyer_one_line": r.buyer_one_line, "buyer": r.buyer,
            "in_scope": r.in_scope or [], "out_of_scope": r.out_of_scope or [],
            "boundaries": r.boundaries or [], "sort_order": r.sort_order,
        }
    industry_by_slug = {v["slug"]: k for k, v in industries.items()}
    industry_by_name_lower = {v["name"].lower(): k for k, v in industries.items()}

    categories: dict[int, dict] = {}
    categories_by_industry: dict[int, list[int]] = defaultdict(list)
    for r in db.execute(select(
        Category.id, Category.industry_id, Category.code, Category.name, Category.alias,
        Category.fn, Category.status, Category.sort_order,
    ).order_by(Category.industry_id, Category.sort_order, Category.code)).all():
        categories[r.id] = dict(r._mapping)
        categories_by_industry[r.industry_id].append(r.id)

    members_by_category: dict[int, list[dict]] = defaultdict(list)
    for r in db.execute(select(
        CategoryMember.category_id, CategoryMember.line_key, CategoryMember.pids,
        CategoryMember.is_whole_line, CategoryMember.sort_order,
    ).order_by(CategoryMember.category_id, CategoryMember.sort_order)).all():
        members_by_category[r.category_id].append(dict(r._mapping))

    refs_by_category: dict[int, list[dict]] = defaultdict(list)
    for r in db.execute(select(
        CategoryRef.category_id, CategoryRef.shared_id, CategoryRef.pids, CategoryRef.sort_order,
    ).order_by(CategoryRef.category_id, CategoryRef.sort_order)).all():
        refs_by_category[r.category_id].append(dict(r._mapping))

    shared = {
        r.id: dict(r._mapping) for r in db.execute(select(
            CategoryShared.id, CategoryShared.code, CategoryShared.name, CategoryShared.fn,
            CategoryShared.owner, CategoryShared.status,
        )).all()
    }
    shared_members: dict[int, list[dict]] = defaultdict(list)
    for r in db.execute(select(
        CategorySharedMember.shared_id, CategorySharedMember.line_key,
        CategorySharedMember.pids, CategorySharedMember.is_whole_line,
    ).order_by(CategorySharedMember.shared_id, CategorySharedMember.sort_order)).all():
        shared_members[r.shared_id].append(dict(r._mapping))

    build_by_category: dict[int, list[str]] = defaultdict(list)
    for cid, txt in db.execute(select(CategoryBuildItem.category_id, CategoryBuildItem.text)
                               .order_by(CategoryBuildItem.category_id, CategoryBuildItem.seq)).all():
        build_by_category[cid].append(txt)

    out_by_industry: dict[int, list[dict]] = defaultdict(list)
    for r in db.execute(select(
        IndustryOut.industry_id, IndustryOut.pid, IndustryOut.code, IndustryOut.to_industries,
    ).order_by(IndustryOut.industry_id, IndustryOut.id)).all():
        out_by_industry[r.industry_id].append(dict(r._mapping))

    placements_by_pid: dict[str, list[tuple[int, int, str | None]]] = defaultdict(list)
    placements_by_cat_key: dict[tuple[int, str], list[str]] = defaultdict(list)
    for ind_id, cat_id, line_key, pid, fn in db.execute(select(
        CategoryPlacement.industry_id, CategoryPlacement.category_id,
        CategoryPlacement.line_key, CategoryPlacement.pid, CategoryPlacement.fn,
    ).order_by(CategoryPlacement.category_id, CategoryPlacement.pid)).all():
        placements_by_pid[pid].append((ind_id, cat_id, fn))
        if line_key:
            placements_by_cat_key[(cat_id, line_key)].append(pid)

    producers: dict[uuid.UUID, dict] = {}
    for r in db.execute(select(
        Producer.id, Producer.name, Producer.hq_country, Producer.is_bucket,
    ).where(select(ProducerFormula.id).where(ProducerFormula.producer_id == Producer.id)
            .exists())).all():
        producers[r.id] = {"id": r.id, "name": r.name, "hq_country": r.hq_country,
                           "is_bucket": bool(r.is_bucket)}
    aliases: dict[uuid.UUID, list[str]] = defaultdict(list)
    for pid_, raw in db.execute(select(ProducerAlias.producer_id, ProducerAlias.raw_value)
                                .order_by(ProducerAlias.raw_value)).all():
        if pid_ in producers:
            aliases[pid_].append(raw)

    links_by_producer: dict[uuid.UUID, list[LinkRow]] = defaultdict(list)
    links_by_code: dict[str, list[LinkRow]] = defaultdict(list)
    for r in db.execute(select(
        ProducerFormula.producer_id, ProducerFormula.subject_code, ProducerFormula.row_order,
        ProducerFormula.role, ProducerFormula.counts_toward_floor,
        ProducerFormula.evidence_label, ProducerFormula.weak_reading,
        ProducerFormula.integration_status, ProducerFormula.integrated,
        ProducerFormula.integration_basis, ProducerFormula.origin_restriction,
        ProducerFormula.region_uncertain, ProducerFormula.corp_group,
        ProducerFormula.regions_raw, ProducerFormula.tags, ProducerFormula.sites,
        ProducerFormula.hq_country, ProducerFormula.raw_name,
    ).order_by(ProducerFormula.subject_code, ProducerFormula.row_order)).all():
        link = LinkRow(
            producer_id=r.producer_id, code=r.subject_code, row_order=r.row_order,
            role=r.role, counts=bool(r.counts_toward_floor),
            evidence_label=r.evidence_label or "not_audited", weak_reading=r.weak_reading,
            integration_status=r.integration_status, integrated=r.integrated,
            integration_basis=r.integration_basis, origin_restriction=r.origin_restriction,
            region_uncertain=r.region_uncertain, corp_group=r.corp_group,
            regions=_region_sorted(r.regions_raw or []), tags=list(r.tags or []),
            sites=list(r.sites or []), hq_country=r.hq_country, raw_name=r.raw_name,
        )
        links_by_producer[r.producer_id].append(link)
        links_by_code[r.subject_code].append(link)

    reports: dict[str, dict] = {}
    for r in db.execute(select(
        MarketReport.slug, MarketReport.name, MarketReport.family, MarketReport.old_line_name,
        MarketReport.as_of, MarketReport.in_v1_scope, MarketReport.word_count,
    )).all():
        reports[r.slug] = dict(r._mapping)
    report_lines: dict[str, list[dict]] = defaultdict(list)
    reports_by_line: dict[int, list[dict]] = defaultdict(list)
    for slug, line_id, source in db.execute(select(
        MarketReportLine.slug, MarketReportLine.product_line_id, MarketReportLine.source,
    )).all():
        report_lines[slug].append({"product_line_id": line_id, "source": source})
        if line_id is not None:
            reports_by_line[line_id].append({"slug": slug, "source": source})

    def _report_sort(entry: dict) -> tuple:
        name = (reports.get(entry["slug"]) or {}).get("name") or entry["slug"]
        return (REPORT_SOURCE_RANK.get(entry["source"], 9), name.lower(), entry["slug"])

    for entries in reports_by_line.values():
        entries.sort(key=_report_sort)
    for entries in report_lines.values():
        entries.sort(key=lambda e: (
            REPORT_SOURCE_RANK.get(e["source"], 9),
            e["product_line_id"] not in lines,
            (lines[e["product_line_id"]].name.lower() if e["product_line_id"] in lines else ""),
        ))

    playbooks = dict(db.execute(select(Playbook.slug, Playbook.name)).all())

    snap = Snapshot(
        version=version, built_at=time.monotonic(), families=families,
        family_by_name=family_by_name, subfamilies=subfamilies, lines=lines,
        line_index=line_index, templates=templates, card_of=card_of,
        behind_card=dict(behind_card), line_codes=dict(line_codes),
        line_cards=dict(line_cards), placements_by_pid=dict(placements_by_pid),
        placements_by_cat_key=dict(placements_by_cat_key),
        industries=industries, industry_by_slug=industry_by_slug,
        industry_by_name_lower=industry_by_name_lower, categories=categories,
        categories_by_industry=dict(categories_by_industry),
        members_by_category=dict(members_by_category), refs_by_category=dict(refs_by_category),
        shared=shared, shared_members=dict(shared_members),
        build_by_category=dict(build_by_category), out_by_industry=dict(out_by_industry),
        producers=producers, aliases=dict(aliases), links_by_producer=dict(links_by_producer),
        links_by_code=dict(links_by_code), reports=reports, report_lines=dict(report_lines),
        reports_by_line=dict(reports_by_line), playbooks=playbooks,
    )
    _precompute_cards(snap)
    _precompute_suppliers(snap)
    _precompute_lines(snap)
    _precompute_industries(snap)
    return snap


def _card_for(templates: dict[str, TemplateRow], t: TemplateRow) -> str | None:
    """The card that stands for template `t`: itself when it is a card, else
    its group (absorbed) or its target (pointer, duplicate), one hop, when
    that is a card."""
    if t.visible:
        return t.code
    target = None
    if t.card_kind == "absorbed":
        target = t.absorbed_into
    elif t.card_kind in ("pointer", "duplicate"):
        target = t.redirect_to
    tt = templates.get(target) if target else None
    return tt.code if tt is not None and tt.visible else None


# ── Shared helpers ───────────────────────────────────────────────────────────

def _month(d: date | None) -> str | None:
    return d.strftime("%Y-%m") if d else None


def _ranked_names(counter: Counter) -> list[str]:
    return [name for name, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def _ranked_counts(counter: Counter, key: str = "name") -> list[dict]:
    return [{key: name, "count": n}
            for name, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def _industry_name(snap: Snapshot, industry_id: int) -> str:
    return snap.industries[industry_id]["name"]


def _industry_counts(snap: Snapshot, counter: Counter) -> list[dict]:
    """[{name, slug, count}], most first."""
    out = []
    for name, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        iid = snap.industry_by_name_lower.get(name.lower())
        out.append({"name": name, "slug": snap.industries[iid]["slug"] if iid else None,
                    "count": n})
    return out


def _is_supplier(snap: Snapshot, producer_id: uuid.UUID) -> bool:
    p = snap.producers.get(producer_id)
    return bool(p) and not p["is_bucket"]


def _supplier_ref(snap: Snapshot, producer_id: uuid.UUID) -> dict:
    return {"id": str(producer_id), "name": snap.producers[producer_id]["name"]}


def _family_ref(snap: Snapshot, family_id: int | None) -> dict | None:
    f = snap.families.get(family_id) if family_id is not None else None
    return {"id": f["id"], "name": f["name"]} if f else None


def _subfamily_ref(ln: LineRow) -> dict | None:
    return {"id": ln.subfamily_id, "name": ln.subfamily} if ln.subfamily_id else None


def _line_ref(snap: Snapshot, line_id: int | None) -> dict | None:
    ln = snap.lines.get(line_id) if line_id is not None else None
    if ln is None or ln.hidden:
        return None
    return {"id": ln.id, "line_key": ln.line_key, "name": ln.name}


def _status_short(code: str | None) -> dict | None:
    b = status_badge(code)
    return {"code": b["code"], "label": b["label"], "tone": b["tone"]} if b else None


def _flags(ln: LineRow) -> list[dict]:
    return [dict(PENDING_FLAG)] if ln.pending else []


def resolve_line(snap: Snapshot, line_key: str | None) -> LineRow | None:
    """The current line a key names (its own key, or a former key exactly one
    current line claims), or None for an unpublished key."""
    if not line_key:
        return None
    lid = snap.line_index.get(line_key)
    return snap.lines.get(lid) if lid is not None else None


def _key_family(snap: Snapshot, line_key: str) -> str | None:
    """The family half of a key, when it names a real family."""
    head = line_key.split(LINE_KEY_SEP, 1)[0] if LINE_KEY_SEP in line_key else None
    return head if head in snap.family_by_name else None


def _in_status(t: TemplateRow, statuses: frozenset[str] | None) -> bool:
    return statuses is None or t.supply_status in statuses


def _status_sort(t: TemplateRow) -> tuple:
    return (STATUS_RANK.get(t.supply_status, 99), t.is_group, t.name.lower(), t.code)


def _counting_links(snap: Snapshot, code: str) -> list[LinkRow]:
    """Counting links on a card (empty when `code` is not a card)."""
    t = snap.templates.get(code)
    if t is None or not t.visible:
        return []
    return [lk for lk in snap.links_by_code.get(code, ()) if lk.counts]


def _suppliers_of_cards(snap: Snapshot, cards: Iterable[str]) -> tuple[Counter, set]:
    """Directory producers with a counting link on `cards`, counted by
    distinct card; plus the bucket producers linked to them."""
    per: dict[uuid.UUID, set[str]] = defaultdict(set)
    buckets: set[uuid.UUID] = set()
    for card in cards:
        t = snap.templates.get(card)
        if t is None or not t.visible:
            continue
        for lk in snap.links_by_code.get(card, ()):
            if lk.producer_id not in snap.producers:
                continue
            if not _is_supplier(snap, lk.producer_id):
                buckets.add(lk.producer_id)
            elif lk.counts:
                per[lk.producer_id].add(card)
    return Counter({k: len(v) for k, v in per.items()}), buckets


def _top_suppliers(snap: Snapshot, counter: Counter, n: int) -> list[dict]:
    ranked = sorted(counter.items(), key=lambda kv: (
        -kv[1], -snap.supplier_row_by_id.get(kv[0], {}).get("product_count", 0),
        snap.producers[kv[0]]["name"].lower()))
    return [_supplier_ref(snap, pid_) for pid_, _ in ranked[:n]]


def _generic(snap: Snapshot, bucket_ids: Iterable[uuid.UUID]) -> list[str]:
    return sorted({snap.producers[b]["name"] for b in bucket_ids})


def _line_reports(snap: Snapshot, line_id: int) -> list[dict]:
    out = []
    for entry in snap.reports_by_line.get(line_id, ()):
        rep = snap.reports.get(entry["slug"])
        if rep:
            out.append({"slug": entry["slug"], "name": rep["name"], "source": entry["source"]})
    return out


def _line_playbooks(snap: Snapshot, reports: list[dict]) -> list[dict]:
    return [{"slug": r["slug"], "name": snap.playbooks[r["slug"]]}
            for r in reports if r["slug"] in snap.playbooks]


def _precompute_cards(snap: Snapshot) -> None:
    """Industries and functions per card, counted over every tree pid that
    reaches it (placements key on the tree's pids: absorbed members and
    pointers included, groups never)."""
    for card, codes in snap.behind_card.items():
        ind: Counter = Counter()
        fns: Counter = Counter()
        seen_ind: set[tuple[str, str]] = set()
        seen_fn: set[tuple[str, str]] = set()
        for code in codes:
            for ind_id, _cat, fn in snap.placements_by_pid.get(code, ()):
                name = _industry_name(snap, ind_id)
                if (name, code) not in seen_ind:
                    seen_ind.add((name, code))
                    ind[name] += 1
                if fn and (fn, code) not in seen_fn:
                    seen_fn.add((fn, code))
                    fns[fn] += 1
        snap.card_industries[card] = ind
        snap.card_functions[card] = fns


# ── Lines ────────────────────────────────────────────────────────────────────

def _line_counts(snap: Snapshot, line_id: int) -> dict:
    cards = [snap.templates[c] for c in snap.line_cards.get(line_id, [])]
    return {"listed": len(cards), "default_view": sum(1 for t in cards if t.default_view)}


def _precompute_lines(snap: Snapshot) -> None:
    items = []
    for ln in snap.lines.values():
        if ln.hidden:
            continue
        cards = snap.line_cards.get(ln.id, [])
        ind: dict[str, set[str]] = defaultdict(set)
        fns: dict[str, set[str]] = defaultdict(set)
        for card in cards:
            for name in snap.card_industries.get(card, ()):
                ind[name].add(card)
            for fn in snap.card_functions.get(card, ()):
                fns[fn].add(card)
        sup_counter, buckets = _suppliers_of_cards(snap, cards)
        reports = _line_reports(snap, ln.id)
        search = " ".join([ln.name, ln.family, ln.subfamily or "", ln.platform or ""]
                          + list(cards) + [snap.templates[c].name for c in cards]).lower()
        item = {
            "id": ln.id,
            "line_key": ln.line_key,
            "name": ln.name,
            "family": {"id": ln.family_id, "name": ln.family},
            "subfamily": _subfamily_ref(ln),
            "platform": ln.platform,
            "in_v1_scope": ln.in_v1_scope,
            "counts": _line_counts(snap, ln.id),
            "industries": _ranked_names(Counter({k: len(v) for k, v in ind.items()})),
            "functions": _ranked_names(Counter({k: len(v) for k, v in fns.items()})),
            "top_suppliers": [],  # filled below, once supplier rows exist
            "generic_suppliers": _generic(snap, buckets),
            "has_report": bool(reports),
            "report_slug": ln.report_slug,
            "reports": [{"slug": r["slug"], "name": r["name"]} for r in reports],
            "playbooks": _line_playbooks(snap, reports),
            "pending": ln.pending,
            "flags": _flags(ln),
            "_cards": cards,
            "_search": search,
            "_sup_counter": sup_counter,
            "_sort": (ln.family.lower(), ln.subfamily is None, (ln.subfamily or "").lower(),
                      ln.name.lower()),
        }
        item["top_suppliers"] = _top_suppliers(snap, sup_counter, TOP_LINE_SUPPLIERS)
        items.append(item)
    items.sort(key=lambda it: it["_sort"])
    snap.line_items = items
    snap.line_item_by_id = {it["id"]: it for it in items}


def _public(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def _cards_in_status(snap: Snapshot, cards: list[str],
                     statuses: frozenset[str] | None) -> list[str]:
    return [c for c in cards if _in_status(snap.templates[c], statuses)]


def _with_status(snap: Snapshot, item: dict, statuses: frozenset[str] | None) -> dict:
    pids = _cards_in_status(snap, item["_cards"], statuses)
    pids.sort(key=lambda c: _status_sort(snap.templates[c]))
    out = _public(item)
    out["product_count"] = len(pids)
    out["pids"] = pids
    return out


def list_lines(db: Session, *, family_id: int | None = None, subfamily_id: int | None = None,
               industry: str | None = None, fn: str | None = None, q: str | None = None,
               has_report: bool | None = None, status: str | None = None) -> dict:
    """Every visible line (hidden ones never), grouped-ready: sorted by
    family, sub-family, then line name. A line with no card under the status
    filter is returned with `product_count = 0`; the page folds it away."""
    statuses = parse_status(status)
    snap = get_snapshot(db)
    ind_name = _resolve_industry_name(snap, industry) if industry else None
    needle = q.strip().lower() if q and q.strip() else None

    out = []
    for it in snap.line_items:
        if family_id is not None and it["family"]["id"] != family_id:
            continue
        if subfamily_id is not None and (it["subfamily"] or {}).get("id") != subfamily_id:
            continue
        if industry and (ind_name is None or ind_name not in it["industries"]):
            continue
        if fn and fn not in it["functions"]:
            continue
        if has_report is not None and it["has_report"] != has_report:
            continue
        if needle and needle not in it["_search"]:
            continue
        out.append(_with_status(snap, it, statuses))

    fam_counts: dict[int, dict] = {}
    ind_c: Counter = Counter()
    fn_c: Counter = Counter()
    for it in out:
        fam = it["family"]
        f = fam_counts.setdefault(fam["id"], {"id": fam["id"], "name": fam["name"], "count": 0,
                                              "_subs": {}})
        f["count"] += 1
        sub = it["subfamily"]
        key = sub["id"] if sub else None
        e = f["_subs"].setdefault(key, {"id": key, "name": sub["name"] if sub else None,
                                        "count": 0})
        e["count"] += 1
        ind_c.update(it["industries"])
        fn_c.update(it["functions"])
    families = []
    for fid in sorted(fam_counts, key=lambda k: fam_counts[k]["name"].lower()):
        f = fam_counts[fid]
        subs = sorted(f.pop("_subs").values(),
                      key=lambda e: (e["id"] is None, e["name"] is None, (e["name"] or "").lower()))
        f["subfamilies"] = subs
        families.append(f)
    return {
        "total": len(out),
        "counts": {
            # lines with at least one listed card (any status)
            "lines_with_listed": sum(1 for it in out if it["counts"]["listed"]),
            # lines with at least one card under the status filter
            "lines_with_products": sum(1 for it in out if it["product_count"]),
        },
        "families": families,
        "industries": _industry_counts(snap, ind_c),
        "functions": _ranked_counts(fn_c),
        "items": out,
    }


def _resolve_industry_name(snap: Snapshot, value: str) -> str | None:
    v = value.strip()
    if v in snap.industry_by_slug:
        return snap.industries[snap.industry_by_slug[v]]["name"]
    iid = snap.industry_by_name_lower.get(v.lower())
    return snap.industries[iid]["name"] if iid else None


def _product_card(snap: Snapshot, code: str) -> dict:
    t = snap.templates[code]
    sup_counter, buckets = _suppliers_of_cards(snap, [code])
    members = []
    if t.is_group:
        for m in t.group_members or []:
            mt = snap.templates.get(m) if isinstance(m, str) else None
            if mt:
                members.append({"pid": m, "name": mt.name, "regions": mt.regions})
    return {
        "pid": code,
        "name": t.name,
        "full_name": t.full_name,
        "form": t.form,
        "kind": t.card_kind,
        "status": _status_short(t.supply_status),
        "regions": t.regions,
        "is_group": t.is_group,
        "group_members": members,
        "industries": _ranked_names(snap.card_industries.get(code, Counter())),
        "functions": _ranked_names(snap.card_functions.get(code, Counter())),
        "top_suppliers": [s["name"] for s in
                          _top_suppliers(snap, sup_counter, TOP_PRODUCT_SUPPLIERS)],
        "generic_suppliers": _generic(snap, buckets),
    }


def caveat_for(snap: Snapshot, slug: str) -> dict:
    """The August-2026 report caveat (WAVE4 REP-3): written on the old line,
    now split into N current lines (N = the report's `market_report_lines`
    rows), not regenerated, August industry vocabulary.

    A join whose line is not published (product_line_id NULL) is named
    `UNPUBLISHED_LINE`, never by its key. `strategy.report_caveat` serves
    this same object."""
    rep = snap.reports[slug]
    rows = snap.report_lines.get(slug, [])
    lines = []
    for r in rows:
        ln = snap.lines.get(r["product_line_id"]) if r["product_line_id"] is not None else None
        if ln is None:
            lines.append({"id": None, "line_key": None, "name": UNPUBLISHED_LINE,
                          "visible": False})
        else:
            lines.append({"id": ln.id, "line_key": ln.line_key, "name": ln.name,
                          "visible": not ln.hidden})
    n = len(lines)
    published = [ln["name"] for ln in lines if ln["id"] is not None]
    unpublished = n - len(published)
    old = rep.get("old_line_name") or rep["name"]
    as_of = rep.get("as_of")
    when = f" in {as_of.strftime('%B %Y')}" if as_of else ""
    text = f"This report was written{when} for the product line then called {old}."
    if n == 0:
        text += " No current product line maps to it."
    elif n == 1:
        text += (f" That line is now called {published[0]}." if published
                 else " That line is not yet published.")
    else:
        names = ", ".join(published)
        if unpublished:
            names += (", plus " if published else "") + (
                f"{unpublished} not yet published")
        text += f" That line is now split into {n} product lines: {names}."
    text += (" The report has not been regenerated since, and the industry names in it"
             + (f" are the {as_of.strftime('%B %Y')} vocabulary." if as_of
                else " are the vocabulary it was written in."))
    return {"as_of": _month(as_of), "text": text, "old_line_name": old,
            "split_into_n_lines": n, "lines": lines}


def find_line(snap: Snapshot, ref: str) -> LineRow | None:
    """A visible line by id, current key or former key."""
    key = ref.strip()
    ln = resolve_line(snap, ref) or resolve_line(snap, key)
    # isdecimal + isascii: "²" or "٣" pass isdigit() but int() rejects them.
    if ln is None and key.isascii() and key.isdecimal():
        ln = snap.lines.get(int(key))
    if ln is None or ln.hidden:
        return None
    return ln


def get_line(db: Session, ref: str, *, status: str | None = None) -> dict | None:
    statuses = parse_status(status)
    snap = get_snapshot(db)
    ln = find_line(snap, ref)
    if ln is None:
        return None
    item = snap.line_item_by_id[ln.id]
    base = _with_status(snap, item, statuses)
    products = [_product_card(snap, c) for c in base["pids"]]

    reports = []
    for r in _line_reports(snap, ln.id):
        rep = snap.reports[r["slug"]]
        cav = caveat_for(snap, r["slug"])
        reports.append({
            "slug": r["slug"], "name": rep["name"], "family": rep["family"],
            "old_line_name": rep["old_line_name"], "as_of": _month(rep["as_of"]),
            "in_v1_scope": bool(rep["in_v1_scope"]), "source": r["source"],
            "word_count": rep["word_count"],
            "playbook_slug": r["slug"] if r["slug"] in snap.playbooks else None,
            "caveat": cav["text"], "split_into_n_lines": cav["split_into_n_lines"],
        })

    # Demand placements summary: industry → categories → the line's cards
    # placed there (every listed card, whatever the status filter).
    by_ind: dict[int, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    placed: set[str] = set()
    for card in item["_cards"]:
        for code in snap.behind_card.get(card, [card]):
            for ind_id, cat_id, _fn in snap.placements_by_pid.get(code, ()):
                by_ind[ind_id][cat_id].add(card)
                placed.add(card)
    demand = []
    for ind_id, cats in by_ind.items():
        ind = snap.industries[ind_id]
        demand.append({
            "industry": ind["name"], "slug": ind["slug"],
            "categories": sorted(({
                "code": snap.categories[cid]["code"], "name": snap.categories[cid]["name"],
                "fn": snap.categories[cid]["fn"], "status": snap.categories[cid]["status"],
                "pids": sorted(pids)} for cid, pids in cats.items()),
                key=lambda c: c["code"]),
        })
    demand.sort(key=lambda d: (-len(d["categories"]), d["industry"]))

    sup_counter = item["_sup_counter"]
    return {
        **base,
        "former_names": sorted({k.split(LINE_KEY_SEP, 1)[-1] for k in ln.former_keys}
                               - {ln.name}),
        "top_suppliers": _top_suppliers(snap, sup_counter, TOP_LINE_SUPPLIERS),
        "suppliers": [
            {**_supplier_ref(snap, pid_), "products_on_line": n,
             "hq": snap.supplier_row_by_id.get(pid_, {}).get("hq")}
            for pid_, n in sorted(sup_counter.items(), key=lambda kv: (
                -kv[1], snap.producers[kv[0]]["name"].lower()))
        ],
        "products": products,
        "reports": reports,
        "demand": {
            "industry_count": len(demand),
            "category_count": sum(len(d["categories"]) for d in demand),
            "placed_product_count": len(placed),
            "industries": demand,
        },
    }


# ── Reports ──────────────────────────────────────────────────────────────────

def report_unpublished(snap: Snapshot, slug: str) -> bool:
    """A report written for a line that is not published: it has joins, and
    none of them is a current line. Such a report is titled after that line
    and no line links to it, so it is not served (design §2.2, frozen
    instruction). `caveat_for` still answers for it."""
    rows = snap.report_lines.get(slug, [])
    return bool(rows) and all(r["product_line_id"] is None for r in rows)


def get_report(db: Session, slug: str) -> dict | None:
    snap = get_snapshot(db)
    rep = snap.reports.get(slug)
    if rep is None or report_unpublished(snap, slug):
        return None
    report = db.get(MarketReport, slug)
    sections = db.execute(select(
        MarketReportSection.section_id, MarketReportSection.ordinal, MarketReportSection.heading,
        MarketReportSection.html, MarketReportSection.word_count,
    ).where(MarketReportSection.slug == slug).order_by(MarketReportSection.ordinal)).all()
    panels = db.execute(select(
        MarketReportPanel.panel, MarketReportPanel.ordinal, MarketReportPanel.heading,
        MarketReportPanel.html, MarketReportPanel.data,
    ).where(MarketReportPanel.slug == slug).order_by(MarketReportPanel.ordinal)).all()

    lines = []
    for r in snap.report_lines.get(slug, []):
        ln = snap.lines.get(r["product_line_id"]) if r["product_line_id"] is not None else None
        if ln is None or ln.hidden:
            continue
        lines.append({"id": ln.id, "line_key": ln.line_key, "name": ln.name,
                      "family": ln.family, "source": r["source"],
                      "product_count": len(snap.line_cards.get(ln.id, []))})
    return {
        "slug": slug,
        "name": rep["name"],
        "family": rep["family"],
        "old_line_name": rep["old_line_name"],
        "as_of": _month(rep["as_of"]),
        "in_v1_scope": bool(rep["in_v1_scope"]),
        "word_count": rep["word_count"],
        "lines": lines,
        "caveat": caveat_for(snap, slug),
        "sections": [dict(s._mapping) for s in sections],
        "panels": [dict(p._mapping) for p in panels],
        "kraljic": report.kraljic if report else None,
        "playbook": ({"slug": slug, "name": snap.playbooks[slug]}
                     if slug in snap.playbooks else None),
    }


# ── Industries ───────────────────────────────────────────────────────────────

def _cards_of(snap: Snapshot, codes: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for code in codes:
        card = snap.card_of.get(code)
        if card and card not in seen:
            seen.append(card)
    return seen


def _member_cards(snap: Snapshot, cat_id: int, line_key: str, ln: LineRow | None,
                  pids: list | None) -> list[str]:
    """The cards a member row reaches: its authored pids, or, for a whole-line
    row ("*"), every template on the line, or, when the key is not a current
    line, the pids the loader placed for that key in this category."""
    if pids is None:
        if ln is not None:
            codes = snap.line_codes.get(ln.id, [])
        else:
            codes = snap.placements_by_cat_key.get((cat_id, line_key), [])
    else:
        codes = [p for p in pids if isinstance(p, str)]
    return _cards_of(snap, codes)


def _product_ref(snap: Snapshot, code: str) -> dict:
    t = snap.templates[code]
    return {"pid": code, "name": t.name, "form": t.form, "is_group": t.is_group,
            "kind": t.card_kind, "status": _status_short(t.supply_status)}


def _line_fields(snap: Snapshot, line_key: str, ln: LineRow | None) -> dict:
    if ln is None:
        return {"line_id": None, "line_key": None, "line_name": UNPUBLISHED_LINE,
                "family": _key_family(snap, line_key)}
    return {"line_id": ln.id, "line_key": ln.line_key, "line_name": ln.name,
            "family": ln.family}


def _category_view(snap: Snapshot, cat_id: int) -> dict:
    cat = snap.categories[cat_id]
    all_cards: set[str] = set()
    all_lines: set[int] = set()
    members = []
    for m in snap.members_by_category.get(cat_id, []):
        ln = resolve_line(snap, m["line_key"])
        if ln is not None and ln.hidden:
            continue
        cards = _member_cards(snap, cat_id, m["line_key"], ln, m["pids"])
        all_cards.update(cards)
        if cards and ln is not None:
            all_lines.add(ln.id)
        members.append({
            **_line_fields(snap, m["line_key"], ln),
            "is_whole_line": bool(m["is_whole_line"]),
            "products": [_product_ref(snap, c) for c in cards],
        })
    shared_refs = []
    for ref in snap.refs_by_category.get(cat_id, []):
        sh = snap.shared.get(ref["shared_id"])
        if not sh:
            continue
        narrow = set(ref["pids"]) if ref["pids"] is not None else None
        sh_lines = []
        for sm in snap.shared_members.get(ref["shared_id"], []):
            ln = resolve_line(snap, sm["line_key"])
            if ln is not None and ln.hidden:
                continue
            pids = sm["pids"]
            if narrow is not None:
                if pids is not None:
                    base = pids
                elif ln is not None:
                    base = snap.line_codes.get(ln.id, [])
                else:
                    base = snap.placements_by_cat_key.get((cat_id, sm["line_key"]), [])
                pids = [p for p in base if p in narrow]
            cards = _member_cards(snap, cat_id, sm["line_key"], ln, pids)
            if narrow is not None and not cards:
                continue
            all_cards.update(cards)
            if cards and ln is not None:
                all_lines.add(ln.id)
            sh_lines.append({
                **_line_fields(snap, sm["line_key"], ln),
                "is_whole_line": bool(sm["is_whole_line"]),
                "products": [_product_ref(snap, c) for c in cards],
            })
        shared_refs.append({"code": sh["code"], "name": sh["name"], "fn": sh["fn"],
                            "narrowed_to": sorted(narrow) if narrow is not None else None,
                            "lines": sh_lines})
    return {
        "code": cat["code"], "name": cat["name"], "alias": cat["alias"], "fn": cat["fn"],
        "status": cat["status"],
        "product_count": len(all_cards), "line_count": len(all_lines),
        "members": members, "shared": shared_refs,
        "build": snap.build_by_category.get(cat_id, []),
        "_cards": all_cards, "_lines": all_lines,
    }


def _precompute_industries(snap: Snapshot) -> None:
    items = []
    for ind_id, ind in snap.industries.items():
        cat_ids = snap.categories_by_industry.get(ind_id, [])
        status_c = Counter(snap.categories[c]["status"] for c in cat_ids)
        cards: set[str] = set()
        line_ids: set[int] = set()
        for c in cat_ids:
            view = _category_view(snap, c)
            cards |= view["_cards"]
            line_ids |= view["_lines"]
        items.append({
            "id": ind_id, "name": ind["name"], "slug": ind["slug"],
            "buyer_one_line": ind["buyer_one_line"], "status": ind["status"],
            "ratified_on": ind["ratified_on"].isoformat() if ind["ratified_on"] else None,
            "scope_status": ind["scope_status"],
            "category_count": len(cat_ids),
            "categories_by_status": {s: status_c.get(s, 0) for s in CATEGORY_STATUSES},
            "product_count": len(cards), "line_count": len(line_ids),
            "out_count": len(snap.out_by_industry.get(ind_id, [])),
        })
    snap.industry_items = items


def list_industries(db: Session) -> dict:
    snap = get_snapshot(db)
    totals = Counter()
    for it in snap.industry_items:
        totals.update(it["categories_by_status"])
    return {
        "total": len(snap.industry_items),
        "categories_by_status": {s: totals.get(s, 0) for s in CATEGORY_STATUSES},
        "items": snap.industry_items,
    }


def _out_row(snap: Snapshot, o: dict) -> dict:
    t = snap.templates.get(o["pid"])
    product = ({"pid": t.code, "name": t.name, "listed": t.visible,
                "redirect_to": t.redirect_to} if t else None)
    targets = []
    for name in o["to_industries"] or []:
        if not isinstance(name, str) or not name.strip():
            continue
        iid = snap.industry_by_name_lower.get(name.strip().lower())
        targets.append({"name": snap.industries[iid]["name"] if iid else name.strip(),
                        "slug": snap.industries[iid]["slug"] if iid else None})
    code = o["code"]
    return {"pid": o["pid"], "product": product,
            "reason": {"code": code, "label": OUT_REASONS.get(code, OUT_REASON_FALLBACK)}
            if code else None,
            "to_industries": targets}


def get_industry(db: Session, slug: str) -> dict | None:
    snap = get_snapshot(db)
    ind_id = snap.industry_by_slug.get(slug)
    if ind_id is None:
        return None
    ind = snap.industries[ind_id]
    item = next(it for it in snap.industry_items if it["id"] == ind_id)
    cats = [_public(_category_view(snap, c)) for c in snap.categories_by_industry.get(ind_id, [])]
    shared_ids: dict[int, int] = {}
    for c in snap.categories_by_industry.get(ind_id, []):
        for ref in snap.refs_by_category.get(c, []):
            shared_ids[ref["shared_id"]] = shared_ids.get(ref["shared_id"], 0) + 1
    shared_objects = []
    for sid, n in shared_ids.items():
        sh = snap.shared.get(sid)
        if sh:
            shared_objects.append({"code": sh["code"], "name": sh["name"], "fn": sh["fn"],
                                   "owner": sh["owner"], "referenced_by": n})
    shared_objects.sort(key=lambda s: s["code"])
    return {
        **{k: item[k] for k in ("id", "name", "slug", "status", "ratified_on", "scope_status",
                                "category_count", "categories_by_status", "product_count",
                                "line_count", "out_count")},
        "reference_buyer": {
            "buyer_one_line": ind["buyer_one_line"], "buyer": ind["buyer"],
            "in_scope": ind["in_scope"], "out_of_scope": ind["out_of_scope"],
            "boundaries": ind["boundaries"],
        },
        "categories": cats,
        "shared_objects": shared_objects,
        "out": [_out_row(snap, o) for o in snap.out_by_industry.get(ind_id, [])],
    }


# ── Suppliers ────────────────────────────────────────────────────────────────

def _integrated(lk: LinkRow) -> bool:
    """`integration_status` when it is stated, else the `integrated` flag."""
    if lk.integration_status:
        return lk.integration_status != "NOT_EVIDENCED"
    return lk.integrated is True


def _card_links(snap: Snapshot, producer_id: uuid.UUID) -> list[LinkRow]:
    """The producer's links on cards."""
    return [lk for lk in snap.links_by_producer.get(producer_id, [])
            if (t := snap.templates.get(lk.code)) is not None and t.visible]


def _precompute_suppliers(snap: Snapshot) -> None:
    rows = []
    generic = []
    for pid_, p in snap.producers.items():
        links = _card_links(snap, pid_)
        if p["is_bucket"]:
            cards = {lk.code for lk in links}
            if cards:
                generic.append({"id": str(pid_), "name": p["name"], "product_count": len(cards)})
            continue
        counting = [lk for lk in links if lk.counts]
        if not counting:
            continue
        fam_c: Counter = Counter()
        fam_ids: dict[str, int | None] = {}
        ind_sets: dict[str, set[str]] = defaultdict(set)
        fn_sets: dict[str, set[str]] = defaultdict(set)
        regions: set[str] = set()
        lines: set[int] = set()
        hq_c: Counter = Counter()
        integrated: set[str] = set()
        cards = sorted({lk.code for lk in counting})
        for card in cards:
            t = snap.templates[card]
            fam = snap.families.get(t.family_id)
            name = fam["name"] if fam else "Unassigned"
            fam_c[name] += 1
            fam_ids[name] = fam["id"] if fam else None
            if t.product_line_id in snap.lines:
                lines.add(t.product_line_id)
            for ind_name in snap.card_industries.get(card, ()):
                ind_sets[ind_name].add(card)
            for fn in snap.card_functions.get(card, ()):
                fn_sets[fn].add(card)
        for lk in counting:
            regions.update(lk.regions)
            if lk.hq_country:
                hq_c[lk.hq_country] += 1
            if _integrated(lk):
                integrated.add(lk.code)
        ind_c = Counter({k: len(v) for k, v in ind_sets.items()})
        hq = p["hq_country"] or (hq_c.most_common(1)[0][0] if hq_c else None)
        rows.append({
            "id": str(pid_),
            "name": p["name"],
            "hq": hq,
            "product_count": len(cards),
            "family_count": len(fam_c),
            "families": [{"id": fam_ids[name], "name": name, "count": n}
                         for name, n in sorted(fam_c.items(), key=lambda kv: (-kv[1], kv[0]))],
            "industry_count": len(ind_c),
            "industries": _industry_counts(snap, ind_c),
            "region_count": len(regions),
            "regions": _region_sorted(regions),
            "line_count": len(lines),
            "integrated_count": len(integrated),
            "_functions": Counter({k: len(v) for k, v in fn_sets.items()}),
            "_regions": Counter(r for lk in counting for r in set(lk.regions)),
            "_cards": set(cards),
            "_lines": lines,
            "_search": " ".join([p["name"]] + snap.aliases.get(pid_, [])).lower(),
        })
    rows.sort(key=lambda r: (-r["product_count"], r["name"].lower()))
    generic.sort(key=lambda r: (-r["product_count"], r["name"].lower()))
    snap.supplier_rows = rows
    snap.supplier_row_by_id = {uuid.UUID(r["id"]): r for r in rows}
    snap.generic_suppliers = generic


def list_suppliers(db: Session, *, q: str | None = None, family_id: int | None = None,
                   industry: str | None = None, min_products: int = 1,
                   integrated: bool | None = None, limit: int = 100, offset: int = 0) -> dict:
    """The directory: producers with at least one counting link on a card."""
    snap = get_snapshot(db)
    needle = q.strip().lower() if q and q.strip() else None
    ind_name = _resolve_industry_name(snap, industry) if industry else None
    out = []
    for r in snap.supplier_rows:
        if r["product_count"] < max(min_products, 1):
            continue
        if needle and needle not in r["_search"]:
            continue
        if family_id is not None and not any(f["id"] == family_id for f in r["families"]):
            continue
        if industry and (ind_name is None or not any(i["name"] == ind_name
                                                     for i in r["industries"])):
            continue
        if integrated is True and not r["integrated_count"]:
            continue
        if integrated is False and r["integrated_count"]:
            continue
        out.append(r)
    fam_c: dict[tuple, int] = Counter()
    ind_c: Counter = Counter()
    for r in out:
        fam_c.update((f["id"], f["name"]) for f in r["families"])
        ind_c.update(i["name"] for i in r["industries"])
    page = out[offset:offset + limit]
    return {
        "total": len(out),
        "limit": limit,
        "offset": offset,
        "families": sorted(({"id": fid, "name": name, "count": n}
                            for (fid, name), n in fam_c.items()),
                           key=lambda e: e["name"].lower()),
        "industries": _industry_counts(snap, ind_c),
        "generic_suppliers": snap.generic_suppliers,
        # The card needs every family but only the head of the industry
        # list; `industry_count` carries the full number.
        "items": [{**_public(r), "industries": r["industries"][:LIST_TOP_INDUSTRIES]}
                  for r in page],
    }


def _link_out(snap: Snapshot, lk: LinkRow) -> dict:
    t = snap.templates[lk.code]
    ln = snap.lines.get(t.product_line_id) if t.product_line_id else None
    return {
        "pid": t.code,
        "name": t.name,
        "kind": t.card_kind,
        "status": _status_short(t.supply_status),
        "family": _family_ref(snap, t.family_id),
        "line": ({"id": ln.id, "line_key": ln.line_key, "name": ln.name}
                 if ln and not ln.hidden else None),
        "is_group": t.is_group,
        "role": lk.role,
        "evidence": {"code": lk.evidence_label,
                     "label": EVIDENCE_LABEL_TEXT.get(lk.evidence_label, lk.evidence_label)},
        "counts": lk.counts,
        "weak": lk.weak_reading is True,
        "integration": {"status": lk.integration_status, "integrated": lk.integrated,
                        "basis": lk.integration_basis},
        "regions": lk.regions,
        "region_uncertain": bool(lk.region_uncertain),
        "origin_restriction": ({"code": lk.origin_restriction, "label": ORIGIN_RESTRICTION_TEXT}
                               if lk.origin_restriction else None),
        "corp_group": lk.corp_group,
        "tags": lk.tags,
        "sites": lk.sites,
        "raw_name": lk.raw_name,
    }


def get_supplier(db: Session, producer_id: uuid.UUID) -> dict | None:
    """Any producer with a link answers by id (`in_directory` says whether it
    is listed). `products` holds its links on cards, counting links first;
    `unlisted_codes` the PIDs it is linked to that are not cards."""
    snap = get_snapshot(db)
    p = snap.producers.get(producer_id)
    if p is None:
        return None
    row = snap.supplier_row_by_id.get(producer_id)
    links = _card_links(snap, producer_id)

    def _order(lk: LinkRow) -> tuple:
        t = snap.templates[lk.code]
        fam = (snap.families.get(t.family_id) or {}).get("name") or ""
        ln = snap.lines.get(t.product_line_id) if t.product_line_id else None
        return (not lk.counts, lk.role == "distributor", fam.lower(),
                (ln.name if ln else "").lower(), lk.code)

    products = [_link_out(snap, lk) for lk in sorted(links, key=_order)]
    on_cards = {lk.code for lk in links}
    unlisted = sorted({lk.code for lk in snap.links_by_producer.get(producer_id, [])
                       if lk.code not in on_cards})

    competitors = []
    if row is not None:
        mine = row["_cards"]
        my_lines = row["_lines"]
        for other in snap.supplier_rows:
            if other["id"] == row["id"]:
                continue
            shared = len(mine & other["_cards"])
            if not shared:
                continue
            competitors.append({
                "id": other["id"], "name": other["name"], "shared_products": shared,
                "shared_lines": len(my_lines & other["_lines"]),
                "shared_pct": round(100.0 * shared / len(mine)) if mine else 0,
                "product_count": other["product_count"],
            })
        competitors.sort(key=lambda c: (-c["shared_products"], -c["shared_lines"],
                                        c["name"].lower()))

    dossier_roles = []
    for r in db.execute(select(
        CommodityIndex.id, CommodityIndex.commodity_key, CommodityIndex.name,
        IndexDossier.region, IndexProducerRole.role, IndexProducerRole.location,
    ).join(IndexDossier, IndexDossier.id == IndexProducerRole.dossier_id)
     .join(CommodityIndex, CommodityIndex.id == IndexDossier.commodity_id)
     .where(IndexProducerRole.producer_id == producer_id)
     .order_by(CommodityIndex.name, IndexProducerRole.role)).all():
        dossier_roles.append({
            "commodity_id": r.id, "series_key": r.commodity_key, "index_name": r.name,
            "region": DROP_REGION_BY_APP.get(r.region, r.region) if r.region else None,
            "role": r.role, "location": r.location,
        })

    base = _public(row) if row else {
        "id": str(producer_id), "name": p["name"], "hq": p["hq_country"],
        "product_count": 0,
    }
    region_c: Counter = row["_regions"] if row else Counter()
    return {
        **base,
        "is_bucket": p["is_bucket"],
        "in_directory": row is not None,
        "aliases": [a for a in snap.aliases.get(producer_id, []) if a != p["name"]],
        "functions": _ranked_counts(row["_functions"]) if row else [],
        "region_breakdown": [{"region": r, "count": region_c[r]}
                             for r in sorted(region_c, key=lambda c: (-region_c[c],
                                                                       _REGION_RANK.get(c, 99)))],
        "products": products,
        "unlisted_codes": unlisted,
        "competitors": competitors[:TOP_COMPETITORS],
        "competitor_count": len(competitors),
        "dossier_roles": dossier_roles,
    }


__all__ = [
    "HIDDEN_LINE_FLAGS", "OUT_REASONS", "REGION_ORDER", "UNPUBLISHED_LINE", "BadStatusFilter",
    "Snapshot", "caveat_for", "derived", "find_line", "get_industry", "get_line", "get_report",
    "report_unpublished",
    "get_snapshot", "get_supplier", "list_industries", "list_lines", "list_suppliers",
    "parse_status", "reset_cache", "resolve_line",
]
