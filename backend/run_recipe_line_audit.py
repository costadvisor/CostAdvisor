"""CLI: does every recipe line point at a real index with data? (Jira 208)

    python run_recipe_line_audit.py                      # summary by status
    python run_recipe_line_audit.py --status other_region
    python run_recipe_line_audit.py --csv audit.csv      # every line, one row each
    python run_recipe_line_audit.py --as-of 2027-01-02   # age the data against a date

**Read only.** Nothing is written to the database; the transaction is rolled
back. The statuses and their order are in `app/services/recipe_line_audit.py`.
"""
import argparse
import csv
import sys
from dataclasses import asdict, fields
from datetime import date

from app.database import SessionLocal, bypass_rls_var
from app.services.recipe_line_audit import (
    STATUSES, LineAudit, audit_recipe_lines, summarise,
)


def run(status: str | None = None, csv_path: str | None = None,
        as_of: date | None = None, limit: int = 50) -> int:
    # Platform tables, no tenant — the same bypass the seeders take.
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        as_of = as_of or date.today()
        lines = audit_recipe_lines(db, as_of=as_of)
        summary = summarise(db, lines, as_of=as_of)

        print(f"Recipe-line audit as of {as_of}: {summary.lines} lines "
              f"in {summary.recipes} recipes\n")
        print(f"  {'status':<16}{'lines':>8}{'recipes':>9}{'% weight':>10}")
        for name in STATUSES:
            row = summary.by_status[name]
            if row["lines"]:
                print(f"  {name:<16}{row['lines']:>8}{row['recipes']:>9}"
                      f"{row['weight_pct_of_all']:>10.2f}")
        print(f"\n  Meets the criterion: {summary.meets_pct_of_index_weight}% "
              "of index and material weight")
        print(f"  Proxy status unclassified on {summary.proxy_unclassified_lines} index lines")
        if summary.templates_without_product_line:
            print(f"  Templates with no product line ({len(summary.templates_without_product_line)}): "
                  + ", ".join(summary.templates_without_product_line))

        if status:
            picked = sorted((l for l in lines if l.status == status), key=lambda l: -l.weight_pct)
            print(f"\n{status} — {len(picked)} line(s), heaviest first:")
            for l in picked[:limit]:
                print(f"  {l.template_code:<18} {str(l.region):<8} {l.weight_pct:>6.1f}%  "
                      f"{l.line_name[:40]:<40}  {l.reason}")
            if len(picked) > limit:
                print(f"  ... and {len(picked) - limit} more (use --csv for all)")

        if csv_path:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=[x.name for x in fields(LineAudit)])
                writer.writeheader()
                writer.writerows(asdict(l) for l in lines)
            print(f"\nWrote {len(lines)} lines to {csv_path}")
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", choices=STATUSES, help="list the lines in one status")
    parser.add_argument("--csv", dest="csv_path", help="write every line to this CSV file")
    parser.add_argument("--as-of", type=date.fromisoformat, help="age the data against this date")
    parser.add_argument("--limit", type=int, default=50, help="lines to print for --status")
    args = parser.parse_args()
    sys.exit(run(status=args.status, csv_path=args.csv_path, as_of=args.as_of, limit=args.limit))
