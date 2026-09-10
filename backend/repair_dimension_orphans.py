"""CLI: remove dimension assertions that nothing supports (SCRUM-77 follow-up).

    python repair_dimension_orphans.py                 # dry run, reports only
    python repair_dimension_orphans.py --kind industry # scope to one facet
    python repair_dimension_orphans.py --term adhesives-sealants   # or one term
    python repair_dimension_orphans.py --apply         # actually delete

**Dry run is the default**, the opposite of the seed CLIs, because this one
deletes. `--apply` is the only way to write, and the dry path is the real path
with the transaction rolled back, so it rehearses exactly what would happen.

The predicate and why it is safe are in `app/services/dimension_repair.py`. In
short: a row qualifies only if it has *no recorded alias* **and** its raw value
does not resolve to the term it sits under under the current vocabulary. Rows
failing only the first half are sound and are kept — `functionality_family` has
56 of them.
"""
import argparse
import sys

from app.database import SessionLocal, bypass_rls_var
from app.services.dimension_repair import repair


def run(kind: str | None = None, term_codes: list[str] | None = None,
        apply: bool = False) -> int:
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        report = repair(db, kind=kind, term_codes=term_codes, apply=apply)
        print(report.render())
        if apply:
            db.commit()
            print("\nApplied.")
        else:
            db.rollback()
            print("\nDRY RUN — rolled back, nothing written. Re-run with --apply to delete.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", default=None,
                    help="Scope to one facet (e.g. industry). Default: every facet.")
    ap.add_argument("--term", action="append", dest="term_codes", default=None,
                    help="Narrow to one term code; repeatable. Use to fix a single "
                         "bad term without touching the rest of a facet.")
    ap.add_argument("--apply", action="store_true",
                    help="Delete the orphaned rows. Without this, reports only.")
    args = ap.parse_args()
    sys.exit(run(kind=args.kind, term_codes=args.term_codes, apply=args.apply))
