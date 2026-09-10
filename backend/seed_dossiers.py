"""CLI: load the structured index dossiers and fit the volatility ladder
(Wave 3, DB-7).

    python seed_dossiers.py [--dry-run] [--no-calibration] [--rungs N]

The ladder is **regenerated from the series**, never imported: the shipped
`VOLATILITY_PERCENTILE_BREAKPOINTS.json` deviates from the real 91-series
distribution by up to ~13.7, and its top rung (21.57) sits below the library's
real maximum (35.28) — so the most volatile series would be pinned at 100 by a
ladder that never saw it.

`--dry-run` is the caller rolling back, not a flag threaded through the loader,
so the dry path genuinely rehearses the real one.
"""
import argparse
import sys

from app.database import SessionLocal, bypass_rls_var
from app.services.drop.dossier_loader import load_dossiers
from app.services.drop.reader import DropNotAvailable
from app.services.index_dossier import recompute_volatility_calibration
# Side-effect import: region auto-register listener.
from app.services import regions as _region_events  # noqa: F401


def run(dry_run: bool = False, calibrate: bool = True, rungs: int = 21) -> int:
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        report = load_dossiers(db)
        print(report.render())
        if calibrate:
            try:
                calibration = recompute_volatility_calibration(
                    db, n_rungs=rungs, note="seed_dossiers")
                ladder = [
                    float(b.dispersion)
                    for b in sorted(calibration.breakpoints, key=lambda b: b.rung)
                ]
                print(f"\nVolatility calibration: {calibration.n_rungs} rungs over "
                      f"{calibration.n_series} series, step {calibration.step:.3f}")
                print(f"  ladder: {[round(v, 3) for v in ladder]}")
            except ValueError as exc:
                print(f"\nCalibration skipped: {exc}")
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
    parser.add_argument("--no-calibration", action="store_true")
    parser.add_argument("--rungs", type=int, default=21)
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run, calibrate=not args.no_calibration,
                 rungs=args.rungs))
