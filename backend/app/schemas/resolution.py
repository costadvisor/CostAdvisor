"""Response shapes for the resolution + concentration API (SCRUM-74).

Every answer carries its provenance — the resolution state, which of the two
proxy readings it is, and the weight behind each hop — because the two known
consumers need exactly that: SCRUM-80 ranks a swap backlog off it and SCRUM-71
rolls exposure up through it. A bare list of names could not be audited by the
person who has to act on it.
"""
import uuid

from pydantic import BaseModel


# ── Shared fragments ─────────────────────────────────────────────────────────

class SeriesRef(BaseModel):
    commodity_id: int
    commodity_key: str | None = None
    value_kind: str | None = None
    base_period: str | None = None
    agency: str | None = None
    unit: str | None = None


class CardRef(BaseModel):
    feed_key: str
    feed_slug: str | None = None
    region: str | None = None
    region_label: str | None = None
    is_default_region: bool | None = None


class SeriesHistory(BaseModel):
    actual_points: int = 0
    forecast_points: int = 0
    first_year: int | None = None
    last_year: int | None = None


# ── Q1: the chain for one type code ──────────────────────────────────────────

class TypeCodeChainOut(BaseModel):
    code: str
    label: str | None = None
    resolution: str                      # resolved | no_series | ambiguous
    proxy_status: str | None = None
    # Names WHICH of the two disagreeing readings this is, rather than
    # implying there is only one.
    proxy_status_source: str
    swap_priority: str | None = None     # A | B | C — a sourcing rank, not accuracy
    ideal_index: str | None = None       # prose; the series we would rather have
    registry_note: str | None = None
    series: SeriesRef | None = None      # absent when resolution is ambiguous
    cards: list[CardRef] = []            # several cards may display one series
    history: SeriesHistory
    priceable: bool
    blocker: str | None = None


# ── Q2 + Q4: the dependents of one series ────────────────────────────────────

class DependentTypeCode(BaseModel):
    code: str
    label: str | None = None
    resolution: str
    proxy_status: str | None = None
    swap_priority: str | None = None
    source_total_weight: float
    weight_share_of_series_pct: float | None = None


class SeriesTotals(BaseModel):
    type_code_count: int
    source_total_weight: float
    weight_share_of_library_pct: float | None = None


class AffectedCatalogLine(BaseModel):
    formula_code: str | None = None
    formula_name: str | None = None
    region: str | None = None
    lines: int


class SeriesDependentsOut(BaseModel):
    commodity_id: int
    commodity_key: str | None = None
    value_kind: str | None = None
    base_period: str | None = None
    agency: str | None = None
    cards: list[CardRef] = []
    type_codes: list[DependentTypeCode] = []
    totals: SeriesTotals
    # The blast radius if this series is withdrawn or re-sourced. Empty until
    # the catalog retarget populates the type-code link on cost lines.
    affected_catalog_lines: list[AffectedCatalogLine] = []


# ── The library-wide view ────────────────────────────────────────────────────

class ConcentrationEntry(BaseModel):
    commodity_id: int
    commodity_key: str | None = None
    type_code_count: int
    source_total_weight: float
    weight_share_of_library_pct: float | None = None


class ConcentrationOut(BaseModel):
    library_total_weight: float
    series: list[ConcentrationEntry] = []


class BlockerGroup(BaseModel):
    code_count: int
    source_total_weight: float
    weight_share_of_library_pct: float | None = None
    codes: list[dict] = []


class UnpriceableOut(BaseModel):
    library_total_weight: float
    # Keyed by reason — never one combined count, because the three need three
    # different actions: buy a feed, decide what a code means, run a scrape.
    blockers: dict[str, BlockerGroup]


# ── Q3: why can't this combo be costed ───────────────────────────────────────

class LineBlockerOut(BaseModel):
    line_name: str
    region: str | None = None
    weight_pct: float | None = None
    type_code: str | None = None
    reason: str
    detail: str
    ideal_index: str | None = None


class ComboDiagnosisOut(BaseModel):
    template_id: uuid.UUID
    template_code: str | None = None
    region: str
    coverage_exists: bool
    priceable: bool
    reason: str | None = None
    blocking_lines: list[LineBlockerOut] = []
    blocked_weight_pct: float = 0.0
    total_lines: int = 0
    # Distinguishes "nothing wrong" from "nothing linked yet".
    type_coded_lines: int = 0


# ── Derivation + provenance (SCRUM-80 / FD-1) ────────────────────────────────

class DerivationOut(BaseModel):
    """How a derived number was produced. Present only when `derived` is true,
    so a consumer can never mistake a computed value for an observed one."""

    base_series: str
    base_series_id: int
    operation: str
    spread: float | None = None
    spread_unit: str | None = None
    expression: str
    base_value: float
    # A derivation is only as fresh as the base it stands on.
    base_status: str
    recalibration: str | None = None
    note: str | None = None


class ValueProvenanceOut(BaseModel):
    value: float | None = None
    status: str                        # current | stale | absent
    observed_year: int | None = None
    observed_quarter: int | None = None
    # 0 when current. Non-zero means the number was carried forward, which the
    # costing path does silently and this read refuses to.
    quarters_stale: int | None = None
    kind: str | None = None           # actual | forecast
    derived: bool = False
    derivation: DerivationOut | None = None
    reason: str | None = None


class TypeCodeValueOut(BaseModel):
    code: str
    resolution: str
    resolvable: bool
    value: float | None = None
    provenance: ValueProvenanceOut | None = None
    # Named even when unresolvable: "we wanted this series and cannot have it"
    # is actionable, a bare null is not.
    wanted_series: str | None = None
    ideal_index: str | None = None
    swap_priority: str | None = None
    unresolvable_reason: str | None = None


class BacklogEntryOut(BaseModel):
    code: str
    label: str | None = None
    # A sourcing rank, not an accuracy score: A = a better index exists and
    # buying it improves the number overnight; B = defensible upstream
    # stand-in; C = permanent by design.
    swap_priority: str | None = None
    resolution: str
    proxy_status: str | None = None
    ideal_index: str | None = None
    # Live catalog weight, index lines only — margin sits inside the 100% total
    # and would inflate the denominator.
    catalog_weight: float
    line_count: int
    priceable: bool


class SwapBacklogOut(BaseModel):
    total_catalog_weight: float
    entries: list[BacklogEntryOut] = []
