"""CLI: drop volatility calibrations the test suite wrote into the app database.

    python purge_junk_calibrations.py            # dry run, reports only
    python purge_junk_calibrations.py --apply    # actually delete

**Dry run is the default**, like `repair_dimension_orphans.py` and for the same
reason: this one deletes. The dry path is the real path with the transaction
rolled back, so it rehearses exactly what `--apply` would do.

Why the rows exist, what distinguishes them, and why removing them is
unobservable to every consumer is in `app/services/calibration_purge.py`. This
is a one-off cleanup — T0.1 pointed the suite at its own database, so nothing
writes these any more.
"""
import argparse
import sys

from app.database import SessionLocal, bypass_rls_var
from app.services.calibration_purge import purge


def run(apply: bool = False) -> int:
    # Platform table, no tenant — same bypass the seeders and the other repair
    # CLI take.
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        report = purge(db, apply=apply)
        print(report.render())
        if apply:
            db.commit()
            print("\nApplied.")
        else:
            db.rollback()
            print("\nDRY RUN — rolled back, nothing written. "
                  "Re-run with --apply to delete.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="delete the rows (default is a dry run)")
    args = parser.parse_args()
    sys.exit(run(apply=args.apply))
