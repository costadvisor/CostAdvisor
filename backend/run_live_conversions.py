"""CLI: convert the numbers typed into reports into live tokens (LIVE-4 / SCRUM-211).

    python run_live_conversions.py status
    python run_live_conversions.py propose [--write]
    python run_live_conversions.py review --csv ../docs/live4/review.csv
    python run_live_conversions.py approve --ids 12,15 [--by NAME] [--write]
    python run_live_conversions.py approve --kind cost_bar [--by NAME] [--write]
    python run_live_conversions.py reject --ids 7 [--write]
    python run_live_conversions.py apply [--write]
    python run_live_conversions.py export --csv ../docs/live4/for_laurent.csv
    python run_live_conversions.py verify

**Dry run unless `--write`.** `propose` turns LIVE-4 S2's findings into rows
(a typed number equal to today's is approved at once; a drifted one waits for
a person). `apply` writes the approved ones into the stored reports; the
report loader applies them again on every content load.

CSVs quote report text (licensed content): they are written only under the
git-ignored `docs/` folder.
"""
import argparse
import csv
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.database import SessionLocal, bypass_rls_var
from app.models.live_conversion import KINDS, ReportLiveConversion
from app.services.live_conversions import apply_stored, propose, verify

DOCS = (Path(__file__).resolve().parent.parent / "docs").resolve()
COLUMNS = ["id", "status", "slug", "section_id", "kind", "typed", "today", "token", "anchor",
           "decided_by", "decided_at"]


def _write_csv(path: str, rows) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([getattr(r, c) or "" for c in COLUMNS])
    print(f"Wrote {len(rows)} rows to {path}")


def _status(db) -> None:
    c = Counter((r.kind, r.status) for r in db.query(ReportLiveConversion))
    print(f"  {'kind':<10}{'approved':>10}{'proposed':>10}{'rejected':>10}")
    for kind in KINDS:
        print(f"  {kind:<10}" + "".join(f"{c.get((kind, s), 0):>10}"
                                        for s in ("approved", "proposed", "rejected")))


def run(args) -> int:
    if getattr(args, "csv_path", None) and DOCS not in Path(args.csv_path).resolve().parents:
        print(f"Refusing to write {args.csv_path}: it quotes licensed text; write it under {DOCS}")
        return 2
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        if args.cmd == "status":
            _status(db)
            return 0

        if args.cmd == "propose":
            r = propose(db)
            print(f"New rows: {r.created} (approved at once, typed = today: {r.approved_now}); "
                  f"already there: {r.already}; no unique place found: {r.no_anchor}")

        elif args.cmd == "review":
            rows = (db.query(ReportLiveConversion).filter(ReportLiveConversion.status == "proposed")
                    .order_by(ReportLiveConversion.kind, ReportLiveConversion.slug).all())
            print(f"{len(rows)} proposed row(s) waiting for a decision")
            if args.csv_path:
                _write_csv(args.csv_path, rows)
            return 0

        elif args.cmd in ("approve", "reject"):
            q = db.query(ReportLiveConversion).filter(ReportLiveConversion.status == "proposed")
            if args.ids:
                q = q.filter(ReportLiveConversion.id.in_([int(i) for i in args.ids.split(",")]))
            elif args.kind:
                q = q.filter(ReportLiveConversion.kind == args.kind)
            else:
                print("Say which rows: --ids 1,2,3 or --kind cost_bar")
                return 2
            rows = q.all()
            now = datetime.now(timezone.utc)
            for r in rows:
                r.status = "approved" if args.cmd == "approve" else "rejected"
                r.decided_by, r.decided_at = args.by, now
            print(f"{args.cmd}d {len(rows)} row(s)")

        elif args.cmd == "apply":
            r = apply_stored(db)
            print(f"Applied: {r.applied}; sections changed: {r.sections_changed}, "
                  f"panels changed: {r.panels_changed}")
            if r.not_reversible:
                print(f"NOT applied, failed the reversibility check: {r.not_reversible}")
            if r.skipped:
                print(f"{len(r.skipped)} skipped (text changed or not found), e.g.:")
                for s in r.skipped[:10]:
                    print(f"  {s}")

        elif args.cmd == "verify":
            v = verify(db)
            print(f"Checked {v.sections} section(s) and {v.panels} panel(s) carrying {v.tokens} token(s)")
            print(f"  raw token left for a reader: {len(v.leftovers)}  {v.leftovers[:5]}")
            print(f"  failed the sanitiser:        {len(v.unsafe)}  {v.unsafe[:5]}")
            print(f"  a value fell back (no data): {len(v.missing)}  {v.missing[:5]}")
            return 1 if v.leftovers or v.unsafe else 0

        elif args.cmd == "export":
            rows = (db.query(ReportLiveConversion).filter(ReportLiveConversion.status == "approved")
                    .order_by(ReportLiveConversion.slug, ReportLiveConversion.section_id).all())
            if not args.csv_path:
                print("Say where: --csv ../docs/live4/for_laurent.csv")
                return 2
            _write_csv(args.csv_path, rows)
            return 0

        _status(db)
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
    parser.add_argument("cmd", choices=["status", "propose", "review", "approve", "reject", "apply", "export",
                                        "verify"])
    parser.add_argument("--write", action="store_true", help="save the changes")
    parser.add_argument("--csv", dest="csv_path", help="review / export: CSV path under docs/")
    parser.add_argument("--ids", help="approve / reject: comma-separated row ids")
    parser.add_argument("--kind", choices=KINDS, help="approve / reject: every proposed row of this kind")
    parser.add_argument("--by", default="cli", help="approve / reject: who decided")
    sys.exit(run(parser.parse_args()))
