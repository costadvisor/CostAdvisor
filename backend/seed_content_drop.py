"""Load the content drop (design ARCHITECTURE_LIVE_DATA §1.2, §3.2).

    python seed_content_drop.py                          # load everything
    python seed_content_drop.py --dry-run                # do the work, print, roll back
    python seed_content_drop.py --expect-commit <sha>    # refuse any other drop commit
    python seed_content_drop.py --only taxonomy,indexes  # a partial (development) load
    python seed_content_drop.py --stats                  # statements and time per loader

Runs the loaders in dependency order — taxonomy, indexes, catalogue, content,
reports, playbooks — each a module `app/services/content_drop/<name>.py`
exposing `load(db, report)`. A loader module that does not exist is skipped
with a message.

**The drop is checked before the database is opened.** The drop directory is
`CONTENT_DROP_DIR` (default `docs/drop_live`; required outside the main
checkout). The run is refused (exit 2) when its `_manifest.json` reports a
failed or partial constant, when it names no source commit, or when
`--expect-commit` names another commit (full sha, exact match).

**One transaction for the whole run.** Loaders flush and never commit; this
script commits once at the end, or rolls back on `--dry-run` or on any error.
Later loaders read what earlier ones wrote (the catalogue needs the lines the
taxonomy loaded), so a dry run has to rehearse them together, and a failure
part-way must not leave half a drop behind.

**The record.** A real run of all six loaders writes one `content_loads` row
(source commit, date, branch, extractor, drop dir, start and finish, the
loader identity, the per-loader counts and notes) in the same transaction, so
a failed run leaves none. A dry run writes none. A partial run (`--only` with
fewer than six loaders) writes none either: the row means "this database
holds a full load of that commit", which a partial load is not.

**Output contract** (`scripts/ops/rebuild_env.py` parses it; keep it stable):
one `  <loader> changes=<n>` line per loader in the summary, and a real run
ends with the line `Committed.`.

Idempotent: a second run reports zero changes.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import event

from app.database import SessionLocal, bypass_rls_var
from app.models.content_load import ContentLoad
from app.services.drop.reader import DropNotAvailable
from app.services.drop.report import LoadReport
from app.services.content_drop import LOADER_ORDER
from app.services.content_drop.reader import (
    DropManifest, DropRefused, check_commit, drop_dir, loader_user_id,
)
# Side-effect import: region auto-register listener (APAC / MEA on flush).
from app.services import regions as _region_events  # noqa: F401

PACKAGE = "app.services.content_drop"
# Skip reasons kept per loader in content_loads.notes (the full list is printed).
NOTES_SKIP_LIMIT = 200


def resolve_loader(name: str):
    """The loader module for `name`, or None if it has not been written yet.

    `find_spec` rather than catching ModuleNotFoundError: a loader that exists
    but fails on one of its own imports must raise, not pass as "not written".
    """
    module_name = f"{PACKAGE}.{name}"
    if importlib.util.find_spec(module_name) is None:
        return None
    module = importlib.import_module(module_name)
    if not callable(getattr(module, "load", None)):
        raise RuntimeError(f"{module_name} has no load(db, report) function")
    return module


def parse_only(value: str | None) -> list[str]:
    if not value:
        return list(LOADER_ORDER)
    wanted = [v.strip() for v in value.split(",") if v.strip()]
    unknown = [v for v in wanted if v not in LOADER_ORDER]
    if unknown:
        raise ValueError(
            f"unknown loader(s): {', '.join(unknown)} — choose from {', '.join(LOADER_ORDER)}")
    # Always in dependency order, whatever order they were named in.
    return [name for name in LOADER_ORDER if name in wanted]


@dataclass
class LoaderStats:
    """`--stats`: cursor executions (an executemany counts once) and wall time
    of one loader, including its final flush. A measurement, not a gate."""
    statements: int = 0
    seconds: float = 0.0


@dataclass
class RunResult:
    """What `run()` did. `code` is the process exit code (0 done, 2 refused)."""
    code: int
    dry_run: bool
    reports: dict[str, LoadReport] = field(default_factory=dict)   # in run order
    missing: list[str] = field(default_factory=list)               # loader modules absent
    manifest: DropManifest | None = None
    content_load_id: int | None = None
    stats: dict[str, LoaderStats] = field(default_factory=dict)
    error: str | None = None

    @property
    def changes(self) -> int:
        return sum(r.changed for r in self.reports.values())


class _StatementCounter:
    """Counts cursor executions on an engine while attached."""

    def __init__(self, engine):
        self.engine = engine
        self.count = 0
        event.listen(engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, *_args, **_kwargs):
        self.count += 1

    def remove(self):
        if event.contains(self.engine, "before_cursor_execute", self._on_execute):
            event.remove(self.engine, "before_cursor_execute", self._on_execute)


def _counts(reports: dict[str, LoadReport]) -> dict:
    return {
        name: {
            "changes": report.changed,
            "skipped": len(report.skipped),
            "tables": {
                t.table: {"created": t.created, "updated": t.updated, "deleted": t.deleted,
                          "unchanged": t.unchanged, "stale": t.stale, "skipped": len(t.skipped)}
                for t in report.tables
            },
        }
        for name, report in reports.items()
    }


def _notes(found: DropManifest, reports: dict[str, LoadReport],
           stats: dict[str, LoaderStats] | None) -> dict:
    notes: dict = {
        "loaders": list(reports),
        "source_dirty": found.dirty,
        "extracted_at": found.extracted_at.isoformat() if found.extracted_at else None,
        "skipped": {},
    }
    for name, report in reports.items():
        skips = report.skipped
        if skips:
            notes["skipped"][name] = {
                "total": len(skips),
                "first": [f"{table}: {key} — {why}" for table, key, why in skips[:NOTES_SKIP_LIMIT]],
            }
    if stats:
        notes["stats"] = {name: {"statements": s.statements, "seconds": round(s.seconds, 3)}
                          for name, s in stats.items()}
    return notes


def _record_load(db, found: DropManifest, reports: dict[str, LoadReport],
                 stats: dict[str, LoaderStats] | None, started_at: datetime) -> ContentLoad:
    try:
        loaded_by = loader_user_id(db)
    except RuntimeError:
        loaded_by = None
    row = ContentLoad(
        source_commit=found.commit,
        source_date=found.date,
        source_branch=found.branch,
        extractor_version=(found.extractor_version or "")[:255] or None,
        drop_dir=str(found.drop_dir.resolve()),
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        loaded_by=loaded_by,
        dry_run=False,
        counts=_counts(reports),
        notes=_notes(found, reports, stats),
    )
    db.add(row)
    db.flush()
    return row


def _print_source(found: DropManifest) -> None:
    when = found.date.isoformat() if found.date else "date unknown"
    print(f"Source: commit {found.commit} ({when}), branch {found.branch or 'unknown'}")
    print(f"Extractor: {found.extractor_version or 'unknown'}")
    if found.dirty:
        print("WARNING: the source tree had uncommitted changes when this drop was extracted; "
              "the data may not match the commit above.")


def run(only: list[str] | None = None, dry_run: bool = False,
        expect_commit: str | None = None, stats: bool = False) -> RunResult:
    """Check the drop, run the loaders in one transaction, commit or roll back.

    Returns a `RunResult` (the per-loader reports, the new `content_loads` id
    on a full real run, `--stats` measurements). A refused drop returns
    `code=2` without opening the database; a loader exception is re-raised
    after the rollback."""
    names = list(only) if only else list(LOADER_ORDER)
    try:
        root = drop_dir()
        found = check_commit(expect_commit)
    except (DropNotAvailable, DropRefused) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return RunResult(code=2, dry_run=dry_run, error=str(exc))

    result = RunResult(code=0, dry_run=dry_run, manifest=found)
    full = set(names) == set(LOADER_ORDER)
    # Platform reference data — the loaders write tables with no team_id and
    # read across every team's rows to build their diffs. Restored on exit so
    # an in-process caller (a test) does not inherit the bypass.
    bypass_token = bypass_rls_var.set(True)
    db = SessionLocal()
    counter = _StatementCounter(db.get_bind()) if stats else None
    started_at = datetime.now(timezone.utc)
    try:
        print(f"Content drop: {root}")
        _print_source(found)
        for name in names:
            module = resolve_loader(name)
            if module is None:
                print(f"\n[{name}] skipped — {PACKAGE}.{name} does not exist yet")
                result.missing.append(name)
                continue
            report = LoadReport(title=f"content drop · {name}")
            t0 = time.perf_counter()
            n0 = counter.count if counter else 0
            loaded = module.load(db, report)
            if isinstance(loaded, LoadReport):
                report = loaded
            db.flush()
            if counter:
                result.stats[name] = LoaderStats(counter.count - n0, time.perf_counter() - t0)
            result.reports[name] = report
            print()
            print(report.render(dry_run=dry_run))

        record = None
        if dry_run:
            db.rollback()
        else:
            if full and not result.missing:
                record = _record_load(db, found, result.reports, result.stats or None, started_at)
                result.content_load_id = record.id
            db.commit()

        print("\nSummary")
        for name, report in result.reports.items():
            print(f"  {name:10s} changes={report.changed:6d}  skipped={len(report.skipped):5d}")
        for name in result.missing:
            print(f"  {name:10s} not run (module missing)")
        if counter:
            print("\nStats (statements = cursor executions; an executemany counts once)")
            for name, s in result.stats.items():
                print(f"  {name:10s} statements={s.statements:7d}  time={s.seconds:8.2f}s")
            total_s = sum(s.seconds for s in result.stats.values())
            total_n = sum(s.statements for s in result.stats.values())
            print(f"  {'all':10s} statements={total_n:7d}  time={total_s:8.2f}s")
        if dry_run:
            print("DRY RUN — rolled back, nothing written.")
        else:
            if record is not None:
                print(f"Recorded content load #{record.id} (commit {found.commit}).")
            else:
                print("Partial load: no content_loads row recorded (only a full run records one).")
            print("Committed.")
        return result
    except DropNotAvailable as exc:
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        result.code = 2
        result.error = str(exc)
        return result
    except Exception:
        # Never leave a half-applied load behind.
        db.rollback()
        raise
    finally:
        if counter:
            counter.remove()
        db.close()
        bypass_rls_var.reset(bypass_token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="do the work, print the diff, then roll back")
    parser.add_argument("--only", metavar="NAME[,NAME]",
                        help=f"run only these loaders ({', '.join(LOADER_ORDER)}); "
                             f"a partial run records no content_loads row")
    parser.add_argument("--expect-commit", metavar="SHA",
                        help="refuse unless the drop's _manifest.json names exactly this commit")
    parser.add_argument("--stats", action="store_true",
                        help="print statements and time per loader (a measurement, not a gate)")
    args = parser.parse_args(argv)
    try:
        selected = parse_only(args.only)
    except ValueError as exc:
        parser.error(str(exc))
    return run(only=selected, dry_run=args.dry_run, expect_commit=args.expect_commit,
               stats=args.stats).code


if __name__ == "__main__":
    raise SystemExit(main())
