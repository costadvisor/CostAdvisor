"""CLI: run the index data-quality checks (SCRUM-34).

    python run_index_validation.py                # dry run, reports only
    python run_index_validation.py --apply        # store the findings
    python run_index_validation.py --derived-only # skip the carried-through register
    python run_index_validation.py --check proxy_status_contradiction

**Dry run is the default.** Unlike the seed CLIs, and like
`repair_dimension_orphans.py` / `purge_junk_calibrations.py`: this one writes a
judgement about the library's data quality, so seeing it before storing it is
the useful order. The dry path is the real path with the transaction rolled
back, so it rehearses exactly what `--apply` would record.

What each check looks for, and why `no_series` and `unclassified` are
deliberately not findings, is in `app/services/index_validation.py`.
"""
import argparse
import sys

from app.database import SessionLocal, bypass_rls_var
from app.services.index_validation import run_validation


def run(apply: bool = False, include_declared: bool = True,
        check: str | None = None, note: str | None = None) -> int:
    # Platform tables, no tenant — the same bypass the seeders take.
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        report = run_validation(db, include_declared=include_declared, note=note)
        print(report.render())
        if check:
            rows = report.by_check(check)
            print(f"\n{check} — {len(rows)} finding(s):")
            for finding in rows[:50]:
                print(f"  {finding.subject_key:<28} {finding.summary}")
            if len(rows) > 50:
                print(f"  ... and {len(rows) - 50} more")
        if apply:
            db.commit()
            print("\nStored.")
        else:
            db.rollback()
            print("\nDRY RUN — rolled back, nothing stored. "
                  "Re-run with --apply to record these findings.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="store the findings (default is a dry run)")
    parser.add_argument("--derived-only", action="store_true",
                        help="skip the carried-through _issues.csv register")
    parser.add_argument("--check", help="list the findings for one check code")
    parser.add_argument("--note", help="a note recorded on the run")
    args = parser.parse_args()
    sys.exit(run(apply=args.apply, include_declared=not args.derived_only,
                 check=args.check, note=args.note))
