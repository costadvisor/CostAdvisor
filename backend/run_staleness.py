"""CLI: which texts are out of date, and why (LIVE-5 / SCRUM-212).

    python run_staleness.py check [--as-of 2027-01-15] [--write]
    python run_staleness.py list [--kind section] [--limit 30]
    python run_staleness.py review --kind section --id 123 --by Gourav [--write]

**Dry run unless `--write`.** `check` fills missing baselines and gives every
report section, panel, platform lever and block its reasons (the daily task
does the same at 07:50 UTC). `review` re-pins one text's baseline after a
person has read it again.
"""
import argparse
import sys
from datetime import date

from app.database import SessionLocal, bypass_rls_var
from app.services.staleness import KINDS, MODELS, OLD_TEXT, check, mark_reviewed, reader_note


def run(args) -> int:
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        if args.cmd == "check":
            r = check(db, today=args.as_of)
            print(f"Baselines filled: {r.baselines_filled}")
            print(f"  {'kind':<10}{'checked':>9}{'stale':>8}")
            for kind in KINDS:
                print(f"  {kind:<10}{r.checked.get(kind, 0):>9}{r.stale.get(kind, 0):>8}")
            print(f"By reason: {dict(r.by_reason)}")
            print(f"Newly stale: {r.newly_stale}; cleared: {r.cleared}")

        elif args.cmd == "list":
            shown = 0
            age_only = 0
            for kind in ([args.kind] if args.kind else KINDS):
                model = MODELS[kind]
                for row in db.query(model).filter(model.is_stale).limit(args.limit * 50):
                    codes = [r["code"] for r in row.stale_reasons or []]
                    if codes == [OLD_TEXT]:
                        age_only += 1
                        continue
                    if shown < args.limit:
                        print(f"{kind:<8} id={row.id}  {'; '.join(r['detail'] for r in row.stale_reasons)}")
                        print(f"         reader sees: {reader_note(row.stale_reasons)}")
                        shown += 1
            if age_only:
                print(f"\n+ {age_only} text(s) stale only by age (re-load the content drop to refresh them)")
            return 0

        elif args.cmd == "review":
            row = mark_reviewed(db, args.kind, int(args.id) if args.kind != "block" else args.id, by=args.by)
            print(f"Reviewed {args.kind} {row.id}: baseline re-pinned, flag cleared")

        if args.write:
            db.commit()
            print("Written.")
        else:
            db.rollback()
            print("Dry run: nothing written (add --write).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cmd", choices=["check", "list", "review"])
    parser.add_argument("--write", action="store_true", help="save the changes")
    parser.add_argument("--as-of", type=date.fromisoformat, help="check: judge against this date")
    parser.add_argument("--kind", choices=KINDS, help="list / review: which kind of text")
    parser.add_argument("--id", help="review: the text's id")
    parser.add_argument("--by", default="cli", help="review: who read it again")
    parser.add_argument("--limit", type=int, default=30, help="list: rows to print")
    sys.exit(run(parser.parse_args()))
