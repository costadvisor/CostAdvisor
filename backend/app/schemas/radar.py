"""Negotiation window + market signal API contract (Wave 3, SCRUM-79 / MON-1).

The single-window payload is specified by the ticket: the driver, the evidence
values, the threshold applied *and its unit*, the cost line -> type code ->
series resolution path with proxy state, and open/close with `close_basis`.
Everything needed to explain a window without re-running the radar.
"""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class WindowProductOut(BaseModel):
    cost_model_id: uuid.UUID
    product: str | None = None
    exposure_pct: float | None = None
    # Read from the type-code side of the resolution layer only.
    via_proxy: bool | None = None


class WindowOut(BaseModel):
    id: uuid.UUID
    driver: str
    driver_key: str
    scope_type: str
    scope_supplier_id: int | None = None
    scope_contract_id: uuid.UUID | None = None
    scope_cost_model_id: uuid.UUID | None = None
    scope_commodity_id: int | None = None
    headline: str
    opens_on: date
    closes_on: date | None = None
    # `unknown` is a real answer — a forward-looking close needs forecast
    # storage, and a synthesised date would be worse than admitting that.
    close_basis: str
    state: str
    # covered | partial | unknown. Tri-valued so a blind spot cannot read as calm.
    coverage: str
    threshold_value: float | None = None
    threshold_unit: str | None = None
    products: list[WindowProductOut] = []
    opened_at: datetime
    closes_in_days: int | None = None


class WindowDetailOut(WindowOut):
    evidence: dict | None = None
    # The team's own flag, alongside what the window suggests. The radar never
    # writes it — see `trigger_radar.SUGGESTS_NOT_SETS`.
    suggested_negotiation_state: str | None = None
    current_negotiation_states: dict[str, str] = {}


class RadarRunOut(BaseModel):
    opened: int
    refreshed: int
    closed: int
    windows_open: int


class ModelCoverageOut(BaseModel):
    cost_model_id: uuid.UUID
    product: str | None = None
    coverage: str
    unresolved_type_codes: list[str] = []
    fallback_reason: str | None = None
    resolved_lines: int = 0
    total_index_lines: int = 0


class CoverageReportOut(BaseModel):
    # Reported even when no window opened: telling a buyer "no signal" on a
    # product whose biggest cost line has never had a price is the failure mode
    # this endpoint exists to prevent.
    models: list[ModelCoverageOut] = []
    counts: dict[str, int] = {}


class SignalIn(BaseModel):
    signal_type: str
    headline: str = Field(min_length=1)
    body: str | None = None
    supplier_id: int | None = None
    commodity_id: int | None = None
    region: str | None = None
    as_of_date: date
    expires_at: date | None = None
    source_url: str | None = None
    # Platform-scoped (visible to every team) requires super-admin; a team
    # analyst creates a team-scoped signal.
    platform: bool = False


class SignalOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID | None = None
    origin: str
    signal_type: str
    headline: str
    body: str | None = None
    supplier_id: int | None = None
    commodity_id: int | None = None
    region: str | None = None
    as_of_date: date
    expires_at: date | None = None
    as_of_inferred: bool
    source_url: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
