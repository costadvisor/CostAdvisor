"""Load combos + recipes from the 2026-07 drop (SCRUM-74/3b).

    python seed_catalog_retarget.py --dry-run    # report, write nothing
    python seed_catalog_retarget.py              # load

Requires `seed_index_layer.py` to have run first — recipe lines resolve
through `type_codes`, so the index layer has to exist.

Replaces the recipe data the older 257-formula drop left behind, but only for
the templates this drop actually covers. Expert sign-offs on coverage rows are
preserved; see app/services/drop/catalog_loader.py for why coverage is upserted
while lines are replaced.
"""
import argparse
import sys

from app.database import SessionLocal, bypass_rls_var
from app.models.index_layer import TypeCode
from app.services.drop.catalog_loader import load_catalog
from app.services.drop.reader import DropNotAvailable


def run(dry_run: bool = False) -> int:
    db = SessionLocal()
    # Platform catalog data — no team_id on these rows, and the loader reads
    # across the whole library to build its diff.
    bypass_rls_var.set(True)
    try:
        if db.query(TypeCode).count() == 0:
            print(
                "ERROR: no type codes loaded — run seed_index_layer.py first "
                "(recipe lines resolve through them).",
                file=sys.stderr,
            )
            return 2

        report = load_catalog(db)
        print(report.render(dry_run=dry_run))
        if dry_run:
            db.rollback()
        else:
            db.commit()
        return 0
    except DropNotAvailable as exc:
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="do the work, print the diff, then roll back",
    )
    raise SystemExit(run(dry_run=parser.parse_args().dry_run))
