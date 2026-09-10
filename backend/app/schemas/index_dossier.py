"""Index dossier + volatility calibration API contract (Wave 3, DB-7).

A volatility reading always names the calibration it was read from — SCRUM-75's
own acceptance criterion is that it reports which calibration it used, and it
cannot do that if the number arrives on its own.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DriverOut(BaseModel):
    category: str | None = None
    provider: str | None = None
    correlation: float | None = None
    # The raw string is authoritative; the parsed bounds exist so a caller can
    # sort and threshold, and are null rather than guessed when it won't parse.
    lag_raw: str | None = None
    lag_days_min: int | None = None
    lag_days_max: int | None = None
    signal_raw: str | None = None
    signal_strength: str | None = None
    move_raw: str | None = None
    move_up: bool | None = None


class ChainNodeOut(BaseModel):
    position: int
    node_type: str
    label: str
    detail: str | None = None


class FlagOut(BaseModel):
    flag_kind: str
    severity: str | None = None
    label: str
    detail: str | None = None


class SplitOut(BaseModel):
    split_type: str
    label: str
    pct: float | None = None
    note: str | None = None


class ProducerRoleOut(BaseModel):
    producer_id: str
    producer_name: str | None = None
    role: str
    # Null whenever the share was not disclosed — never a real zero.
    share_pct: float | None = None
    share_disclosed: bool
    location: str | None = None
    regions_raw: list | None = None
    tags: list | None = None
    raw_name: str | None = None


class PointerOut(BaseModel):
    title: str
    body: str | None = None


class DossierOut(BaseModel):
    commodity_id: int
    commodity_key: str | None = None
    region: str | None = None
    # "region" when a region-specific dossier supplied it, "series" when the
    # series-wide one did.
    resolved_from: str
    header: dict = {}
    drivers: list[DriverOut] = []
    chain: list[ChainNodeOut] = []
    flags: list[FlagOut] = []
    splits: list[SplitOut] = []
    producer_roles: list[ProducerRoleOut] = []
    pointers: list[PointerOut] = []
    # Stated on every dossier read so a consumer never waits for a cycle or
    # volatility field that is deliberately not stored here.
    computed_elsewhere: list[str] = [
        "current_value", "change_pct", "cycle_position", "seasonality",
        "volatility_percentile",
    ]


class BreakpointOut(BaseModel):
    rung: int
    dispersion: float


class CalibrationOut(BaseModel):
    id: uuid.UUID
    method: str
    n_rungs: int
    n_series: int
    min_points: int
    is_active: bool
    # Percentile points per rung, derived from the ladder's own length. The
    # mockup hardcodes 5, which is only right at 21 rungs.
    step: float
    note: str | None = None
    computed_at: datetime
    breakpoints: list[BreakpointOut] = []


class RecomputeRequest(BaseModel):
    n_rungs: int = Field(default=21, ge=2, le=101)
    min_points: int = Field(default=13, ge=3, le=240)
    note: str | None = None


class VolatilityOut(BaseModel):
    commodity_id: int
    dispersion: float | None = None
    percentile: int | None = None
    # Which calibration produced the number.
    calibration_id: uuid.UUID | None = None
    calibration_computed_at: datetime | None = None
    method: str | None = None
    n_series: int | None = None
    # Set instead of returning a bare null: "not measurable" is not "calm".
    reason: str | None = None
