"""Refresh overview and alerts: one row per index, and what staff must act on
(Jira 209 S5).

Joins the three answers built earlier in 209:

    route       how the index gets new values          (S1, refresh_route)
    state       what its last refresh did              (S2, index_refresh_state)
    freshness   whether its data is late for its kind  (S4, series_health)

and turns them into alerts. Alerts are computed, not stored: the overview
endpoint shows them live, and the daily check (`tasks/refresh_check`) reports
them to the worker log and Sentry. No email for now (agreed, Jira 209).

Two kinds of alert:

    failing   a scheduled refresh has failed FAILURES_BEFORE_ALERT runs in a
              row (failed, rejected or no data — see refresh_log.record)
    late      the data is older than its threshold (S4). Late content-drop
              series are grouped into ONE alert, because the fix is one
              action — re-extract and re-load the drop — not one per series.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models.index_refresh import IndexRefreshState
from app.services.refresh_route import CONTENT_DROP, NONE, SCRAPER, refresh_routes
from app.services.series_health import STALE, series_health_bulk

# PROPOSED (Jira 209 S5). A single failure is often a source's bad day and the
# next weekly run fixes it; two in a row is a broken source.
FAILURES_BEFORE_ALERT = 2

# How many names an alert lists before "and N more".
_NAMES_SHOWN = 10

FAILING = "failing"
LATE = "late"
CONTENT_DROP_LATE = "content_drop_late"


@dataclass
class RefreshOverviewRow:
    commodity_id: int
    name: str
    route: str
    source: str | None
    detail: str
    frequency: str | None
    # freshness (S4)
    state: str
    reason: str
    last_actual: str | None
    months_old: int | None
    stale_after_months: int
    stale_rule: str
    # last refresh (S2)
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_outcome: str | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    # what is wrong, for filtering: late | failing | no_route | never_attempted
    flags: list[str] = field(default_factory=list)


@dataclass
class RefreshAlert:
    kind: str                     # failing | late | content_drop_late
    message: str
    commodity_ids: list[int]


def refresh_overview(db: Session, *, as_of: date | None = None) -> list[RefreshOverviewRow]:
    """Every index, problems first (failing, then late, then the rest), by name."""
    health = {h.commodity_id: h for h in series_health_bulk(db, as_of=as_of)}
    states = {s.commodity_id: s for s in db.query(IndexRefreshState)}

    rows = []
    for r in refresh_routes(db):
        h = health[r.commodity_id]
        s = states.get(r.commodity_id)
        row = RefreshOverviewRow(
            commodity_id=r.commodity_id, name=r.name, route=r.route, source=r.source,
            detail=r.detail, frequency=r.frequency,
            state=h.state, reason=h.reason, last_actual=h.last_actual, months_old=h.months_old,
            stale_after_months=h.stale_after_months, stale_rule=h.stale_rule,
        )
        if s is not None:
            row.last_attempt_at = s.last_attempt_at
            row.last_success_at = s.last_success_at
            row.last_outcome = s.last_outcome
            row.last_error = s.last_error
            row.consecutive_failures = s.consecutive_failures or 0
        if row.consecutive_failures >= FAILURES_BEFORE_ALERT:
            row.flags.append(FAILING)
        if row.state == STALE:
            row.flags.append(LATE)
        if row.route == NONE:
            row.flags.append("no_route")
        if row.route == SCRAPER and s is None:
            row.flags.append("never_attempted")
        rows.append(row)

    rows.sort(key=lambda x: (FAILING not in x.flags, LATE not in x.flags, x.name.lower()))
    return rows


def _names(rows: list[RefreshOverviewRow]) -> str:
    shown = ", ".join(r.name for r in rows[:_NAMES_SHOWN])
    more = len(rows) - _NAMES_SHOWN
    return shown + (f" and {more} more" if more > 0 else "")


def build_alerts(rows: list[RefreshOverviewRow]) -> list[RefreshAlert]:
    """Failing first (most consecutive failures first), then late."""
    alerts: list[RefreshAlert] = []

    failing = sorted((r for r in rows if FAILING in r.flags), key=lambda r: -r.consecutive_failures)
    for r in failing:
        text = r.last_error or r.last_outcome or ""
        first_line = text.splitlines()[0] if text else ""
        alerts.append(RefreshAlert(
            kind=FAILING,
            message=f"{r.name}: refresh failed {r.consecutive_failures} runs in a row ({first_line})",
            commodity_ids=[r.commodity_id],
        ))

    late = [r for r in rows if LATE in r.flags]
    drop_late = [r for r in late if r.route == CONTENT_DROP]
    if drop_late:
        newest = max((r.last_actual for r in drop_late if r.last_actual), default=None)
        alerts.append(RefreshAlert(
            kind=CONTENT_DROP_LATE,
            message=(f"{len(drop_late)} content-drop series are late (newest actual {newest}): "
                     f"re-extract and re-load the content drop. {_names(drop_late)}"),
            commodity_ids=[r.commodity_id for r in drop_late],
        ))
    for r in late:
        if r.route == CONTENT_DROP or FAILING in r.flags:
            continue   # grouped above, or already alerted as failing
        fix = "nothing updates it; give it a refresh method" if r.route == NONE else f"route: {r.route}"
        alerts.append(RefreshAlert(
            kind=LATE, message=f"{r.name}: {r.reason}; {fix}", commodity_ids=[r.commodity_id],
        ))
    return alerts


def overview_counts(rows: list[RefreshOverviewRow], alerts: list[RefreshAlert]) -> dict[str, int]:
    return {
        "indexes": len(rows),
        "failing": sum(FAILING in r.flags for r in rows),
        "late": sum(LATE in r.flags for r in rows),
        "no_route": sum("no_route" in r.flags for r in rows),
        "never_attempted": sum("never_attempted" in r.flags for r in rows),
        "alerts": len(alerts),
    }


def alert_summary(alerts: list[RefreshAlert]) -> str:
    """One line for a log, a run note or a Sentry message."""
    if not alerts:
        return "no refresh alerts"
    kinds = {}
    for a in alerts:
        kinds[a.kind] = kinds.get(a.kind, 0) + 1
    return f"{len(alerts)} refresh alert(s): " + ", ".join(f"{n} {k}" for k, n in kinds.items())
