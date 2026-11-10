"""CLI: record which data every text's live numbers read (Jira 210, S5).

    python run_live_derived_from.py            # dry run: what would change, and broken tokens
    python run_live_derived_from.py --write    # write `derived_from` on the rows that differ

**Dry run unless `--write`.** New text records `derived_from` as it is
written; this fills rows written before that. Safe to run again.
"""
import argparse
import sys

from app.database import SessionLocal, bypass_rls_var
from app.services.live_derived import recompute


def run(write: bool = False, limit: int = 50) -> int:
    # Platform tables, no tenant — the same bypass the seeders take.
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        report = recompute(db)
        print(f"{'table':<26}{'checked':>9}{'with tokens':>13}{'would change' if not write else 'changed':>14}")
        for table, n in report.checked.items():
            print(f"{table:<26}{n:>9}{report.with_tokens.get(table, 0):>13}"
                  f"{report.changed.get(table, 0):>14}")
        if report.problems:
            print(f"\n{len(report.problems)} broken token(s) (readers show their fallback):")
            for p in report.problems[:limit]:
                print(f"  {p}")
            if len(report.problems) > limit:
                print(f"  ... and {len(report.problems) - limit} more")
        if write:
            db.commit()
            print("\nWritten.")
        else:
            db.rollback()
            print("\nDry run: nothing written (add --write).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the changes")
    parser.add_argument("--limit", type=int, default=50, help="broken tokens to print")
    args = parser.parse_args()
    sys.exit(run(write=args.write, limit=args.limit))
