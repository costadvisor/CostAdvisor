"""Intelligence derived-payload contract (Wave 3, SCRUM-75 / INT-1).

The derived half of the ID card. The other half — composed editorial + dimensions
— is SCRUM-76's `GET /formulas/{code}/intelligence`; the card is two calls by
design and neither is a parallel copy of the other.

Every block that can be absent carries its own reason rather than a bare null:
a combo with no lines, no anchor or no priceable lines is a real thing in the
catalogue and will be requested.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SeriesPoint(BaseModel):
    year: int
    quarter: int
    # Base 100 at the combo's base period, by construction — the level rebases
    # on the recipe's own weight sum, margin line included.
    level: float


class ComponentOut(BaseModel):
    name: str
    component_type: str | None = None
    commodity_id: int | None = None
    commodity_key: str | None = None
    weight_pct: float
    is_proxy: bool | None = None
    depth: int | None = None
    line_region: str | None = None
    base_value: float | None = None
    current_value: float | None = None
    ratio: float
    # False where the line rode flat because no index value was found.
    has_data: bool
    # Which store the values came from: `index_values` (what the costing engine
    # reads) or `index_monthly_values` (the drop's series, which it cannot).
    value_source: str | None = None
    contribution_pct: float
    # Money only where the combo carries a base-price anchor; the series is a
    # level, not a price.
    contribution_abs: float | None = None


class ChangeOut(BaseModel):
    short_window_quarters: int
    long_window_quarters: int
    short_pct: float | None = None
    long_pct: float | None = None


class CycleOut(BaseModel):
    window_quarters: int
    # Generated from the same constant as the verdict — the mismatch this
    # prevents is a percentile computed over one window and labelled with
    # another.
    window_label: str
    periods_used: int
    low: float | None = None
    high: float | None = None
    spread: float
    percentile: float | None = None
    # near_the_top | mid_range | near_the_bottom | flat
    verdict: str
    sentence: str


class SeasonalityOut(BaseModel):
    # Twelve values, January first.
    factors: list[float]
    peak_month: int
    trough_month: int
    spread: float
    # How much of the recipe actually carries a seasonal profile. The rest
    # contributes flat and damps the amplitude — that damping is the signal.
    seasonal_weight_pct: float
    source: str


class VolatilityOut(BaseModel):
    dispersion: float | None = None
    percentile: int | None = None
    # Which ladder produced the number; the engine reads DB-7's, never its own.
    calibration_id: uuid.UUID | None = None
    calibration_computed_at: datetime | None = None
    method: str | None = None
    monthly_weight_pct: float = 0.0
    reason: str | None = None


class TrustOut(BaseModel):
    # Read from SCRUM-78's stored field, never recomputed here.
    grade: str | None = None
    # Shipped with the grade rather than written by each screen — the state and
    # the customer-facing claim are the same thing.
    caveat: str | None = None
    needs_review: bool = False
    reviewed_at: datetime | None = None
    inputs: dict = {}
    # Echoed rather than chosen: the two proxy-status columns disagree, and
    # SCRUM-78 canonicalises one.
    proxy_status_source: str | None = None
    source: str = ""
    # Named separately with a stated relationship — `coverage_tier` already
    # holds a third vocabulary, `data_confidence` a fourth axis.
    coverage_tier: str | None = None
    proxy_density_tier: str | None = None


class DataGap(BaseModel):
    line: str
    commodity_id: int | None = None
    reason: str


class IntelligenceOut(BaseModel):
    template_id: uuid.UUID
    template_code: str | None = None
    region_requested: str
    coverage_region: str | None = None
    evaluable: bool
    # Stated whenever something is missing — a combo with no lines or no anchor
    # is a valid response, not a 500.
    reason: str | None = None
    base_price: float | None = None
    currency: str | None = None
    base_year: int | None = None
    base_quarter: int | None = None
    series: list[SeriesPoint] = []
    components: list[ComponentOut] = []
    change: ChangeOut | None = None
    cycle: CycleOut | None = None
    seasonality: SeasonalityOut | None = None
    volatility: VolatilityOut | None = None
    trust: TrustOut | None = None
    data_gaps: list[DataGap] = []
    # Which store the levels came from, and whether that matches what the
    # costing engine reads. Stated rather than hidden — this engine can see the
    # drop's monthly series and `data_resolver` cannot.
    value_sources: dict = {}
    # Where a product resolved from, when this was reached that way.
    resolved_via: str | None = None


class ComboRequest(BaseModel):
    template_id: uuid.UUID
    region: str = Field(min_length=1, max_length=20)


class BatchRequest(BaseModel):
    # The library renders a page of tiles at once; one request per tile is what
    # does not scale to the platform catalogue.
    combos: list[ComboRequest] = Field(min_length=1, max_length=50)


class BatchOut(BaseModel):
    count: int
    results: list[IntelligenceOut] = []
