"""CLI: load the dimension layer + producer master from the 2026-07 drop
(Wave 3, SCRUM-77 / INT-3).

    python seed_dimensions.py [--dry-run]

`--dry-run` is the caller rolling back, not a flag threaded through the loader
— so the dry path genuinely rehearses the real one (the same shape as
seed_index_layer.py and seed_catalog_retarget.py).

The report is the point as much as the load: three of the four facets need an
analyst decision, and the unresolved register is that work queue. Export it as
a decision sheet with the `dimension_decision` sheet-roundtrip payload.
"""
import argparse
import sys

from app.database import SessionLocal, bypass_rls_var
from app.services.drop.dimension_loader import load_dimensions
from app.services.drop.reader import DropNotAvailable
# Side-effect import: region auto-register listener.
from app.services import regions as _region_events  # noqa: F401


def run(dry_run: bool = False) -> int:
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        report = load_dimensions(db)
        print(report.render())
        if dry_run:
            db.rollback()
            print("\nDRY RUN — rolled back, nothing written.")
        else:
            db.commit()
        return 0
    except DropNotAvailable as exc:
        db.rollback()
        print(f"drop not available: {exc}")
        return 2
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    sys.exit(run(dry_run=parser.parse_args().dry_run))
