"""CLI: the typed numbers in reports and blocks, and how far they have drifted
from our data (LIVE-4 / SCRUM-211, S1 + S2).

    python run_number_inventory.py                         # summary
    python run_number_inventory.py --reports-only
    python run_number_inventory.py --csv ../docs/live4/numbers.csv
    python run_number_inventory.py --as-of 2027-01-04

**Read only.** Nothing is written to the database.

The CSV quotes the reports' text, which is licensed content: write it only
under the git-ignored `docs/` folder (the script refuses other places).
"""
import argparse
import csv
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from app.database import SessionLocal, bypass_rls_var
from app.services.number_inventory import inventory
from app.services.number_matching import STATUSES, match_items, summarise

DOCS = (Path(__file__).resolve().parent.parent / "docs").resolve()


def run(csv_path: str | None = None, reports_only: bool = False, as_of: date | None = None) -> int:
    if csv_path and DOCS not in Path(csv_path).resolve().parents:
        print(f"Refusing to write {csv_path}: the CSV quotes licensed text; write it under {DOCS}")
        return 2
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        items = inventory(db, include_blocks=not reports_only)
        matches = match_items(db, items, as_of=as_of)
        table = summarise(matches)

        print(f"Typed numbers: {len(items)}\n")
        print(f"  {'kind':<18}" + "".join(f"{s:>16}" for s in STATUSES))
        for kind in sorted(table):
            print(f"  {kind:<18}" + "".join(f"{table[kind].get(s, 0):>16}" for s in STATUSES))

        ours = [m for m in matches if m.status in ("match", "drifted")]
        drifted = [m for m in ours if m.status == "drifted"]
        print(f"\nFrom our data: {len(ours)}; of those, drifted from today's value: {len(drifted)}")
        high = Counter(m.item.kind for m in ours if m.confidence == "high")
        print(f"High-confidence conversions by kind: {dict(high)}")

        if csv_path:
            Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["source", "doc", "part", "kind", "status", "confidence", "typed", "today",
                            "token", "why", "number_as_typed", "context"])
                for m in matches:
                    it = m.item
                    w.writerow([it.source, it.doc, it.part, it.kind, m.status, m.confidence or "",
                                m.typed or "", m.today or "", m.token or "", m.why, it.text, it.context])
            print(f"\nWrote {len(matches)} rows to {csv_path}")
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", dest="csv_path", help="write every number to this CSV (under docs/)")
    parser.add_argument("--reports-only", action="store_true", help="leave the blocks out")
    parser.add_argument("--as-of", type=date.fromisoformat, help="judge 'late' against this date")
    args = parser.parse_args()
    sys.exit(run(csv_path=args.csv_path, reports_only=args.reports_only, as_of=args.as_of))
