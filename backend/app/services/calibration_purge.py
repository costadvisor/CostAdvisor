"""One-off repair: drop volatility calibrations the test suite left behind.

Before T0.1 the suite ran against the *application* database, so every test
that called `recompute_volatility_calibration` wrote a real vintage into real
data — and each one was fitted over the live library **plus** whatever synthetic
series that test had just inserted. The suite is now pointed at its own
database, so this is a one-off cleanup of the damage already done, not a job to
schedule.

**The discriminator is `n_series`, not `n_rungs`.** It is tempting to purge the
11-rung rows because nothing in the app passes 11 — the default is 21 and the
only 11 in the codebase is one test asserting that `step` derives from the
ladder's length. But `n_rungs` is a caller's choice (the API accepts 2-101), so
a future 11-rung ladder could be perfectly legitimate, and 15 of the *21*-rung
rows are contaminated in exactly the same way. What actually gives them away is
being fitted over more series than the library contains.

The predicate is therefore, all three:

  * **not active** — the ladder readers use is never a candidate, whatever else
    is true of it. `percentile_for` reads only the active row, so this alone
    makes the purge unobservable to every consumer;
  * **no note** — `seed_dossiers` stamps its own, and the rebuilt active row
    explains itself. Anything a human or a real CLI authored says who made it;
  * **fitted over a series count the library cannot produce** — the caller
    passes the count in rather than it being inferred, so a stale count is
    reported honestly rather than guessed at.

Nothing stores a calibration id: `VolatilityReading` is computed at read time
and names whichever calibration was active then, so no stored row loses its
explanation. `volatility_breakpoints` is the only FK and cascades.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.index_dossier import VolatilityBreakpoint, VolatilityCalibration


@dataclass
class Row:
    n_rungs: int
    n_series: int
    count: int
    first: str
    last: str


@dataclass
class PurgeReport:
    live_n_series: int
    deleted: list[Row] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    n_deleted: int = 0
    n_breakpoints: int = 0

    def render(self) -> str:
        out = [f"Volatility calibrations — library fits {self.live_n_series} series",
               ""]
        if self.deleted:
            out.append(f"Deleting {self.n_deleted} calibration(s) "
                       f"+ {self.n_breakpoints} breakpoint(s):")
            for r in sorted(self.deleted, key=lambda r: (-r.count, r.n_rungs)):
                out.append(f"  {r.count:>4}  n_rungs={r.n_rungs:<4} "
                           f"n_series={r.n_series:<4}  {r.first} .. {r.last}")
        else:
            out.append("Nothing to delete.")
        out.append("")
        out.append(f"Keeping {len(self.kept)}:")
        for line in self.kept:
            out.append(f"  {line}")
        return "\n".join(out)


def live_series_count(db: Session) -> int:
    """How many series the active calibration was fitted over.

    Used as the reference the contaminated rows are measured against. Read from
    the active row rather than recomputed: recomputing here would fit a ladder
    as a side effect of a delete, and the active row is by definition the one
    the library last agreed on.
    """
    active = db.query(VolatilityCalibration).filter(
        VolatilityCalibration.is_active.is_(True)).first()
    return active.n_series if active else 0


def purge(db: Session, *, apply: bool = False) -> PurgeReport:
    reference = live_series_count(db)
    report = PurgeReport(live_n_series=reference)

    rows = db.query(VolatilityCalibration).order_by(
        VolatilityCalibration.computed_at).all()
    doomed, shapes = [], {}
    for cal in rows:
        contaminated = (
            not cal.is_active
            and cal.note is None
            # `>` not `!=`: a ladder fitted over *fewer* series than today's is
            # simply older, which is what a vintage is. Only an impossible
            # surplus proves synthetic rows were present.
            and reference > 0 and cal.n_series > reference
        )
        if contaminated:
            doomed.append(cal)
            key = (cal.n_rungs, cal.n_series)
            seen = shapes.get(key)
            stamp = cal.computed_at.date().isoformat()
            if seen is None:
                shapes[key] = Row(cal.n_rungs, cal.n_series, 1, stamp, stamp)
            else:
                seen.count += 1
                seen.first = min(seen.first, stamp)
                seen.last = max(seen.last, stamp)
        else:
            report.kept.append(
                f"{cal.id} {'ACTIVE ' if cal.is_active else '       '}"
                f"n_rungs={cal.n_rungs} n_series={cal.n_series} "
                f"{cal.computed_at.date().isoformat()}"
                + (f'  "{cal.note}"' if cal.note else ""))

    report.deleted = list(shapes.values())
    report.n_deleted = len(doomed)
    if doomed:
        ids = [c.id for c in doomed]
        report.n_breakpoints = db.query(VolatilityBreakpoint).filter(
            VolatilityBreakpoint.calibration_id.in_(ids)).count()
        if apply:
            # Breakpoints cascade, but deleting them explicitly keeps the count
            # in the report and the rows removed in one statement each rather
            # than one per ORM object.
            db.query(VolatilityBreakpoint).filter(
                VolatilityBreakpoint.calibration_id.in_(ids)).delete(
                    synchronize_session=False)
            db.query(VolatilityCalibration).filter(
                VolatilityCalibration.id.in_(ids)).delete(
                    synchronize_session=False)
    return report
