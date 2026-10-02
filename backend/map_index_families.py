"""CLI: derive commodity_indexes.family_id/subfamily_id from real recipe usage.

    python map_index_families.py            # dry run, reports only
    python map_index_families.py --apply     # actually write

Dry run is the default, same reasoning as purge_junk_calibrations.py: the dry
path is the real path with the transaction rolled back, so it rehearses
exactly what --apply would do. Re-runnable — a commodity already correctly
mapped is reported unchanged, never re-touched.

See app/services/index_family_mapping.py for what "correctly mapped" means
and why a commodity with no recipe usage is left NULL rather than guessed.
"""
import argparse
import sys

from app.database import SessionLocal, bypass_rls_var
from app.services.index_family_mapping import map_index_families


def run(apply: bool = False) -> int:
    # Platform tables (commodity_indexes, chemical_families, formula_templates
    # all have no team_id), same bypass the other repair/mapping CLIs take.
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        report = map_index_families(db, apply=apply)
        print(report.render())
        if apply:
            db.commit()
            print("\nApplied.")
        else:
            db.rollback()
            print("\nDRY RUN — rolled back, nothing written. "
                  "Re-run with --apply to write.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write family_id/subfamily_id (default is a dry run)")
    args = parser.parse_args()
    sys.exit(run(apply=args.apply))
