"""Intelligence reference contract (design §4.2): product lines, reports,
industries and the supplier directory.

Response shapes for `routers/intel_reference.py`. Page builders code against
`docs/api/intel_reference.md`.

Conventions shared by every model here:

* Region codes are the drop's display codes (`EU`, `NA`, `CN`, `IN`, `APAC`,
  `MEA`, `LA`, `GL`), in that order. A supplier's regions are manufacturing
  regions, not sales regions.
* Counts of products are counts of *cards*: listed catalogue templates
  (product or group, with a live formula) on a line that is not hidden.
* No share, no share flag and no supplier count headline appears anywhere
  (design §4.1). Supplier rollups use counting links only.
* A line key that is not a current line is never served: the row carries
  `line_id: null`, `line_key: null` and `line_name: "Product line not yet
  published"`.
"""
from typing import Any

from pydantic import BaseModel


class NameCount(BaseModel):
    name: str
    count: int


class IndustryCount(BaseModel):
    name: str
    slug: str | None = None
    count: int


class Ref(BaseModel):
    id: str
    name: str


class IdName(BaseModel):
    id: int
    name: str


class IdNameOpt(BaseModel):
    """A sub-family: its name may be null (a deliberately unnamed node)."""
    id: int
    name: str | None = None


class SlugName(BaseModel):
    slug: str
    name: str


class Flag(BaseModel):
    code: str
    label: str


class StatusShort(BaseModel):
    code: str
    label: str
    tone: str


class ListedCounts(BaseModel):
    listed: int
    default_view: int


class SubfamilyCount(BaseModel):
    id: int | None = None
    name: str | None = None
    count: int


class FamilySummary(BaseModel):
    id: int
    name: str
    count: int
    subfamilies: list[SubfamilyCount]


# ── Lines ────────────────────────────────────────────────────────────────────

class LineItem(BaseModel):
    id: int
    line_key: str
    name: str
    family: IdName
    subfamily: IdNameOpt | None = None
    platform: str | None = None
    in_v1_scope: bool
    counts: ListedCounts
    # Cards under the status filter, verified first.
    product_count: int
    pids: list[str]
    industries: list[str]
    functions: list[str]
    top_suppliers: list[Ref]
    generic_suppliers: list[str]
    has_report: bool
    report_slug: str | None = None
    reports: list[SlugName]
    playbooks: list[SlugName]
    pending: bool
    flags: list[Flag]


class LineListCounts(BaseModel):
    lines_with_listed: int
    lines_with_products: int


class LineListOut(BaseModel):
    total: int
    counts: LineListCounts
    families: list[FamilySummary]
    industries: list[IndustryCount]
    functions: list[NameCount]
    items: list[LineItem]


class GroupMember(BaseModel):
    pid: str
    name: str
    regions: list[str]


class LineProduct(BaseModel):
    pid: str
    name: str
    full_name: str | None = None
    form: str | None = None
    kind: str
    status: StatusShort | None = None
    regions: list[str]
    is_group: bool
    group_members: list[GroupMember]
    industries: list[str]
    functions: list[str]
    top_suppliers: list[str]
    generic_suppliers: list[str]


class LineReport(BaseModel):
    slug: str
    name: str
    family: str | None = None
    old_line_name: str | None = None
    as_of: str | None = None
    in_v1_scope: bool
    source: str
    word_count: int | None = None
    playbook_slug: str | None = None
    caveat: str
    split_into_n_lines: int


class LineSupplier(Ref):
    products_on_line: int
    hq: str | None = None


class DemandCategory(BaseModel):
    code: str
    name: str
    fn: str | None = None
    status: str
    pids: list[str]


class DemandIndustry(BaseModel):
    industry: str
    slug: str
    categories: list[DemandCategory]


class DemandSummary(BaseModel):
    industry_count: int
    category_count: int
    placed_product_count: int
    industries: list[DemandIndustry]


class LineDetailOut(BaseModel):
    id: int
    line_key: str
    name: str
    family: IdName
    subfamily: IdNameOpt | None = None
    platform: str | None = None
    in_v1_scope: bool
    former_names: list[str]
    counts: ListedCounts
    product_count: int
    pids: list[str]
    industries: list[str]
    functions: list[str]
    top_suppliers: list[Ref]
    suppliers: list[LineSupplier]
    generic_suppliers: list[str]
    has_report: bool
    report_slug: str | None = None
    reports: list[LineReport]
    playbooks: list[SlugName]
    pending: bool
    flags: list[Flag]
    products: list[LineProduct]
    demand: DemandSummary


# ── Reports ──────────────────────────────────────────────────────────────────

class CaveatLine(BaseModel):
    id: int | None = None
    line_key: str | None = None
    name: str
    visible: bool


class Caveat(BaseModel):
    as_of: str | None = None
    text: str
    old_line_name: str
    split_into_n_lines: int
    lines: list[CaveatLine]


class ReportLineOut(BaseModel):
    id: int
    line_key: str
    name: str
    family: str
    source: str
    product_count: int


class ReportSection(BaseModel):
    section_id: str
    ordinal: int
    heading: str | None = None
    html: str
    word_count: int | None = None


class ReportPanel(BaseModel):
    panel: str
    ordinal: int
    heading: str | None = None
    html: str
    data: dict[str, Any] | None = None


class ReportOut(BaseModel):
    slug: str
    name: str
    family: str | None = None
    old_line_name: str | None = None
    as_of: str | None = None
    in_v1_scope: bool
    word_count: int | None = None
    lines: list[ReportLineOut]
    caveat: Caveat
    sections: list[ReportSection]
    panels: list[ReportPanel]
    kraljic: dict[str, Any] | None = None
    playbook: SlugName | None = None


# ── Industries ───────────────────────────────────────────────────────────────

class StatusCounts(BaseModel):
    servable: int
    partial: int
    build: int


class IndustryItem(BaseModel):
    id: int
    name: str
    slug: str
    buyer_one_line: str | None = None
    status: str | None = None
    ratified_on: str | None = None
    scope_status: str | None = None
    category_count: int
    categories_by_status: StatusCounts
    product_count: int
    line_count: int
    out_count: int


class IndustryListOut(BaseModel):
    total: int
    categories_by_status: StatusCounts
    items: list[IndustryItem]


class ProductRef(BaseModel):
    pid: str
    name: str
    form: str | None = None
    is_group: bool
    kind: str
    status: StatusShort | None = None


class CategoryMemberOut(BaseModel):
    line_id: int | None = None
    line_key: str | None = None
    line_name: str
    family: str | None = None
    is_whole_line: bool
    products: list[ProductRef]


class CategorySharedRefOut(BaseModel):
    code: str
    name: str
    fn: str | None = None
    narrowed_to: list[str] | None = None
    lines: list[CategoryMemberOut]


class CategoryOut(BaseModel):
    code: str
    name: str
    alias: str | None = None
    fn: str | None = None
    status: str
    product_count: int
    line_count: int
    members: list[CategoryMemberOut]
    shared: list[CategorySharedRefOut]
    build: list[str]


class SharedObjectOut(BaseModel):
    code: str
    name: str
    fn: str | None = None
    owner: str | None = None
    referenced_by: int


class OutProduct(BaseModel):
    pid: str
    name: str
    listed: bool
    redirect_to: str | None = None


class OutReason(BaseModel):
    code: str
    label: str


class OutTarget(BaseModel):
    name: str
    slug: str | None = None


class OutRow(BaseModel):
    pid: str
    product: OutProduct | None = None
    reason: OutReason | None = None
    to_industries: list[OutTarget]


class ReferenceBuyer(BaseModel):
    buyer_one_line: str | None = None
    buyer: str | None = None
    in_scope: list[Any]
    out_of_scope: list[Any]
    boundaries: list[Any]


class IndustryDetailOut(BaseModel):
    id: int
    name: str
    slug: str
    status: str | None = None
    ratified_on: str | None = None
    scope_status: str | None = None
    category_count: int
    categories_by_status: StatusCounts
    product_count: int
    line_count: int
    out_count: int
    reference_buyer: ReferenceBuyer
    categories: list[CategoryOut]
    shared_objects: list[SharedObjectOut]
    out: list[OutRow]


# ── Suppliers ────────────────────────────────────────────────────────────────

class FamilyCount(BaseModel):
    id: int | None = None
    name: str
    count: int


class SupplierItem(BaseModel):
    id: str
    name: str
    hq: str | None = None
    # Cards this producer has a counting link on ("products tracked here").
    product_count: int
    family_count: int
    families: list[FamilyCount]
    industry_count: int
    industries: list[IndustryCount]
    region_count: int
    regions: list[str]
    line_count: int
    integrated_count: int


class GenericSupplier(BaseModel):
    id: str
    name: str
    product_count: int


class SupplierListOut(BaseModel):
    total: int
    limit: int
    offset: int
    families: list[FamilyCount]
    industries: list[IndustryCount]
    generic_suppliers: list[GenericSupplier]
    items: list[SupplierItem]


class LineRefOut(BaseModel):
    id: int
    line_key: str
    name: str


class Evidence(BaseModel):
    code: str
    label: str


class Integration(BaseModel):
    status: str | None = None
    integrated: bool | None = None
    basis: str | None = None


class OriginRestriction(BaseModel):
    code: str
    label: str


class SupplierProduct(BaseModel):
    pid: str
    name: str
    kind: str
    status: StatusShort | None = None
    family: IdName | None = None
    line: LineRefOut | None = None
    is_group: bool
    role: str | None = None
    evidence: Evidence
    # Counts toward the card's supplier floor (Laurent's predicate).
    counts: bool
    weak: bool
    integration: Integration
    regions: list[str]
    region_uncertain: bool
    origin_restriction: OriginRestriction | None = None
    corp_group: str | None = None
    tags: list[Any]
    sites: list[dict[str, Any]]
    raw_name: str | None = None


class Competitor(BaseModel):
    id: str
    name: str
    shared_products: int
    shared_lines: int
    shared_pct: int
    product_count: int


class DossierRole(BaseModel):
    commodity_id: int
    series_key: str | None = None
    index_name: str
    region: str | None = None
    role: str
    location: str | None = None


class RegionCount(BaseModel):
    region: str
    count: int


class SupplierDetailOut(BaseModel):
    id: str
    name: str
    hq: str | None = None
    is_bucket: bool
    in_directory: bool
    aliases: list[str]
    product_count: int
    family_count: int = 0
    families: list[FamilyCount] = []
    industry_count: int = 0
    industries: list[IndustryCount] = []
    functions: list[NameCount]
    region_count: int = 0
    regions: list[str] = []
    region_breakdown: list[RegionCount]
    line_count: int = 0
    integrated_count: int = 0
    products: list[SupplierProduct]
    unlisted_codes: list[str]
    competitors: list[Competitor]
    competitor_count: int
    dossier_roles: list[DossierRole]
