"""Fetching with retries, and checking values before they are written (Jira 209).

Two things a scheduled refresh did not do:

* **Retry.** A timeout or a 5xx waited a full week for the next Monday run.
  Transient failures are now retried with back-off, up to `MAX_TRIES` in
  total. A client error (4xx other than 429) is not retried: the request is
  wrong and will be wrong again (the Eurostat 400s are this case).

* **Check before writing.** `BaseScraper.run` wrote whatever came back over
  the existing value. Values are now checked first, and if any fails the
  whole fetch is rejected — the stored values, i.e. the last good ones, stay
  exactly as they were, and the rejection is logged with its reason.
"""
from __future__ import annotations

import asyncio
import math
from typing import Awaitable, Callable

import httpx
from sqlalchemy.orm import Session

from app.models.index_data import IndexValue

# Agreed (Jira 209): three tries in total, then the attempt is a failure.
MAX_TRIES = 3
# Seconds before the 2nd and 3rd tries. Module-level so tests can zero it.
RETRY_BACKOFF_SECONDS = (2.0, 8.0)

# PROPOSED (Jira 209): a new value more than this many times the last good
# value for the same index, region and quarter — or less than its inverse —
# is treated as a source error rather than a market move.
MAX_JUMP_RATIO = 3.0


class FetchFailed(Exception):
    """The fetch gave up. `tries` is how many attempts were made."""

    def __init__(self, error: Exception, tries: int):
        super().__init__(f"{type(error).__name__}: {error}")
        self.error = error
        self.tries = tries


def is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 or code == 429
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


async def fetch_with_retry(fetch: Callable[[], Awaitable[list]]) -> tuple[list, int]:
    """`(points, tries)`, or `FetchFailed` once retries are spent or the error
    is not worth retrying."""
    for attempt in range(1, MAX_TRIES + 1):
        try:
            return await fetch(), attempt
        except Exception as exc:  # noqa: BLE001 — classified below, then re-raised
            if attempt == MAX_TRIES or not is_transient(exc):
                raise FetchFailed(exc, attempt) from exc
            await asyncio.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
    raise AssertionError("unreachable")


def check_points(db: Session, commodity_id: int, points: list) -> str | None:
    """None when every point may be written; otherwise the reason the fetch
    is rejected. Points are `ScrapedDataPoint`s (region, year, quarter, value)."""
    for p in points:
        value = p.value
        if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
            return f"{p.year}Q{p.quarter} {p.region}: value {value!r} is not a number"
        if value <= 0:
            return f"{p.year}Q{p.quarter} {p.region}: value {value} is zero or negative"

    # Jump check against the last good value for that region at or before the
    # point's quarter — the same quarter when it is being restated, otherwise
    # the latest one stored before it. A series with nothing stored yet has
    # nothing to compare with.
    for p in points:
        existing = (
            db.query(IndexValue.value)
            .filter(
                IndexValue.commodity_id == commodity_id,
                IndexValue.region == p.region,
                (IndexValue.year * 10 + IndexValue.quarter) <= p.year * 10 + p.quarter,
            )
            .order_by(IndexValue.year.desc(), IndexValue.quarter.desc())
            .limit(1)
            .scalar()
        )
        if existing:
            ratio = float(p.value) / float(existing)
            if ratio > MAX_JUMP_RATIO or ratio < 1 / MAX_JUMP_RATIO:
                return (f"{p.year}Q{p.quarter} {p.region}: {p.value} vs last good {float(existing)} "
                        f"(×{ratio:.2f}, limit ×{MAX_JUMP_RATIO:g})")
    return None


def latest_period(points: list) -> str | None:
    if not points:
        return None
    p = max(points, key=lambda p: (p.year, p.quarter))
    return f"{p.year}Q{p.quarter}"
