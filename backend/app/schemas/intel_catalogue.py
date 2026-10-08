"""Response shapes for the Intelligence catalogue API (design §4.2).

The list, the facets, the status badge, the taxonomy references, the
warnings and the makers panel are typed field by field: a typed model drops
any key it does not declare, so nothing the service did not mean to serve
reaches the client. The rest of the product detail and the Market & Costs
payload are typed at the top level; their nested blocks (editorial bodies
as stored, chart series, narratives) pass through as plain objects and are
documented in `docs/api/intel_catalogue.md`.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


# ── Shared references ────────────────────────────────────────────────────────

class StatusRef(BaseModel):
    """A supply-status badge. It never carries a number."""
    code: str
    label: str
    tone: str


class StatusDetail(StatusRef):
    description: str


class FamilyRef(BaseModel):
    id: int
    name: str | None


class SubfamilyRef(BaseModel):
    id: int
    name: str | None            # one sub-family is deliberately unnamed: skip it


class LineRef(BaseModel):
    id: int
    name: str


class RedirectedFrom(BaseModel):
    pid: str
    kind: str                   # pointer | duplicate


class Warning(BaseModel):
    kind: str                   # pricing_gap | margin_status
    text: str                   # fixed buyer-facing sentence


class DataVersion(BaseModel):
    source_commit: str
    source_date: str | None
    loaded_at: str | None


# ── Facets ───────────────────────────────────────────────────────────────────

class FacetCount(BaseModel):
    name: str
    count: int


class FamilyFacet(BaseModel):
    id: int
    name: str
    count: int


class StatusFacet(StatusRef):
    count: int


class RegionFacet(BaseModel):
    code: str
    app_region: str | None
    name: str
    color: str | None
    count: int


class FacetCounts(BaseModel):
    listed: int                 # every listed card, whatever the status filter
    default_view: int           # listed and live or supply_exception
    products: int               # the cards in the status filter
    lines: int
    families: int
    groups: int
    has_report: int
    regions: int


class FacetsOut(BaseModel):
    status_filter: list[str] | None
    statuses: list[StatusFacet]
    families: list[FamilyFacet]
    industries: list[FacetCount]
    functions: list[FacetCount]
    suppliers: list[FacetCount]
    suppliers_total: int
    regions: list[RegionFacet]
    trends: list[FacetCount]
    counts: FacetCounts
    data_version: DataVersion | None


# ── The grid ─────────────────────────────────────────────────────────────────

class ProductCard(BaseModel):
    pid: str
    name: str
    full_name: str | None
    kind: str
    status: StatusRef | None
    family: FamilyRef | None
    subfamily: SubfamilyRef | None
    line: LineRef | None        # null: "Product line not yet published"
    has_report: bool
    form: str | None
    regions: list[str]
    region_count: int
    is_group: bool
    member_count: int | None
    volatile: bool | None
    current_index: float | None
    trend_pct: float | None
    trend_dir: str | None
    sparkline: list[float]
    sparkline_from: str | None
    sparkline_to: str | None
    sparkline_region: str | None
    top_lines: list[str]


class ProductListOut(BaseModel):
    total: int                  # the filtered count; equals len(items) when no limit is sent
    limit: int | None           # null: no limit was sent, every matching card is here
    offset: int
    items: list[ProductCard]


# ── The product page ─────────────────────────────────────────────────────────

class LineDetail(BaseModel):
    id: int
    name: str
    platform: str | None
    flags: list[str]
    has_report: bool


class AddToPortfolio(BaseModel):
    """What the "Add to portfolio" button needs to start a team cost model
    from the platform template that prices this card (see intel_catalogue.md).
    The team product links the template; the template gives it its family
    and product line."""
    template_id: str            # platform formula_templates.id to link the team product to
    default_region: str         # app region code (regions.code), e.g. "Europe"
    route: str                  # app route to open with state.productId = the new product
    unit: str                   # the new product's unit ("t")


class Evidence(BaseModel):
    code: str
    label: str


class Integration(BaseModel):
    status: str | None
    integrated: bool | None
    basis: str | None


class OriginRestriction(BaseModel):
    code: str
    label: str


class ProducerRef(BaseModel):
    producer_id: str
    name: str
    is_bucket: bool


class Site(BaseModel):
    plant: str | None = None
    town: str | None = None
    country: str | None = None
    region: str | None = None
    scope: str | None = None
    status: str | None = None
    area: str | None = None


class SupplierRow(BaseModel):
    """One authored supplier row of the makers panel. No share, no quote,
    no audit wording."""
    name: str
    canonical: list[str]
    producers: list[ProducerRef]
    is_bucket: bool
    hq: str | None
    role: str | None
    evidence: Evidence
    counts: bool
    weak: bool
    integration: Integration
    regions: list[str]
    region_uncertain: bool
    origin_restriction: OriginRestriction | None
    corp_group: str | None
    sites: list[Site]
    tags: list[Any]
    row_order: int
    display_rank: int


class ProductDetailOut(BaseModel):
    pid: str
    name: str
    redirected_from: RedirectedFrom | None
    kind: str
    status: StatusDetail | None
    listed: bool
    in_default_view: bool
    is_group: bool
    template_id: str | None     # this card's own platform formula_templates.id
    add_to_portfolio: AddToPortfolio | None   # null when no region is priced
    full_name: str | None
    family: FamilyRef | None
    subfamily: SubfamilyRef | None
    line: LineDetail | None
    line_label: str
    form: str | None
    cas: str | None
    reference_grade: str | None
    volatile: bool | None
    base_period: str
    regions: list[str]
    region_info: list[dict[str, Any]]
    default_region: str | None
    has_formula: bool
    group_members: list[str]
    members: list[dict[str, Any]]
    absorbed_into: str | None
    absorbed_into_card: dict[str, Any] | None
    variants: dict[str, Any]
    warnings: list[Warning]
    functionalities: list[dict[str, Any]]
    functions: list[str]
    industries: list[str]
    applications: list[dict[str, Any]]
    macro_drivers: list[dict[str, Any]]
    synthesis: dict[str, Any]
    cost_formula: dict[str, Any] | None
    suppliers: list[SupplierRow]
    supplier_note: str | None
    compliance: list[dict[str, Any]]
    substitution: list[dict[str, Any]]
    current_events: str | None
    negotiation_note: str | None
    index_sources: list[dict[str, Any]]
    index_sources_region: str | None
    placements: list[dict[str, Any]]
    report: dict[str, Any] | None
    reports: list[dict[str, Any]]
    playbook: dict[str, Any] | None
    playbooks: list[dict[str, Any]]
    content_provenance: list[str]


# ── Market & Costs ───────────────────────────────────────────────────────────

class Forecast(BaseModel):
    kind: str                   # model | flat_carry_forward | none
    label: str
    vintage: str | None         # "YYYY-MM" of the forecast's vintage
    vintage_label: str | None   # "September 2026"
    flat_weight_pct: float | None


class MarketOut(BaseModel):
    pid: str
    name: str
    redirected_from: RedirectedFrom | None
    kind: str
    status: StatusDetail | None
    listed: bool
    in_default_view: bool
    is_group: bool
    base_period: str
    data_as_of: str | None      # the last actual month: "Source data to …", never "today"
    timeline: dict[str, Any]
    regions: list[str]
    region_info: list[dict[str, Any]]
    warnings: list[Warning]
    evaluable: bool
    reason: str | None
    region: str | None
    app_region: str | None
    variant: str | None
    variants: list[str] = []
    combo_id: str | None = None
    source_pid: str | None = None
    series_by_region: dict[str, list[dict[str, Any]]]
    band: None = None           # always null: no forecast cone
    band_by_region: dict[str, None]
    flat_forecast: bool | None
    flat_forecast_by_region: dict[str, Any]
    forecast: Forecast | None
    components: list[dict[str, Any]]
    total_weight_pct: float | None = None
    stack: list[dict[str, Any]]
    stack_lines: list[dict[str, Any]]
    dynamics: dict[str, Any] | None
    outlook: dict[str, Any] | None
    macro_drivers: list[dict[str, Any]] = []
    snapshot: dict[str, Any] | None
    cycle: dict[str, Any] | None
    seasonality: dict[str, Any] | None
    volatility: dict[str, Any] | None
    trust: dict[str, Any] | None
    data_gaps: list[dict[str, Any]]
