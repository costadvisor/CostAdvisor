import uuid
from datetime import datetime
from pydantic import BaseModel


class ShouldCostRequest(BaseModel):
    cost_model_id: uuid.UUID
    target_year: int | None = None
    target_quarter: int | None = None
    display_currency: str | None = None
    display_unit: str | None = None
    normalize_to_incoterm: str | None = None


class ShouldCostResult(BaseModel):
    should_cost: float
    cost_before_margin: float
    margin_amount: float
    rm_cost: float
    ovc_cost: float
    per_active_unit: float | None
    currency: str
    unit: str
    incoterm: str | None = None
    normalized_to_incoterm: str | None = None
    # Scrum 28b — a component that should be index-linked but isn't (broken
    # name match, or a tracking-mode link that fell back to its last-known
    # snapshot) surfaces here instead of silently riding flat at ratio 1.0.
    data_gaps: list["DataGap"] = []


# ── Should-cost breakdown (Scrum 17 — inspectable numbers) ─────────────────────

class ComponentBreakdown(BaseModel):
    label: str
    commodity_id: int | None
    commodity_name: str | None
    weight_pct: float          # component weight, as a percent (0-100)
    base_value: float | None   # index value at the formula's base period
    current_value: float | None  # index value at the target period
    ratio: float               # current_value / base_value, or 1.0 if riding flat
    contribution: float        # comp_base * weight * ratio — sums exactly to cost_before_margin
    source: str | None         # "composite"|"fixed"|"team_override"|"scraped_*"|None
    base_period: str           # e.g. "Q1 2024"
    current_period: str
    has_data: bool             # False if either base_value or current_value is missing
    # Scrum 28b — provenance, mirrors GET /formulas/{id}/resolve's per-line
    # shape exactly (populated from the live catalog recipe in tracking mode,
    # from the frozen snapshot in pinned mode). None for hand-built lines.
    component_type: str | None = None       # "index" | "fixed" | None
    depth: int | None = None
    via_template_id: uuid.UUID | None = None
    via_template_name: str | None = None
    line_region: str | None = None
    is_proxy: bool | None = None


class ShouldCostBreakdown(BaseModel):
    should_cost: float
    cost_before_margin: float
    margin_amount: float
    margin_type: str
    components: list[ComponentBreakdown]
    data_gaps: list["DataGap"]
    incoterm_adjustment: float | None = None   # should_cost(after) - should_cost(before normalization)
    fx_rate_used: float | None = None          # target_ccy per 1 unit of model currency, if display_currency requested
    unit_factor_used: float | None = None      # conversion factor applied, if display_unit requested
    currency: str
    unit: str
    incoterm: str | None = None
    normalized_to_incoterm: str | None = None


# ── Forward should-cost (Scrum 70 Part 2) ──────────────────────────────────

class ForwardShouldCostResult(BaseModel):
    """A should-cost evaluated `horizon_quarters` ahead of today, using Scrum 70
    Part 1's projected index values instead of scraped ones. `insufficient=True`
    means no forecast should-cost was produced — never a fabricated number."""
    insufficient: bool
    forecast_should_cost: float | None = None
    forecast_vintage: datetime | None = None
    forecast_method: str | None = None
    horizon_year: int
    horizon_quarter: int
    data_gaps: list["DataGap"] = []


class EvolutionRequest(BaseModel):
    cost_model_id: uuid.UUID
    reference_year: int | None = None
    reference_quarter: int | None = None
    from_year: int | None = None
    from_quarter: int | None = None
    to_year: int | None = None
    to_quarter: int | None = None
    granularity: str = "quarterly"  # 'quarterly' or 'monthly'
    formula_mode: str = "active"  # 'active' or 'versioned'
    display_currency: str | None = None
    display_unit: str | None = None
    normalize_to_incoterm: str | None = None


class EvolutionPeriod(BaseModel):
    period: str  # 'Q1-23' or 'Jan-24'
    year: int
    quarter: int
    month: int | None = None  # set for monthly granularity
    theoretical: float
    actual: float | None
    gap: float | None
    gap_pct: float | None
    component_costs: dict[str, float] | None = None  # label -> cost for that period


class ComponentInfo(BaseModel):
    label: str
    commodity_name: str | None


class DataGap(BaseModel):
    component_label: str
    period: str  # e.g. 'Q1-23'
    reason: str  # e.g. 'no index value found'


class EvolutionResult(BaseModel):
    product_name: str
    supplier_name: str | None
    reference_cost: float
    region: str
    currency: str
    unit: str
    incoterm: str | None = None
    named_place: str | None = None
    normalized_to_incoterm: str | None = None
    periods: list[EvolutionPeriod]
    components: list[ComponentInfo] = []
    available_from_year: int | None = None
    available_from_quarter: int | None = None
    available_to_year: int | None = None
    available_to_quarter: int | None = None
    data_gaps: list[DataGap] = []


class SqueezeRequest(BaseModel):
    cost_model_id: uuid.UUID
    reference_year: int | None = None
    reference_quarter: int | None = None
    from_year: int | None = None
    from_quarter: int | None = None
    to_year: int | None = None
    to_quarter: int | None = None
    granularity: str = "quarterly"
    include_margin: bool = True
    volume_projection: str = "flat"  # 'flat' or 'seasonal'
    display_currency: str | None = None
    display_unit: str | None = None


class SqueezePeriod(BaseModel):
    period: str
    year: int
    quarter: int
    month: int | None = None
    theoretical: float
    actual: float | None
    gap: float | None
    gap_pct: float | None
    volume: float | None
    volume_projected: bool = False
    impact: float | None  # gap * volume
    cumulative_impact: float


class SqueezeResult(BaseModel):
    product_name: str
    supplier_name: str | None
    reference_cost: float
    region: str
    currency: str
    unit: str
    periods: list[SqueezePeriod]
    cumulative_impact: float
    total_volume: float


class BriefRequest(BaseModel):
    cost_model_id: uuid.UUID
    from_year: int | None = None
    from_quarter: int | None = None
    to_year: int | None = None
    to_quarter: int | None = None
    display_currency: str | None = None
    display_unit: str | None = None


class BriefDriver(BaseModel):
    component_label: str
    index_name: str | None
    index_change_pct: float
    contribution_to_gap: float
    component_cost: float  # absolute cost contribution to theoretical price
    direction: str  # 'up', 'down', 'flat'


class BriefResult(BaseModel):
    product_name: str
    supplier_name: str | None
    destination_country: str | None
    currency: str
    unit: str
    current_should_cost: float
    current_actual_price: float | None
    gap: float | None
    gap_pct: float | None
    total_impact: float | None
    volumes_missing: bool = False
    period_label: str
    evolution: list[EvolutionPeriod]
    narrative: str
    drivers: list[BriefDriver]
    data_gaps: list[DataGap] = []


# ── Price Change Analysis ─────────────────────────────────────

class PriceChangeRequest(BaseModel):
    cost_model_id: uuid.UUID
    from_year: int
    from_quarter: int
    to_year: int
    to_quarter: int


class PriceChangeComponent(BaseModel):
    label: str
    index_name: str | None
    weight: float
    index_start: float | None
    index_end: float | None
    index_change_pct: float
    contribution_pct: float  # weight * index_change_pct


class PriceChangeResult(BaseModel):
    product_name: str
    supplier_name: str | None
    currency: str
    unit: str
    base_price: float
    from_label: str
    to_label: str
    fair_change_pct: float
    fair_new_price: float
    margin_weight: float
    components: list[PriceChangeComponent]
