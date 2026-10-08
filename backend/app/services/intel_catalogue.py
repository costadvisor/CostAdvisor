"""Intelligence catalogue: the in-process snapshot behind `/api/intel/facets`
and `/api/intel/products*` (design §2.3, §4.2).

**Why a snapshot.** The grid shows every listed card, each with a 12-month
sparkline and a trend computed from its recipe and the monthly series. Doing
that per request is thousands of line × month products and several bulk
reads. The inputs change only when the content is reloaded, so the whole
catalogue is computed once, in memory, and reused until a cheap
**load-version key** moves. `intel_warm.warm()` builds it when the process
starts, so the first visitor does not pay for it.

**The version key** is one SQL statement of aggregates over platform rows
only (a team's own formulas never move it): the latest `content_loads` id,
count + max(updated_at) of templates and coverage (plus how many coverage rows
are withdrawn), count + max(created_at) of cost lines (a changed recipe is
rewritten, so its lines are new rows), count + max(id) + sum(value) of monthly
values (the loader updates values in place), count + max(id) of placements,
counts of producer links and of those that count toward the supplier floor,
the report joins, and a hash of the line, sub-family, family, producer and
series rows the cards print. A partial reload writes no `content_loads` row,
so the other aggregates are what catch it.

**Which cards the grid shows.** One rule, from `catalog_visibility`:

* **listed** = kind `product` or `group`, with at least one coverage row
  that is not withdrawn. Absorbed, pointer, duplicate and withdrawn cards
  are not listed, nor is a card with no formula;
* **default view** = listed, and the supply status is `live` or
  `supply_exception`. The page sends that as its default `status` filter.

**Answered by id** = every template the content loader wrote. A pointer or a
duplicate answers with the card it points at, plus `redirected_from`.

**Groups** (`GRP-*`) have coverage but no cost lines (their combos carry only
`lines_html`, never parsed). Their numbers come from a member's structured
recipe for the same region.

**Facets and filters** read the ratified demand tree (`category_placements`:
industry and function); a group card takes the union of its members'
placements. The supplier facet and filter use only producer links that count
toward the supplier floor, on listed cards, never bucket placeholders.

**Product lines.** A card's line is its template's `product_line_id`. A line
that is retired or flagged `do_not_publish` is not shown, and neither is a
NULL line: the page says "Product line not yet published". Report joins are
read by `product_line_id` only, so a join row on a key that is not a current
line never reaches a product.

**What is never served:** `internal_meta`, `archival_note`, quote, share and
audit fields (most are not stored at all, §2.4), the line's `axis_meta` and
`confidence`, and the stored `why` of a pricing gap (the page shows a fixed
sentence instead).
"""
from __future__ import annotations

import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.chemical_family import ChemicalFamily
from app.models.formula_template import (
    SUPPLY_STATUSES, FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexMonthlyValue, TypeCode
from app.models.producer import Producer, ProducerFormula
from app.models.product_line import ProductLine
from app.models.subfamily import Subfamily
from app.models.taxonomy_v2 import Category, CategoryPlacement, CategoryShared, Industry
from app.services import intel_market as mk
from app.services.catalog_visibility import (
    DEFAULT_VIEW_STATUSES, EVIDENCE_LABEL_TEXT, ORIGIN_RESTRICTION_TEXT, STATUS_RANK,
    in_default_view, is_listed, status_badge,
)
from app.services.content_drop.sanitize import (
    clean_current_events, clean_supplier_note, served_applications, served_compliance,
    strip_private,
)
from app.services.drop.common import REGION_MAP

# The templates the content loader writes (`content_drop.catalogue.SOURCES`;
# not imported from there, so the API does not load the loader and its
# write-side dependencies at import). A test keeps the two equal.
CATALOGUE_SOURCES = ("FORMULA_COMBOS", "AUTO_GROUPS", "CURATED_CONTENT")
DROP_REGION_BY_APP = {app: drop for drop, app in REGION_MAP.items()}
DEFAULT_REGION = "EU"
# The existing app route that starts a team cost model from a platform
# template: the Cost Model Builder, opened with `state.productId` of a team
# product whose `formula_template_id` is the template (it auto-loads the recipe).
ADD_TO_PORTFOLIO_ROUTE = "/cost-models/new"
# Catalogue recipes are bulk chemicals priced per tonne.
ADD_TO_PORTFOLIO_UNIT = "t"
# Kinds that answer with another card.
REDIRECT_KINDS = ("pointer", "duplicate")
# The editorial block types a product page reads (the public formula set,
# design §4.2). Any other platform block type is never read here.
PUBLIC_FORMULA_BLOCKS = (
    "functionalities", "applications", "supplier_note", "compliance", "macro_drivers",
    "substitution", "synthesis_route", "current_events", "negotiation_note",
)
# The line a card shows when its product line is not published (design §4.1).
UNPUBLISHED_LINE_TEXT = "Product line not yet published"

# Fixed buyer-facing warnings (design §4.2). Never the stored `why`.
WARNING_PRICING_GAP = "pricing_gap"
WARNING_MARGIN_STATUS = "margin_status"
PRICING_GAP_TEXT = "One cost line ({line}) has no public price index. This trend is partial."
PRICING_GAP_TEXT_NO_LINE = "One cost line has no public price index. This trend is partial."
MARGIN_STATUS_TEXT = "The margin in this formula is being re-estimated."

# ── Load version ─────────────────────────────────────────────────────────────

_VERSION_SQL = text("""
SELECT concat_ws('|',
  (SELECT coalesce(max(id), 0) FROM content_loads),
  (SELECT count(*) || ':' || coalesce(max(updated_at)::text, '')
     FROM formula_templates WHERE team_id IS NULL),
  (SELECT count(*) || ':' || count(c.withdrawn_at) || ':' || coalesce(max(c.updated_at)::text, '')
     FROM formula_region_coverage c JOIN formula_templates t ON t.id = c.template_id
    WHERE t.team_id IS NULL),
  (SELECT count(*) || ':' || coalesce(max(f.created_at)::text, '')
     FROM formula_template_components f JOIN formula_templates t ON t.id = f.template_id
    WHERE t.team_id IS NULL),
  (SELECT count(*) || ':' || coalesce(max(id), 0) || ':' || coalesce(sum(value), 0)
     FROM index_monthly_values),
  (SELECT count(*) || ':' || coalesce(max(id), 0) FROM category_placements),
  (SELECT count(*) || ':' || count(*) FILTER (WHERE counts_toward_floor) || ':'
          || coalesce(max(created_at)::text, '') FROM producer_formulas),
  (SELECT count(*) || ':' || count(product_line_id) FROM market_report_lines),
  (SELECT md5(coalesce(string_agg(id || '/' || name || '/' || family_id || '/'
                                  || coalesce(subfamily_id::text, '') || '/'
                                  || coalesce(platform, '') || '/'
                                  || coalesce(flags::text, '') || '/'
                                  || coalesce(retired_at::text, ''), ',' ORDER BY id), ''))
     FROM product_lines),
  (SELECT md5(coalesce(string_agg(id || '/' || coalesce(name, ''), ',' ORDER BY id), ''))
     FROM subfamilies),
  (SELECT md5(coalesce(string_agg(id || '/' || name, ',' ORDER BY id), ''))
     FROM chemical_families),
  (SELECT md5(coalesce(string_agg(id::text || '/' || name || '/' || is_bucket::text, ','
                                  ORDER BY id), '')) FROM producers),
  (SELECT md5(coalesce(string_agg(id || '/' || name || '/' || coalesce(provider, '') || '/'
                                  || coalesce(frequency, ''), ',' ORDER BY id), ''))
     FROM commodity_indexes WHERE commodity_key IS NOT NULL)
)
""")

_DATA_VERSION_SQL = text("""
SELECT source_commit, source_date, finished_at FROM content_loads ORDER BY id DESC LIMIT 1
""")


def load_version(db: Session) -> str:
    """The cheap key the snapshot is valid for."""
    return db.execute(_VERSION_SQL).scalar_one()


def _data_version(db: Session) -> dict | None:
    """Which source commit the content came from (design §1.2): the latest
    real content load. None before any full load."""
    row = db.execute(_DATA_VERSION_SQL).first()
    if row is None:
        return None
    commit, date, finished = row
    return {"source_commit": commit,
            "source_date": date.isoformat() if date else None,
            "loaded_at": finished.isoformat() if finished else None}


# ── Snapshot shapes ──────────────────────────────────────────────────────────

@dataclass
class Combo:
    """One priced (region, variant) of a product, with the recipe that prices
    it. For a group card, `source_pid` is the member whose recipe it is."""
    region: str                      # drop code (EU)
    app_region: str                  # regions.code (Europe)
    variant: str
    combo_id: str | None
    coverage_id: Any
    source_pid: str
    lines: list[dict]
    margin_pct: float | None
    trust_grade: str | None
    needs_review: bool
    reviewed_at: Any
    provenance: str | None


@dataclass
class Product:
    id: Any
    code: str
    name: str
    full_name: str | None
    form: str | None
    cas: str | None
    volatile: bool | None
    reference_grade: str | None
    kind: str
    supply_status: str | None
    redirect_to: str | None
    family_id: int | None
    family: str | None
    line: dict | None                 # the published line row (see _load_lines), or None
    is_group: bool
    absorbed_into: str | None
    group_members: list[str]
    catalog_meta: dict
    has_live_coverage: bool = False
    combos: list[Combo] = field(default_factory=list)
    listed: bool = False
    default_view: bool = False

    @property
    def line_id(self) -> int | None:
        return self.line["id"] if self.line else None

    @property
    def subfamily_id(self) -> int | None:
        return self.line["subfamily_id"] if self.line else None

    @property
    def regions(self) -> list[str]:
        seen: list[str] = []
        for c in self.combos:
            if c.region not in seen:
                seen.append(c.region)
        return seen

    def default_region(self) -> str | None:
        regions = self.regions
        if not regions:
            return None
        return DEFAULT_REGION if DEFAULT_REGION in regions else regions[0]

    def combo_for(self, region: str | None, variant: str | None = None) -> Combo | None:
        """The combo for a drop region; the first (drop order) when the region
        has several variants and none is asked for."""
        matches = [c for c in self.combos if c.region == region]
        if not matches:
            return None
        if variant is not None:
            for c in matches:
                if c.variant == variant:
                    return c
            return None
        return matches[0]


@dataclass
class Catalogue:
    version: str
    built_at: float
    build_ms: float
    timeline: mk.Timeline
    series: dict[int, mk.SeriesData]
    products: dict[str, Product]
    items: list[dict]                     # listed cards, display order
    # per-code filter keys (listed cards only)
    industries_of: dict[str, set[str]]
    functions_of: dict[str, set[str]]
    suppliers_of: dict[str, set[str]]
    placements_by_pid: dict[str, list[dict]]
    lines: dict[int, dict]
    industries: list[str]
    data_version: dict | None
    _facets: dict = field(default_factory=dict)


_lock = threading.Lock()
_cache: Catalogue | None = None


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def get_catalogue(db: Session) -> Catalogue:
    """The snapshot for the current load version: built on the first call
    and whenever the key moves."""
    global _cache
    version = load_version(db)
    cached = _cache
    if cached is not None and cached.version == version:
        return cached
    with _lock:
        cached = _cache
        if cached is not None and cached.version == version:
            return cached
        _cache = build_catalogue(db, version)
        return _cache


# ── Build ────────────────────────────────────────────────────────────────────

def _f(x) -> float | None:
    return float(x) if x is not None else None


def _timeline(rows: list[tuple]) -> mk.Timeline:
    actual = sorted({(y, m) for _, y, m, _, kind in rows if kind == "actual"})
    forecast = sorted({(y, m) for _, y, m, _, kind in rows if kind == "forecast"})
    return mk.Timeline(actual=tuple(actual), forecast=tuple(p for p in forecast
                                                            if not actual or p > actual[-1]))


def _load_series(db: Session) -> tuple[dict[int, mk.SeriesData], mk.Timeline]:
    series: dict[int, mk.SeriesData] = {}
    for cid, key, name, provider, freq, base in db.query(
            CommodityIndex.id, CommodityIndex.commodity_key, CommodityIndex.name,
            CommodityIndex.provider, CommodityIndex.frequency, CommodityIndex.base_period):
        series[cid] = mk.SeriesData(id=cid, key=key, name=name, agency=provider, freq=freq,
                                    base_period=base)
    rows = db.query(IndexMonthlyValue.commodity_id, IndexMonthlyValue.year,
                    IndexMonthlyValue.month, IndexMonthlyValue.value,
                    IndexMonthlyValue.kind).all()
    for cid, y, m, v, kind in rows:
        s = series.get(cid)
        if s is None:
            continue
        (s.actual if kind == "actual" else s.forecast)[(int(y), int(m))] = float(v)
    return series, _timeline(rows)


def line_flags(flags: dict | None) -> list[str]:
    """A line's flags as codes the page can badge: `pending` (supplier
    validation still under way) and `do_not_publish`. The other flag keys
    (`route_flag`, `validation_question`, `retracted`) are authoring notes and
    are not shown: on the one line that carries `retracted` it names a
    retracted naming rule, not a retracted line."""
    flags = flags or {}
    out = []
    if flags.get("do_not_publish"):
        out.append("do_not_publish")
    if "pending" in str(flags.get("status") or ""):
        out.append("pending")
    return out


def _load_lines(db: Session) -> dict[int, dict]:
    """The published product lines: not retired, not `do_not_publish`. A line
    left out here reads "Product line not yet published" on every card."""
    with_report = {lid for (lid,) in db.execute(text(
        "SELECT DISTINCT product_line_id FROM market_report_lines "
        "WHERE product_line_id IS NOT NULL"))}
    subfamilies = {sid: name for sid, name in db.query(Subfamily.id, Subfamily.name)}
    out = {}
    for row in db.query(ProductLine.id, ProductLine.name, ProductLine.family_id,
                        ProductLine.subfamily_id, ProductLine.platform, ProductLine.flags,
                        ProductLine.report_slug).filter(ProductLine.retired_at.is_(None)):
        flags = row.flags or {}
        if flags.get("do_not_publish"):
            continue
        out[row.id] = {
            "id": row.id, "name": row.name, "family_id": row.family_id,
            "subfamily_id": row.subfamily_id,
            "subfamily_name": subfamilies.get(row.subfamily_id) if row.subfamily_id else None,
            "platform": row.platform, "flags": line_flags(flags),
            "report_slug": row.report_slug, "has_report": row.id in with_report,
        }
    return out


def _load_products(db: Session, lines: dict[int, dict]) -> dict[str, Product]:
    families = {fid: name for fid, name in db.query(ChemicalFamily.id, ChemicalFamily.name)}
    out: dict[str, Product] = {}
    # Columns only: `internal_meta` and `archival_note` are never read here.
    q = (db.query(FormulaTemplate.id, FormulaTemplate.code, FormulaTemplate.name,
                  FormulaTemplate.full_name, FormulaTemplate.form, FormulaTemplate.cas_number,
                  FormulaTemplate.volatile, FormulaTemplate.reference_grade,
                  FormulaTemplate.card_kind, FormulaTemplate.supply_status,
                  FormulaTemplate.redirect_to, FormulaTemplate.family_id,
                  FormulaTemplate.product_line_id, FormulaTemplate.is_group,
                  FormulaTemplate.absorbed_into, FormulaTemplate.group_members,
                  FormulaTemplate.catalog_meta)
         .filter(FormulaTemplate.team_id.is_(None), FormulaTemplate.code.isnot(None),
                 FormulaTemplate.catalog_meta["source"].astext.in_(CATALOGUE_SOURCES)))
    for t in q:
        out[t.code] = Product(
            id=t.id, code=t.code, name=t.name, full_name=t.full_name, form=t.form,
            cas=t.cas_number, volatile=t.volatile, reference_grade=t.reference_grade,
            kind=t.card_kind, supply_status=t.supply_status, redirect_to=t.redirect_to,
            family_id=t.family_id, family=families.get(t.family_id),
            line=lines.get(t.product_line_id) if t.product_line_id else None,
            is_group=bool(t.is_group), absorbed_into=t.absorbed_into,
            group_members=list(t.group_members or []), catalog_meta=t.catalog_meta or {},
        )
    return out


def _load_recipes(db: Session, ids: list) -> tuple[dict, dict]:
    """(live coverage by (template_id, app_region, variant), lines by the same
    key). A withdrawn coverage row is not a combo: the card is not priced there."""
    coverage = {}
    for c in db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.template_id.in_(ids),
            FormulaRegionCoverage.withdrawn_at.is_(None)):
        coverage[(c.template_id, c.region, c.variant or "")] = c
    lines: dict[tuple, list[dict]] = defaultdict(list)
    q = (db.query(FormulaTemplateComponent.template_id, FormulaTemplateComponent.region,
                  FormulaTemplateComponent.variant, FormulaTemplateComponent.name,
                  FormulaTemplateComponent.component_type,
                  FormulaTemplateComponent.cost_category,
                  FormulaTemplateComponent.weight_pct,
                  FormulaTemplateComponent.commodity_id, TypeCode.code,
                  FormulaTemplateComponent.sort_order)
         .outerjoin(TypeCode, TypeCode.id == FormulaTemplateComponent.type_code_id)
         .filter(FormulaTemplateComponent.template_id.in_(ids),
                 FormulaTemplateComponent.region.isnot(None))
         .order_by(FormulaTemplateComponent.template_id, FormulaTemplateComponent.region,
                   FormulaTemplateComponent.variant, FormulaTemplateComponent.sort_order))
    for tid, region, variant, name, ctype, cat, weight, cid, tag, order in q:
        lines[(tid, region, variant or "")].append({
            "label": name, "component_type": ctype, "cost_category": cat,
            "weight": float(weight), "commodity_id": cid, "tag": tag, "sort_order": order,
        })
    return coverage, lines


def _combo(region: str, variant: str, combo_id, source: Product, coverage,
           lines: list[dict]) -> Combo:
    return Combo(
        region=region, app_region=REGION_MAP.get(region, region), variant=variant,
        combo_id=combo_id, coverage_id=coverage.id if coverage else None,
        source_pid=source.code, lines=lines,
        margin_pct=_f(coverage.margin_pct) if coverage else None,
        trust_grade=coverage.trust_grade if coverage else None,
        needs_review=bool(coverage.needs_review) if coverage else False,
        reviewed_at=coverage.reviewed_at if coverage else None,
        provenance=coverage.provenance if coverage else None,
    )


def _attach_combos(products: dict[str, Product], coverage: dict, lines: dict) -> None:
    live_templates = {tid for tid, _region, _variant in coverage}
    for p in products.values():
        p.has_live_coverage = p.id in live_templates
        if p.is_group:
            continue
        for meta in p.catalog_meta.get("combos") or []:
            region = meta.get("region")
            app = REGION_MAP.get(region)
            variant = meta.get("variant") or ""
            cov = coverage.get((p.id, app, variant))
            if cov is None:
                continue
            p.combos.append(_combo(region, variant, meta.get("id"), p, cov,
                                   lines.get((p.id, app, variant), [])))
    # Groups: each region the group is covered in, priced by a member's
    # structured recipe (the combo's own member first).
    for p in products.values():
        if not p.is_group:
            continue
        members = [products[m] for m in p.group_members
                   if m in products and not products[m].is_group]
        for meta in p.catalog_meta.get("combos") or []:
            region = meta.get("region")
            app = REGION_MAP.get(region)
            if (p.id, app, "") not in coverage:
                continue
            first = products.get(meta.get("pid"))
            candidates = [m for m in members if m is first] + [m for m in members if m is not first]
            chosen = None
            for member in candidates:
                mc = member.combo_for(region)
                if mc is not None:
                    chosen = mc
                    break
            if chosen is None:
                continue
            p.combos.append(Combo(
                region=region, app_region=app or region, variant=chosen.variant,
                combo_id=meta.get("id"), coverage_id=chosen.coverage_id,
                source_pid=chosen.source_pid, lines=chosen.lines,
                margin_pct=chosen.margin_pct, trust_grade=chosen.trust_grade,
                needs_review=chosen.needs_review, reviewed_at=chosen.reviewed_at,
                provenance=chosen.provenance,
            ))


def _load_placements(db: Session) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    q = (db.query(CategoryPlacement.pid, Industry.name, Industry.slug, Category.code,
                  Category.name, Category.status, CategoryPlacement.fn,
                  CategoryPlacement.name, CategoryShared.code, Industry.sort_order,
                  Category.sort_order)
         .join(Category, Category.id == CategoryPlacement.category_id)
         .join(Industry, Industry.id == CategoryPlacement.industry_id)
         .outerjoin(CategoryShared, CategoryShared.id == CategoryPlacement.via_shared_id)
         .order_by(Industry.name, Category.sort_order, Category.code))
    for pid, ind, slug, code, cname, status, fn, pname, shared, _io, _co in q:
        out[pid].append({
            "industry": ind, "industry_slug": slug, "category_code": code,
            "category_name": cname, "placement_name": pname, "fn": fn, "status": status,
            "via_shared": shared,
        })
    return out


def _load_counting_suppliers(db: Session) -> dict[str, set[str]]:
    """code → the producers whose link on that card counts toward the
    supplier floor. Bucket placeholders are never suppliers."""
    out: dict[str, set[str]] = defaultdict(set)
    q = (db.query(ProducerFormula.subject_code, Producer.name)
         .join(Producer, Producer.id == ProducerFormula.producer_id)
         .filter(ProducerFormula.counts_toward_floor.is_(True), Producer.is_bucket.is_(False)))
    for code, name in q:
        out[code].add(name)
    return out


def _ref(id_, name) -> dict | None:
    return {"id": id_, "name": name} if id_ is not None else None


def _taxonomy(p: Product) -> dict:
    """The card's family, sub-family and product line, null-safe. A
    sub-family's name may be null (the axis leaves one node unnamed): the
    page skips it in the breadcrumb."""
    line = p.line
    return {
        "family": _ref(p.family_id, p.family),
        "subfamily": _ref(line["subfamily_id"], line["subfamily_name"])
        if line and line["subfamily_id"] else None,
        "line": {"id": line["id"], "name": line["name"]} if line else None,
    }


def _status_ref(code: str | None, *, describe: bool = False) -> dict | None:
    badge = status_badge(code)
    if badge is None:
        return None
    return badge if describe else {k: badge[k] for k in ("code", "label", "tone")}


def _card(p: Product, timeline: mk.Timeline, series: dict) -> dict:
    region = p.default_region()
    combo = p.combo_for(region)
    cs = mk.combo_series(combo.lines, series, timeline) if combo else None
    nums = mk.card_numbers(cs, timeline)
    regions = [r for r in mk.REGION_ORDER if r in p.regions] + \
        [r for r in p.regions if r not in mk.REGION_ORDER]
    return {
        "pid": p.code,
        "name": p.name,
        "full_name": p.full_name,
        "kind": p.kind,
        "status": _status_ref(p.supply_status),
        **_taxonomy(p),
        "has_report": bool(p.line and p.line["has_report"]),
        "form": p.form,
        "regions": regions,
        "region_count": len(regions),
        "is_group": p.is_group,
        "member_count": len(p.group_members) if p.is_group else None,
        "volatile": p.volatile,
        **nums,
        "sparkline_region": region if cs else None,
        "top_lines": mk.top_lines(combo.lines) if combo else [],
    }


def _sort_key(item: dict) -> tuple:
    family = item["family"]["name"] if item["family"] else None
    status = item["status"]["code"] if item["status"] else None
    return ((family or "~").casefold(), STATUS_RANK.get(status, len(STATUS_RANK)),
            item["name"].casefold(), item["pid"])


def build_catalogue(db: Session, version: str | None = None) -> Catalogue:
    t0 = time.perf_counter()
    version = version or load_version(db)
    series, timeline = _load_series(db)
    lines = _load_lines(db)
    products = _load_products(db, lines)
    coverage, recipe_lines = _load_recipes(db, [p.id for p in products.values()])
    _attach_combos(products, coverage, recipe_lines)
    placements = _load_placements(db)
    suppliers = _load_counting_suppliers(db)
    industries = [name for (name,) in db.query(Industry.name).order_by(Industry.name)]

    items: list[dict] = []
    industries_of: dict[str, set[str]] = {}
    functions_of: dict[str, set[str]] = {}
    suppliers_of: dict[str, set[str]] = {}
    for p in products.values():
        p.listed = is_listed(p.kind, p.has_live_coverage)
        p.default_view = in_default_view(p.kind, p.supply_status, p.has_live_coverage)
        if not p.listed:
            continue
        items.append(_card(p, timeline, series))
        pids = p.group_members if p.is_group else [p.code]
        rows = [r for pid in pids for r in placements.get(pid, [])]
        industries_of[p.code] = {r["industry"] for r in rows}
        functions_of[p.code] = {r["fn"] for r in rows if r["fn"]}
        suppliers_of[p.code] = set(suppliers.get(p.code, ()))
    items.sort(key=_sort_key)

    return Catalogue(
        version=version, built_at=time.time(),
        build_ms=round((time.perf_counter() - t0) * 1000, 1),
        timeline=timeline, series=series, products=products, items=items,
        industries_of=industries_of, functions_of=functions_of, suppliers_of=suppliers_of,
        placements_by_pid=dict(placements), lines=lines, industries=industries,
        data_version=_data_version(db),
    )


# ── Facets ───────────────────────────────────────────────────────────────────

SUPPLIER_FACET_LIMIT = 60


def parse_statuses(value: str | None) -> frozenset[str] | None:
    """The `status` query value: a comma list of supply statuses, or `all`.
    Absent, blank or `all` → None (every listed card). Raises ValueError on
    an unknown code."""
    if value is None or not value.strip() or value.strip().lower() == "all":
        return None
    codes = {c.strip().lower() for c in value.split(",") if c.strip()}
    unknown = codes - set(SUPPLY_STATUSES)
    if unknown:
        raise ValueError(f"Unknown status {sorted(unknown)}. Allowed: {list(SUPPLY_STATUSES)} or 'all'")
    return None if codes == set(SUPPLY_STATUSES) else frozenset(codes)


def _in_statuses(item: dict, statuses: frozenset[str] | None) -> bool:
    return statuses is None or (item["status"] is not None and item["status"]["code"] in statuses)


def facets(cat: Catalogue, statuses: frozenset[str] | None = None) -> dict:
    """Sidebar facets and header counts. The facet counts cover the cards in
    `statuses` (None = every listed card); `statuses[]`, `counts.listed`
    and `counts.default_view` always cover every listed card, so the status
    checkboxes can show their own counts. Memoised per status set."""
    cached = cat._facets.get(statuses)
    if cached is not None:
        return cached
    items = [i for i in cat.items if _in_statuses(i, statuses)]
    fam: Counter = Counter()
    fam_names: dict[int, str] = {}
    for i in items:
        if i["family"]:
            fam[i["family"]["id"]] += 1
            fam_names[i["family"]["id"]] = i["family"]["name"]
    ind = Counter(x for i in items for x in cat.industries_of.get(i["pid"], ()))
    fns = Counter(x for i in items for x in cat.functions_of.get(i["pid"], ()))
    sup = Counter(x for i in items for x in cat.suppliers_of.get(i["pid"], ()))
    reg = Counter(r for i in items for r in i["regions"])
    trend = Counter(i["trend_dir"] for i in items if i["trend_dir"])
    by_status = Counter(i["status"]["code"] for i in cat.items if i["status"])
    top_sup = sorted(sup.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:SUPPLIER_FACET_LIMIT]
    out = {
        "status_filter": sorted(statuses, key=STATUS_RANK.get) if statuses else None,
        "statuses": [{**_status_ref(code), "count": by_status.get(code, 0)}
                     for code in SUPPLY_STATUSES],
        "families": [{"id": fid, "name": fam_names[fid], "count": c}
                     for fid, c in sorted(fam.items(), key=lambda kv: fam_names[kv[0]].casefold())],
        # Every industry of the demand tree, zero counts included (greyed).
        "industries": [{"name": n, "count": ind.get(n, 0)} for n in cat.industries],
        "functions": [{"name": n, "count": c} for n, c in sorted(fns.items(),
                                                                 key=lambda kv: kv[0].casefold())],
        "suppliers": [{"name": n, "count": c} for n, c in top_sup],
        "suppliers_total": len(sup),
        "regions": [{"code": r, "app_region": REGION_MAP.get(r), "name": mk.REGION_NAMES.get(r, r),
                     "color": mk.REGION_COLORS.get(r), "count": reg[r]}
                    for r in mk.REGION_ORDER if reg.get(r)],
        "trends": [{"name": t, "count": trend.get(t, 0)} for t in ("up", "flat", "down")],
        "counts": {
            "listed": len(cat.items),
            "default_view": sum(by_status.get(s, 0) for s in DEFAULT_VIEW_STATUSES),
            "products": len(items),
            "lines": len({i["line"]["id"] for i in items if i["line"]}),
            "families": len(fam),
            "groups": sum(1 for i in items if i["is_group"]),
            "has_report": sum(1 for i in items if i["has_report"]),
            "regions": sum(1 for r in mk.REGION_ORDER if reg.get(r)),
        },
        "data_version": cat.data_version,
    }
    cat._facets[statuses] = out
    return out


# ── Queries over the snapshot ────────────────────────────────────────────────

def normalize_region(region: str | None) -> str | None:
    """A caller's region as a drop code: `eu` → `EU`, and an app code
    (`Europe`, `Latam`, `GLOBAL`) is mapped back to its drop code."""
    if region is None or not region.strip():
        return None
    r = region.strip()
    if r in DROP_REGION_BY_APP:
        return DROP_REGION_BY_APP[r]
    return r.upper()


def filter_items(cat: Catalogue, *, statuses: frozenset[str] | None = None,
                 family_id: int | None = None, subfamily_id: int | None = None,
                 line_id: int | None = None, industry: str | None = None,
                 fn: str | None = None, supplier: str | None = None, q: str | None = None,
                 has_report: bool | None = None, region: str | None = None,
                 trend: str | None = None) -> list[dict]:
    needle = q.strip().casefold() if q and q.strip() else None
    out = []
    for item in cat.items:
        code = item["pid"]
        if not _in_statuses(item, statuses):
            continue
        if family_id is not None and (item["family"] or {}).get("id") != family_id:
            continue
        if subfamily_id is not None and (item["subfamily"] or {}).get("id") != subfamily_id:
            continue
        if line_id is not None and (item["line"] or {}).get("id") != line_id:
            continue
        if industry and industry not in cat.industries_of.get(code, ()):
            continue
        if fn and fn not in cat.functions_of.get(code, ()):
            continue
        if supplier and supplier not in cat.suppliers_of.get(code, ()):
            continue
        if has_report is not None and item["has_report"] != has_report:
            continue
        if region and region not in item["regions"]:
            continue
        if trend and item["trend_dir"] != trend:
            continue
        if needle:
            p = cat.products[code]
            line = p.line or {}
            hay = " ".join(x for x in (code, p.name, p.full_name, line.get("name"),
                                       line.get("subfamily_name"), p.family, p.cas)
                           if x).casefold()
            if needle not in hay:
                continue
        out.append(item)
    return out


# ── Product detail (header + tab 2) ──────────────────────────────────────────

class NotFound(LookupError):
    """An unknown product, region or variant: the router answers 404."""


def resolve(cat: Catalogue, code: str) -> tuple[Product, dict | None]:
    """The card a PID answers with, and `redirected_from` when a pointer or a
    duplicate sent it to another card (design §4.2). A redirect whose target
    is not a loaded card answers with the card itself."""
    p = cat.products.get(code)
    if p is None:
        raise NotFound(f"Unknown product {code!r}")
    redirected = None
    seen = {p.code}
    while p.kind in REDIRECT_KINDS and p.redirect_to:
        target = cat.products.get(p.redirect_to)
        if target is None or target.code in seen:
            break
        if redirected is None:
            redirected = {"pid": code, "kind": p.kind}
        seen.add(target.code)
        p = target
    return p, redirected


def warnings_for(*cards: Product | None) -> list[dict]:
    """The fixed buyer-facing warnings of a card (and of the member whose
    recipe prices it, for a group). Built from structure only: the pricing
    gap's line label and the margin status's presence, never a stored `why`."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for p in cards:
        if p is None:
            continue
        meta = p.catalog_meta
        gap = meta.get("pricing_gap")
        if isinstance(gap, dict):
            line = gap.get("line")
            msg = PRICING_GAP_TEXT.format(line=line) if line else PRICING_GAP_TEXT_NO_LINE
            if (WARNING_PRICING_GAP, msg) not in seen:
                seen.add((WARNING_PRICING_GAP, msg))
                out.append({"kind": WARNING_PRICING_GAP, "text": msg})
        if meta.get("margin_status") and (WARNING_MARGIN_STATUS, MARGIN_STATUS_TEXT) not in seen:
            seen.add((WARNING_MARGIN_STATUS, MARGIN_STATUS_TEXT))
            out.append({"kind": WARNING_MARGIN_STATUS, "text": MARGIN_STATUS_TEXT})
    return out


def _blocks(db: Session, subject_type: str, codes: list[str],
            block_types: tuple[str, ...]) -> dict[str, dict[str, dict]]:
    """`{subject_code: {block_type: {body, provenance, expires_at, is_stale}}}`:
    platform blocks of the given types at their current version, the
    region-wildcard row."""
    from app.models.editorial import EditorialBlock, EditorialBlockVersion
    if not codes:
        return {}
    q = (db.query(EditorialBlock.subject_code, EditorialBlock.block_type, EditorialBlock.region,
                  EditorialBlock.provenance, EditorialBlock.expires_at, EditorialBlock.is_stale,
                  EditorialBlockVersion.body_format, EditorialBlockVersion.body_text,
                  EditorialBlockVersion.body_json)
         .join(EditorialBlockVersion, EditorialBlockVersion.id == EditorialBlock.current_version_id)
         .filter(EditorialBlock.team_id.is_(None), EditorialBlock.subject_type == subject_type,
                 EditorialBlock.subject_code.in_(codes),
                 EditorialBlock.block_type.in_(block_types)))
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for code, btype, region, prov, expires, stale, fmt, body_text, body_json in q:
        if region is not None and btype in out[code]:
            continue  # the wildcard (region NULL) row wins
        out[code][btype] = {
            "body": body_json if fmt == "json" else body_text,
            "provenance": prov, "expires_at": expires, "is_stale": bool(stale),
        }
    return out


def _region_rows(p: Product) -> list[dict]:
    rows = []
    for r in p.regions:
        combos = [c for c in p.combos if c.region == r]
        rows.append({
            "code": r, "app_region": REGION_MAP.get(r), "name": mk.REGION_NAMES.get(r, r),
            "color": mk.REGION_COLORS.get(r),
            "variants": [c.variant for c in combos if c.variant],
            "combo_ids": [c.combo_id for c in combos],
            "source_pid": combos[0].source_pid if combos else None,
        })
    return rows


# `buildSynthesisNarrative`'s filter for the cost-line fallback feedstocks.
_INFRA = re.compile(
    r"^margin$|industrial electricity|fixed costs lci|packaging|process water", re.IGNORECASE)


def _dicts(value: Any) -> list[dict]:
    return [strip_private(v) for v in value or [] if isinstance(v, dict)] \
        if isinstance(value, list) else []


def _merged(rows: Iterable[list | None]) -> list:
    out: list = []
    for values in rows:
        for v in values or []:
            if v not in out:
                out.append(v)
    return out


# The screen order of the makers panel (design §4.2): verified and counted
# first, then not yet audited, then the rest, distributors last. The API keeps
# the authored order; the page sorts by this rank, stably.
def _display_rank(label: str, counts: bool) -> int:
    if label == "distributor":
        return 3
    if label == "verified" and counts:
        return 0
    if label == "not_audited":
        return 1
    return 2


def suppliers(db: Session, code: str) -> list[dict]:
    """The makers panel, from `producer_formulas` only (there is no
    `suppliers` block): one row per authored supplier name, in authored order
    (`row_order`). A name that resolves to several producers (`A / B`) is one
    row with several `producers`. No share, no quote, no audit wording."""
    links = (db.query(ProducerFormula, Producer.id, Producer.name, Producer.is_bucket)
             .join(Producer, Producer.id == ProducerFormula.producer_id)
             .filter(ProducerFormula.subject_code == code)
             .order_by(ProducerFormula.row_order, Producer.name).all())
    groups: dict[str, list] = {}
    for link in links:
        groups.setdefault(link[0].raw_name or link[2], []).append(link)
    out = []
    for name, rows in groups.items():
        first = rows[0][0]
        label = first.evidence_label or "not_audited"
        counts = bool(first.counts_toward_floor)
        out.append({
            "name": name,
            "canonical": [pname for _pf, _pid, pname, _b in rows],
            "producers": [{"producer_id": str(pid), "name": pname, "is_bucket": bool(bucket)}
                          for _pf, pid, pname, bucket in rows],
            "is_bucket": all(bool(bucket) for *_x, bucket in rows),
            "hq": first.hq_country,
            "role": first.role,
            "evidence": {"code": label, "label": EVIDENCE_LABEL_TEXT.get(label, label)},
            "counts": counts,
            "weak": bool(first.weak_reading),
            "integration": {"status": first.integration_status, "integrated": first.integrated,
                            "basis": first.integration_basis},
            "regions": _merged(pf.regions_raw for pf, *_x in rows),
            "region_uncertain": bool(first.region_uncertain),
            "origin_restriction": ({"code": first.origin_restriction,
                                    "label": ORIGIN_RESTRICTION_TEXT}
                                   if first.origin_restriction else None),
            "corp_group": first.corp_group,
            "sites": _dicts(_merged(pf.sites for pf, *_x in rows)),
            "tags": _merged(pf.tags for pf, *_x in rows),
            "row_order": first.row_order,
            "display_rank": _display_rank(label, counts),
        })
    return out


def _reports(db: Session, line_id: int | None,
             primary: str | None) -> tuple[list[dict], list[dict]]:
    """The market reports and playbooks of a published product line, by
    `product_line_id` only: a join row whose key is not a current line is
    never attached to a product."""
    from sqlalchemy import func
    from app.models.market_report import MarketReport, MarketReportLine
    from app.models.strategy import Playbook
    if not line_id:
        return [], []
    slugs = sorted({s for (s,) in db.query(MarketReportLine.slug)
                    .filter(MarketReportLine.product_line_id == line_id)})
    if not slugs:
        return [], []
    counts = dict(db.query(MarketReportLine.slug, func.count(MarketReportLine.product_line_id))
                  .filter(MarketReportLine.slug.in_(slugs)).group_by(MarketReportLine.slug))
    playbooks = {pb.slug: pb for pb in db.query(Playbook).filter(Playbook.slug.in_(slugs))}
    reports = []
    for r in db.query(MarketReport).filter(MarketReport.slug.in_(slugs)):
        reports.append({
            "slug": r.slug, "name": r.name, "family": r.family,
            "old_line_name": r.old_line_name,
            # "YYYY-MM", the same form /intel/reports and the caveat use.
            "as_of": r.as_of.strftime("%Y-%m") if r.as_of else None,
            "in_v1_scope": bool(r.in_v1_scope), "lines_served": counts.get(r.slug, 0),
            "has_playbook": r.slug in playbooks,
        })
    reports.sort(key=lambda r: (r["slug"] != primary, r["name"].casefold()))
    pbs = [{"slug": pb.slug, "name": pb.name, "family": pb.family,
            "report_slug": pb.report_slug}
           for pb in sorted(playbooks.values(), key=lambda pb: (pb.slug != primary, pb.name))]
    return reports, pbs


def _variants(cat: Catalogue, p: Product) -> dict:
    """The VARIANT_OVERRIDES rows. A row's authored `share` (a split between
    the variants) is not served: the screens show no share value."""
    vo = p.catalog_meta.get("variant_overrides") or {}
    rows = []
    for r in vo.get("varRows") or []:
        pid = r.get("id")
        rows.append({
            "pid": pid, "name": r.get("n"), "spec": r.get("s"), "active": r.get("a"),
            "delta": r.get("d"), "base": bool(r.get("base")),
            "exists": pid in cat.products, "is_this": pid == p.code,
        })
    return {
        "source": "VARIANT_OVERRIDES" if vo else None,
        "note": vo.get("note"),
        "names": vo.get("variants") or [],
        "default_index": vo.get("defVar"),
        "rows": rows,
        # Region-level recipe variants (one region priced twice).
        "combo_variants": [{"region": c.region, "variant": c.variant, "combo_id": c.combo_id}
                           for c in p.combos if c.variant],
    }


def _header(p: Product, redirected: dict | None) -> dict:
    """What the detail and the market payloads both start with."""
    return {
        "pid": p.code,
        "name": p.name,
        "redirected_from": redirected,
        "kind": p.kind,
        "status": _status_ref(p.supply_status, describe=True),
        "listed": p.listed,
        "in_default_view": p.default_view,
        "is_group": p.is_group,
    }


def product_detail(db: Session, cat: Catalogue, code: str) -> dict:
    p, redirected = resolve(cat, code)
    blocks = _blocks(db, "formula", [p.code], PUBLIC_FORMULA_BLOCKS).get(p.code, {})
    body = {k: v["body"] for k, v in blocks.items()}

    region = p.default_region()
    combo = p.combo_for(region)
    lines = combo.lines if combo else []

    # Add to portfolio: the template whose recipe prices this card in its
    # default region. That is the card's own template, except for a group
    # card: a group template has no cost lines, so the Cost Model Builder
    # would load an empty recipe from it; use the member that prices the
    # group. Nothing priced → null. The new team product links the template
    # (`formula_template_id`), which gives it its family and product line.
    pricing = cat.products.get(combo.source_pid) if combo else None
    add_to_portfolio = ({
        "template_id": str(pricing.id),
        "default_region": combo.app_region,
        "route": ADD_TO_PORTFOLIO_ROUTE,
        "unit": ADD_TO_PORTFOLIO_UNIT,
    } if pricing is not None else None)

    # Index sources: the default region's lines (the mockup's idxSources).
    keys = sorted({cat.series[l["commodity_id"]].key for l in lines
                   if l["component_type"] == mk.INDEX and l["commodity_id"] in cat.series
                   and cat.series[l["commodity_id"]].key})
    meta = {k: strip_private(v.get("index_source_meta", {}).get("body") or {})
            for k, v in _blocks(db, "index", keys, ("index_source_meta",)).items()}
    index_sources = []
    for l in lines:
        s = cat.series.get(l["commodity_id"]) if l["component_type"] == mk.INDEX else None
        m = meta.get(s.key, {}) if s else {}
        index_sources.append({
            "line": l["label"], "tag": l["tag"], "indexed": s is not None,
            "cost_category": l["cost_category"],
            "series_key": s.key if s else None, "series_name": s.name if s else None,
            "agency": (m.get("agency") or s.agency) if s else None,
            "freq": (m.get("freq") or s.freq) if s else None,
            "proxy": m.get("proxy") if s else None,
        })

    # Where it is bought: the product's placements (a group: its members').
    pids = p.group_members if p.is_group else [p.code]
    seen, placements = set(), []
    for pid in pids:
        for r in cat.placements_by_pid.get(pid, []):
            if r["category_code"] in seen:
                continue
            seen.add(r["category_code"])
            placements.append({**r, "pid": pid})
    fn_counts = Counter(r["fn"] for r in placements if r["fn"])
    functions = [n for n, _c in sorted(fn_counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    industries = sorted({r["industry"] for r in placements})

    synth = strip_private(body.get("synthesis_route"))
    if isinstance(synth, dict) and synth:
        synthesis = {"feedstocks": synth.get("feedstocks") or [], "reaction": synth.get("reaction"),
                     "note": synth.get("note"), "source": "synthesis_route"}
    else:
        feed = [l["label"] for l in lines
                if not _INFRA.search(l["label"]) and not mk.is_margin(l)]
        synthesis = {"feedstocks": feed, "reaction": None, "note": None,
                     "source": "cost_lines" if feed else None}

    compliance = []
    for c in served_compliance(body.get("compliance")) or []:
        if not isinstance(c, dict):
            continue
        bare = bool(c.get("bare"))
        compliance.append({"flag": None if bare else c.get("flag"), "type": c.get("type"),
                           "name": c.get("name"), "desc": c.get("desc"), "bare": bare})

    line = p.line
    reports, playbooks = _reports(db, p.line_id, line["report_slug"] if line else None)
    report = reports[0] if reports else None
    playbook = next((pb for pb in playbooks if report and pb["slug"] == report["slug"]), None)

    absorbed = None
    if p.absorbed_into:
        target = cat.products.get(p.absorbed_into)
        absorbed = {"code": p.absorbed_into, "name": target.name if target else None,
                    "is_group": bool(target and target.is_group),
                    "via": p.catalog_meta.get("absorbed_via"),
                    "exists": target is not None,
                    "listed": bool(target and target.listed)}
    members = []
    if p.is_group:
        for meta_c in p.catalog_meta.get("combos") or []:
            member = cat.products.get(meta_c.get("pid"))
            members.append({"pid": meta_c.get("pid"), "region": meta_c.get("region"),
                            "has_formula": member is not None and not member.is_group
                            and bool(member.combos),
                            "name": member.name if member else None})

    supplier_note = body.get("supplier_note")
    current_events = body.get("current_events")
    taxonomy = _taxonomy(p)
    if line:
        taxonomy["line"] = {"id": line["id"], "name": line["name"], "platform": line["platform"],
                            "flags": line["flags"], "has_report": line["has_report"]}
    return {
        **_header(p, redirected),
        "template_id": str(p.id) if p.id is not None else None,
        "add_to_portfolio": add_to_portfolio,
        "full_name": p.full_name,
        **taxonomy,
        # What the breadcrumb prints for the line: its name, or the fixed
        # text when the card's line is not published.
        "line_label": line["name"] if line else UNPUBLISHED_LINE_TEXT,
        "form": p.form,
        "cas": p.cas,
        "reference_grade": p.reference_grade,
        "volatile": p.volatile,
        "base_period": mk.period_key(mk.BASE_PERIOD),
        "regions": p.regions,
        "region_info": _region_rows(p),
        "default_region": region,
        "has_formula": bool(p.combos),
        "group_members": p.group_members if p.is_group else [],
        "members": members,
        "absorbed_into": p.absorbed_into,
        "absorbed_into_card": absorbed,
        "variants": _variants(cat, p),
        "warnings": warnings_for(p, pricing if pricing is not p else None),
        "functionalities": _dicts(body.get("functionalities")),
        "functions": functions,
        "industries": industries,
        "applications": [
            {"industry": a.get("industry"), "items": a.get("items") or [], "spec": a.get("spec") or []}
            for a in served_applications(body.get("applications")) or [] if isinstance(a, dict)
        ],
        "macro_drivers": _dicts(body.get("macro_drivers")),
        "synthesis": synthesis,
        "cost_formula": {
            "region": region, "variant": combo.variant or None, "form": p.form,
            "lines": [{"label": l["label"], "weight_pct": l["weight"],
                       "cost_category": l["cost_category"],
                       "indexed": l["component_type"] == mk.INDEX,
                       "series_key": (cat.series[l["commodity_id"]].key
                                      if l["commodity_id"] in cat.series else None)}
                      for l in lines if not mk.is_margin(l)],
            "margin_pct": combo.margin_pct,
            "total_weight_pct": round(mk.weight_total(lines), 4),
        } if combo else None,
        "suppliers": suppliers(db, p.code),
        "supplier_note": (clean_supplier_note(supplier_note)
                          if isinstance(supplier_note, str) else None),
        "compliance": compliance,
        "substitution": _dicts(body.get("substitution")),
        "current_events": (clean_current_events(current_events)
                           if isinstance(current_events, str) else None),
        "negotiation_note": (body.get("negotiation_note")
                             if isinstance(body.get("negotiation_note"), str) else None),
        "index_sources": index_sources,
        "index_sources_region": region if combo else None,
        "placements": placements,
        "report": ({"slug": report["slug"], "name": report["name"],
                    "old_line_name": report["old_line_name"]} if report else None),
        "reports": reports,
        "playbook": {"slug": playbook["slug"], "name": playbook["name"]} if playbook else None,
        "playbooks": playbooks,
        "content_provenance": sorted({v["provenance"] for v in blocks.values() if v["provenance"]}),
    }


# ── Market & Costs (tab 1) ───────────────────────────────────────────────────

def _stack(lines: list[dict], cs, tl) -> dict:
    """`stack` is the list of periods; the legend rides beside it."""
    built = mk.stack(lines, cs, tl)
    return {"stack": built["periods"], "stack_lines": built["lines"]}


def _review(combo: Combo) -> tuple[str, str]:
    if combo.reviewed_at is not None:
        return "signed_off", "Signed off"
    return "pending", "Pending review"


def _vintage() -> tuple[str | None, str | None]:
    """The forecast vintage the index loader records (`FORECAST_VINTAGE`):
    ("YYYY-MM", "Month YYYY"). Imported here, not at module load: the
    loader module pulls in write-side dependencies the API does not need."""
    try:
        from app.services.content_drop.indexes import FORECAST_VINTAGE
    except Exception:  # pragma: no cover - the loader package is part of the app
        return None, None
    y, m = FORECAST_VINTAGE
    return mk.period_key((y, m)), f"{mk.MONTH_NAMES[m - 1]} {y}"


def _forecast(cs, tl: mk.Timeline, lines: list[dict]) -> dict:
    """`kind`: `model` (at least one index line has a moving forecast),
    `flat_carry_forward` (every forecast point repeats the last actual) or
    `none` (no forecast months, or no index line with a forecast)."""
    vintage, vintage_label = _vintage()
    has_forecast = bool(tl.forecast) and any(
        k in (mk.FORECAST_MODEL, mk.FORECAST_FLAT) for k in cs.line_forecast)
    if not has_forecast:
        kind, label = mk.FORECAST_NONE, "no forecast"
    elif mk.is_flat_forecast(cs, tl):
        kind, label = mk.FORECAST_FLAT, "flat carry-forward"
    else:
        kind, label = mk.FORECAST_MODEL, "index forecasts"
    indexed_weight = sum(l["weight"] for l in lines if l["component_type"] == mk.INDEX)
    flat_weight = sum(l["weight"] for l, k in zip(lines, cs.line_forecast)
                      if k in (mk.FORECAST_FLAT, mk.FORECAST_NONE))
    return {
        "kind": kind,
        "label": label,
        "vintage": vintage if has_forecast else None,
        "vintage_label": vintage_label if has_forecast else None,
        "flat_weight_pct": (round(100.0 * flat_weight / indexed_weight, 2)
                            if indexed_weight else None),
    }


def _market_base(p: Product, redirected: dict | None, tl: mk.Timeline) -> dict:
    return {
        **_header(p, redirected),
        "base_period": mk.period_key(mk.BASE_PERIOD),
        # The last actual month of the source data. It is not "today": the
        # page labels it "Source data to <month>".
        "data_as_of": mk.period_key(tl.actual[-1]) if tl.actual else None,
        "timeline": {
            "actual_from": mk.period_key(tl.actual[0]) if tl.actual else None,
            "actual_to": mk.period_key(tl.actual[-1]) if tl.actual else None,
            "forecast_from": mk.period_key(tl.forecast[0]) if tl.forecast else None,
            "forecast_to": mk.period_key(tl.forecast[-1]) if tl.forecast else None,
        },
        "regions": p.regions,
        "region_info": _region_rows(p),
    }


def _not_evaluable(base: dict, reason: str, **extra) -> dict:
    return {**base, "evaluable": False, "reason": reason,
            "region": None, "app_region": None, "variant": None,
            "series_by_region": {}, "band": None, "band_by_region": {},
            "flat_forecast": None, "flat_forecast_by_region": {}, "forecast": None,
            "components": [], "stack": [], "stack_lines": [], "dynamics": None,
            "outlook": None, "snapshot": None, "cycle": None, "seasonality": None,
            "volatility": None, "trust": None, "data_gaps": [], **extra}


def product_market(db: Session, cat: Catalogue, code: str, region: str | None = None,
                   variant: str | None = None) -> dict:
    from app.constants.trust import GRADE_CAVEATS, GRADE_UNRATED
    from app.services.index_dossier import active_calibration, percentile_for
    from app.services.intelligence import _seasonal_factors

    p, redirected = resolve(cat, code)
    tl = cat.timeline
    base = _market_base(p, redirected, tl)
    if not p.combos:
        return _not_evaluable({**base, "warnings": warnings_for(p)},
                              "no cost formula for this product yet")

    region = region or p.default_region()
    if region not in p.regions:
        raise NotFound(f"{p.code} is not priced in region {region!r}; regions: "
                       f"{', '.join(p.regions)}")
    combo = p.combo_for(region, variant if variant else None)
    if combo is None:
        raise NotFound(f"{p.code} has no variant {variant!r} in {region}")
    source = cat.products.get(combo.source_pid)
    warnings = warnings_for(p, source if source is not p else None)

    series_by_region, flat_by_region = {}, {}
    cs = None
    for r in p.regions:
        rc = combo if r == region else p.combo_for(r)
        rcs = mk.combo_series(rc.lines, cat.series, tl)
        if rcs is None:
            series_by_region[r] = []
            flat_by_region[r] = None
            continue
        series_by_region[r] = mk.series_points(rcs, tl)
        flat_by_region[r] = mk.is_flat_forecast(rcs, tl)
        if r == region:
            cs = rcs
    # No cone: the demo's band was a heuristic, not a fitted interval (D4).
    band_by_region = {r: None for r in p.regions}
    if cs is None:
        return _not_evaluable(
            {**base, "warnings": warnings}, "the recipe has no weighted lines",
            region=region, app_region=combo.app_region, variant=combo.variant or None,
            series_by_region=series_by_region, band_by_region=band_by_region,
            flat_forecast_by_region=flat_by_region)

    lines = combo.lines
    keys = sorted({cat.series[l["commodity_id"]].key for l in lines
                   if l["component_type"] == mk.INDEX and l["commodity_id"] in cat.series
                   and cat.series[l["commodity_id"]].key})
    narratives = {k: strip_private(v.get("index_narrative", {}).get("body") or {})
                  for k, v in _blocks(db, "index", keys, ("index_narrative",)).items()}
    cids = {l["commodity_id"] for l in lines if l["commodity_id"]}
    factors = _seasonal_factors(db, cids)

    # Volatility on the active calibration ladder, via the existing service.
    d = mk.dispersion(cs, tl)
    calibration = active_calibration(db)
    vol = {"percentile": None, "dispersion": round(d, 4) if d is not None else None,
           "note": None, "months": len(tl.actual), "calibration_id": None,
           "calibration_computed_at": None, "method": None, "reason": None}
    if d is None:
        vol["reason"] = "not enough monthly history to measure dispersion"
    elif calibration is None:
        vol["reason"] = "no active volatility calibration"
    else:
        vol["percentile"] = percentile_for(d, calibration)
        vol["note"] = mk.volatility_note(vol["percentile"], d, len(tl.actual))
        vol["calibration_id"] = str(calibration.id)
        vol["calibration_computed_at"] = calibration.computed_at
        vol["method"] = calibration.method

    blocks = _blocks(db, "formula", [p.code], ("current_events", "macro_drivers")).get(p.code, {})
    ce = blocks.get("current_events")
    outlook = None
    if ce and isinstance(ce.get("body"), str) and ce["body"].strip():
        outlook = {"text": clean_current_events(ce["body"]), "format": "markdown", "region": None,
                   "expires_at": ce["expires_at"], "is_stale": ce["is_stale"],
                   "provenance": ce["provenance"]}
    drivers = (blocks.get("macro_drivers") or {}).get("body")

    status, label = _review(combo)
    last = tl.last_actual
    return {
        **base,
        "warnings": warnings,
        "evaluable": True,
        "reason": None,
        "region": region,
        "app_region": combo.app_region,
        "variant": combo.variant or None,
        "variants": [c.variant for c in p.combos if c.region == region and c.variant],
        "combo_id": combo.combo_id,
        "source_pid": combo.source_pid,
        "series_by_region": series_by_region,
        "band": None,
        "band_by_region": band_by_region,
        "flat_forecast": flat_by_region[region],
        "flat_forecast_by_region": flat_by_region,
        "forecast": _forecast(cs, tl, lines),
        "components": mk.components(lines, cs, cat.series, tl),
        "total_weight_pct": round(cs.weight_sum, 4),
        **_stack(lines, cs, tl),
        "dynamics": mk.dynamics(lines, cs, cat.series, tl, narratives),
        "outlook": outlook,
        "macro_drivers": _dicts(drivers),
        "snapshot": {
            "index_latest": round(cs.levels[last], 2),
            "index_latest_period": mk.period_key(tl.actual[-1]),
            "region": region,
            "cost_lines": len(lines),
            "regions": len(p.regions),
            "review_status": status,
            "review_status_label": label,
        },
        "cycle": mk.cycle(cs, tl),
        "seasonality": mk.seasonality(lines, factors, cs.weight_sum),
        "volatility": vol,
        "trust": {
            "grade": combo.trust_grade,
            "caveat": GRADE_CAVEATS.get(combo.trust_grade or GRADE_UNRATED),
            "needs_review": combo.needs_review,
            "reviewed_at": combo.reviewed_at,
            "review_status": status,
            "review_status_label": label,
            "provenance": combo.provenance,
            "warnings": warnings,
        },
        "data_gaps": cs.gaps,
    }
