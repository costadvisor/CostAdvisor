"""Response shapes for the refresh monitor API (Jira 209)."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel


class RefreshRouteOut(BaseModel):
    commodity_id: int
    name: str
    commodity_key: str | None = None
    route: str            # content_drop | scraper | proxy | manual_upload | composite | none
    source: str | None = None
    detail: str
    frequency: str | None = None
    expected_interval_months: int | None = None
    team_sources: int = 0


class RefreshRoutesOut(BaseModel):
    counts: dict[str, int]       # every route, zero included
    indexes: list[RefreshRouteOut]


class RefreshStatusOut(RefreshRouteOut):
    last_attempt_at: datetime | None = None
    last_outcome: str | None = None      # ok | no_data | rejected | failed; None = never attempted
    last_success_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    latest_period: str | None = None


class RefreshStatusListOut(BaseModel):
    failing: int                 # indexes whose last attempt did not succeed
    never_attempted: int         # indexes on a refreshable route with no attempt on record
    indexes: list[RefreshStatusOut]


class RefreshAttemptOut(BaseModel):
    commodity_id: int | None = None
    team_source_id: int | None = None
    label: str
    route: str
    outcome: str
    rows_written: int
    latest_period: str | None = None
    tries: int
    error: str | None = None
    finished_at: datetime


class RefreshRunOut(BaseModel):
    id: uuid.UUID
    task: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    attempted: int
    succeeded: int
    no_data: int
    rejected: int
    failed: int
    note: str | None = None      # the daily check's summary (Jira 209 S5)
    attempts: list[RefreshAttemptOut] = []


class RefreshOverviewRowOut(BaseModel):
    commodity_id: int
    name: str
    route: str
    source: str | None = None
    detail: str
    frequency: str | None = None
    state: str                   # series health state (current | stale | empty | ...)
    reason: str
    last_actual: str | None = None
    months_old: int | None = None
    stale_after_months: int
    stale_rule: str
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_outcome: str | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    flags: list[str] = []        # late | failing | no_route | never_attempted


class RefreshAlertOut(BaseModel):
    kind: str                    # failing | late | content_drop_late
    message: str
    commodity_ids: list[int]


class RefreshCheckOut(BaseModel):
    at: datetime
    summary: str | None = None


class RefreshOverviewOut(BaseModel):
    as_of: date
    counts: dict[str, int]       # indexes, failing, late, no_route, never_attempted, alerts
    alerts: list[RefreshAlertOut]
    last_check: RefreshCheckOut | None = None   # the latest daily check, if one has run
    indexes: list[RefreshOverviewRowOut]
