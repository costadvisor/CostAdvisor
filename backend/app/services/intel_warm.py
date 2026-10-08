"""Warm the Intelligence snapshots when an API process starts (design §3.4,
decision 33).

The catalogue snapshot (`intel_catalogue.get_catalogue`) and the reference
snapshot (`intel_reference.get_snapshot`) each take a second or two to build.
Without a warm-up the first visitor after a deploy pays for both. Each API
process calls `warm()` once from the app lifespan, in a background thread,
so every process warms itself: no warm-up script, no token, no load-balancer
targeting.

    # app/main.py lifespan (WP-7 owns main.py)
    asyncio.get_running_loop().run_in_executor(None, intel_warm.warm)
    # or: asyncio.create_task(asyncio.to_thread(intel_warm.warm))

**Blocking, and never raises.** `warm()` does its work in the calling thread
and returns the build time of each snapshot in milliseconds, or None for one
that failed. A failure is logged and does not stop the other snapshot or the
app: that snapshot then builds on its first request, as before.

**Same visibility as a request.** It opens its own session with no user and
no RLS bypass. Platform rows (`team_id IS NULL`) are visible to every session,
and the snapshots read only platform rows, so the version key it computes is
the one a signed-in request computes, and the warm snapshot is reused.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def _catalogue(db: Session) -> None:
    from app.services.intel_catalogue import get_catalogue
    get_catalogue(db)


def _reference(db: Session) -> None:
    from app.services.intel_reference import get_snapshot
    get_snapshot(db)


SNAPSHOTS: tuple[tuple[str, Callable[[Session], None]], ...] = (
    ("catalogue", _catalogue),
    ("reference", _reference),
)


def warm() -> dict[str, float | None]:
    """Build both snapshots now. Returns {name: build ms, or None if it failed}."""
    from app.database import SessionLocal

    out: dict[str, float | None] = {}
    for name, build in SNAPSHOTS:
        db = SessionLocal()
        t0 = time.perf_counter()
        try:
            build(db)
            out[name] = round((time.perf_counter() - t0) * 1000, 1)
            log.info("intel_warm: %s snapshot ready in %.0f ms", name, out[name])
        except Exception:  # noqa: BLE001 - a warm-up must never take the app down
            out[name] = None
            log.exception("intel_warm: %s snapshot failed; it will build on first request", name)
        finally:
            db.rollback()
            db.close()
    return out
