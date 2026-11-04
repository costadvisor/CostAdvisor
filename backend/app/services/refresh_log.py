"""Recording index refreshes (Jira 209).

The three calls a refresh job makes:

    run = start_run(db, "scrape_all")
    record(db, run, commodity_id=..., label=..., route="scraper", outcome="ok", rows=4, ...)
    finish_run(db, run)

Every call commits, on purpose: a run that dies half-way must still leave the
attempts it got through, and its header stays `running` — which is itself the
signal that it did not finish. A caller whose own transaction failed rolls
back BEFORE calling `record`, so the record is never lost with the failure.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.index_refresh import (
    OUTCOMES, IndexRefreshAttempt, IndexRefreshRun, IndexRefreshState,
)

# Long tracebacks and HTML error pages are cut to this; the first lines carry
# what is needed to act.
MAX_ERROR_CHARS = 2000

_COUNTER = {"ok": "succeeded", "no_data": "no_data", "rejected": "rejected", "failed": "failed"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_run(db: Session, task: str, *, note: str | None = None) -> IndexRefreshRun:
    run = IndexRefreshRun(task=task, started_at=_now(), status="running", note=note)
    db.add(run)
    db.commit()
    return run


def record(
    db: Session,
    run: IndexRefreshRun,
    *,
    label: str,
    route: str,
    outcome: str,
    commodity_id: int | None = None,
    team_source_id: int | None = None,
    rows: int = 0,
    latest_period: str | None = None,
    error: str | None = None,
    tries: int = 1,
    started_at: datetime | None = None,
) -> IndexRefreshAttempt:
    """One attempt, the run's counters, and — for a platform index — its state."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown refresh outcome {outcome!r}")
    now = _now()
    error = error[:MAX_ERROR_CHARS] if error else None
    attempt = IndexRefreshAttempt(
        run_id=run.id, commodity_id=commodity_id, team_source_id=team_source_id,
        label=label[:160], route=route, outcome=outcome, rows_written=rows,
        latest_period=latest_period, tries=tries, error=error,
        started_at=started_at or now, finished_at=now,
    )
    db.add(attempt)

    run.attempted += 1
    setattr(run, _COUNTER[outcome], getattr(run, _COUNTER[outcome]) + 1)

    if commodity_id is not None and team_source_id is None:
        state = db.get(IndexRefreshState, commodity_id)
        if state is None:
            state = IndexRefreshState(commodity_id=commodity_id, consecutive_failures=0)
            db.add(state)
        state.last_run_id = run.id
        state.last_attempt_at = now
        state.last_outcome = outcome
        if outcome == "ok":
            state.last_success_at = now
            state.consecutive_failures = 0
            state.last_error = None
            if latest_period:
                state.latest_period = latest_period
        else:
            # `no_data` and `rejected` count too: a source that keeps answering
            # with nothing usable is as broken, to a buyer, as one that errors.
            state.consecutive_failures = (state.consecutive_failures or 0) + 1
            state.last_error = error or outcome
    db.commit()
    return attempt


def finish_run(db: Session, run: IndexRefreshRun) -> IndexRefreshRun:
    run.finished_at = _now()
    if run.attempted and run.succeeded == run.attempted:
        run.status = "ok"
    elif run.attempted and run.succeeded == 0:
        run.status = "failed"
    elif not run.attempted:
        run.status = "ok"      # nothing to do is not a failure
    else:
        run.status = "partial"
    db.commit()
    return run
