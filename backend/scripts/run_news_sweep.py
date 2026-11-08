#!/usr/bin/env python
"""
Run the monthly news sweep by hand (SCRUM-214).

Same code as the scheduled run and the portal's "Run now", from a terminal:
useful on a server shell, or to try one family before switching the schedule on.

How to run (from backend/, with ANTHROPIC_API_KEY set)
------------------------------------------------------
    python scripts/run_news_sweep.py --family FG           # one family, by code
    python scripts/run_news_sweep.py                       # every active profile
    python scripts/run_news_sweep.py --family FG --cap 2   # with a $2 spend cap

Costs money: up to NEWS_SWEEP_SEARCHES_PER_PROFILE searches ($0.01 each) plus
tokens per profile. The run log appears in Curation -> News profiles -> Runs.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, bypass_rls_var  # noqa: E402
from app.models import ChemicalFamily  # noqa: E402,F401  (registers all models)
from app.services.news_sweep import SweepAlreadyRunning, run_sweep  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--family", help="family code, e.g. FG. Default: every active profile")
    ap.add_argument("--cap", type=float, help="spend cap in USD for this run")
    args = ap.parse_args()

    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        family_id = None
        if args.family:
            fam = db.query(ChemicalFamily).filter(ChemicalFamily.code == args.family).first()
            if fam is None:
                print(f"No family with code {args.family}.", file=sys.stderr)
                return 1
            family_id = fam.id
        try:
            run = run_sweep(db, trigger="manual", family_id=family_id, spend_cap=args.cap)
        except SweepAlreadyRunning as e:
            print(e, file=sys.stderr)
            return 1
        print(f"{run.status}: {run.profiles_run}/{run.profiles_planned} profiles, {run.searches} searches, "
              f"{run.items_new} new, {run.items_duplicate} duplicates, ${run.cost_usd:.2f} of ${run.spend_cap_usd:.2f}")
        for d in run.details:
            print(f"  {d['label']}: {d['new']} new, {d['duplicate']} dup, ${d['cost_usd']:.3f}")
        for e in run.errors:
            print(f"  ! {e['label']}: {e['message']}")
        return 0 if run.status in ("completed", "stopped_at_cap") else 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
