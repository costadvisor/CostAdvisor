"""Load the three-layer index model from the 2026-07 drop (SCRUM-74, Loader v2).

    python seed_index_layer.py --dry-run    # report, write nothing
    python seed_index_layer.py              # load

Idempotent: a second run reports zero changes. The loading itself lives in
`app/services/drop/index_loader.py` so it is testable without a CLI; this is
just the entry point, matching the other seed_*.py scripts.

Distinct from the pre-drop seeders (`seed_index_metadata`, `seed_catalog`,
`seed_combos`), which read the older reference workbooks and are untouched by
this. Whether those are retargeted at the drop or retired is a separate call;
this loader neither replaces nor competes with them, because the drop's series
load as their own population (`commodity_key IS NOT NULL`).
"""
import argparse
import sys

from app.database import SessionLocal, bypass_rls_var
from app.services.drop.index_loader import load_index_layer
from app.services.drop.reader import DropNotAvailable


def run(dry_run: bool = False) -> int:
    db = SessionLocal()
    # Platform reference data — the loader writes tables that carry no team_id
    # and no RLS policy, and reads across every team's rows to build its diff.
    bypass_rls_var.set(True)
    try:
        report = load_index_layer(db)
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
        # Never leave a half-applied load behind.
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
