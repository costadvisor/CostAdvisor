"""CLI: do reports and intelligence point at indexes? (Jira 208)

    python run_mention_audit.py                 # summary for reports and editorial blocks
    python run_mention_audit.py --terms 30      # the most-mentioned commodity terms
    python run_mention_audit.py --csv out.csv   # one row per report section / block

**Read only.** Nothing is written; the transaction is rolled back. What counts
as a mention, and why matching is literal, is in
`app/services/mention_audit.py`.

The CSV holds section and block references from the licensed content: write
it outside the repository.
"""
import argparse
import csv
import sys

from app.database import SessionLocal, bypass_rls_var
from app.services.mention_audit import EXCLUDED_TERMS, audit_mentions

_LABELS = {
    "units": "sections / blocks",
    "with_commodity_mention": "name a commodity",
    "with_product_code": "name a catalogue product code",
    "with_neither": "name neither",
    "with_typed_figures": "contain a typed % or price",
    "anchored_figures": "sentences with a commodity AND a typed figure",
    "already_linked": "already carry an index link",
}


def run(terms: int = 15, csv_path: str | None = None) -> int:
    # Platform tables, no tenant — the same bypass the seeders take.
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        rows, summary = audit_mentions(db)
        print(f"Mention audit: {summary.vocabulary_terms} commodity terms from series and card names "
              f"(left out as everyday words: {', '.join(sorted(EXCLUDED_TERMS))})\n")
        for title, block in (("Reports", summary.reports), ("Intelligence (editorial blocks)", summary.editorial)):
            print(title)
            for key, label in _LABELS.items():
                print(f"  {block[key]:>7}  {label}")
            print()
        print(f"Most-mentioned terms (of {len(summary.terms)}):")
        for t in summary.terms[:terms]:
            series = ", ".join(t["series"])
            note = "" if t["single_series"] else "   <- several series: needs a region choice"
            print(f"  {t['units']:>6}  {t['term']:<28} {series}{note}")

        if csv_path:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["source", "ref", "commodity_terms", "product_codes",
                                 "typed_figures", "anchored_figures", "already_linked"])
                for r in rows:
                    writer.writerow([r.source, r.ref, "; ".join(r.commodity_terms),
                                     "; ".join(r.product_codes), r.typed_figures,
                                     r.anchored_figures, r.already_linked])
            print(f"\nWrote {len(rows)} rows to {csv_path}")
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terms", type=int, default=15, help="most-mentioned terms to print")
    parser.add_argument("--csv", dest="csv_path", help="write one row per section / block to this CSV")
    args = parser.parse_args()
    sys.exit(run(terms=args.terms, csv_path=args.csv_path))
