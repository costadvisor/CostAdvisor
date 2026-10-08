"""Seed the demo buyer, "Aquaverde Water Utility (demo)" (demo build, spec D8).

    python seed_demo_buyer.py              # create, or bring up to date
    python seed_demo_buyer.py --dry-run    # do the work, print the diff, roll back
    python seed_demo_buyer.py --reset      # delete the demo team and recreate it
    python seed_demo_buyer.py --owner-email you@example.com   # another owner

ILLUSTRATIVE DEMO BUYER DATA. The prices, volumes, supplier premiums and sites
this writes are made up for the investor demo (see the header of
`app/services/demo_buyer.py` for the base prices and how each quarter is
generated). They live only in the demo team and are never shown on the
Intelligence pages.

Needs the content drop loaded first (`seed_content_drop.py`): the products are
catalogue templates (each takes its product line from its template), the
suppliers are the drop's producers, prices follow the catalogue's EU should-cost
index, and the strategy rows point at playbook levers.

The owner (default `demo_buyer.OWNER_EMAIL`) must already be a user: sign in
once, or provision the account (`scripts/ops/provision_user.py --staff`).
`scripts/ops/rebuild_env.py build --demo-buyer --demo-owner EMAIL` passes
`--owner-email` here and reads the `total changes: <n>` line of the dry run.

One transaction: commits once at the end, or rolls back on --dry-run or on any
error. Idempotent: a second run reports zero changes. Writes team rows (and the
three fictional colleague users) only — never a platform row.
"""
import argparse
import dataclasses
import sys

import app.models  # noqa: F401  (registers every mapper before the first query)
from app.database import SessionLocal, bypass_rls_var
from app.services import demo_buyer
from app.services.drop.report import LoadReport


def run(dry_run: bool = False, do_reset: bool = False,
        config: demo_buyer.DemoBuyerConfig = demo_buyer.DEFAULT_CONFIG) -> int:
    # A seed writes as no one in particular: bypass RLS like the other seed_*.py
    # scripts. Restored on exit so an in-process caller does not inherit it.
    token = bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        if do_reset:
            reset_report = LoadReport(title="demo buyer · reset")
            removed = demo_buyer.reset(db, reset_report, config)
            print(reset_report.render(dry_run=dry_run))
            print(f"removed {removed} team(s) named {config.team_name!r}\n")

        report = LoadReport(title="demo buyer")
        result = demo_buyer.seed(db, report, config)
        print(report.render(dry_run=dry_run))

        print("\nStory check (illustrative): should-cost vs actual, EUR/t")
        for key, rows in result.story.items():
            if key not in demo_buyer.DRIFT:
                continue
            pid, supplier, site = key
            print(f"  {pid} · {supplier} → {site}")
            for year, quarter, should, actual in rows[-6:]:
                gap = (actual - should) / should * 100
                print(f"    {year}Q{quarter}  should-cost {should:8.2f}  actual {actual:8.2f}"
                      f"  gap {gap:+5.1f}%")

        overdue = demo_buyer.overdue_actions(db, result.team_id)
        print(f"\nOverdue actions on {demo_buyer.DEMO_TODAY}: {len(overdue)}")

        if dry_run:
            db.rollback()
            print("\nDRY RUN — rolled back, nothing written.")
        else:
            db.commit()
            print("\nCommitted.")
        print(f"\nTeam: {config.team_name}  id={result.team_id}")
        print("Switch the browser to it: pick it in the Team menu, or in the console run")
        print(f"  localStorage.setItem('ca_active_team', '{result.team_id}'); location.reload()")
        return 0
    except demo_buyer.DemoSeedError as exc:
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        bypass_rls_var.reset(token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="do the work, print the diff, then roll back")
    parser.add_argument("--reset", action="store_true",
                        help="delete the demo team (and its fictional colleagues) first")
    parser.add_argument("--owner-email", metavar="EMAIL",
                        default=demo_buyer.DEFAULT_CONFIG.owner_email,
                        help="the demo team's owner, an existing user "
                             f"(default {demo_buyer.DEFAULT_CONFIG.owner_email})")
    args = parser.parse_args(argv)
    if not args.owner_email.strip():
        parser.error("--owner-email must not be empty")
    config = dataclasses.replace(demo_buyer.DEFAULT_CONFIG, owner_email=args.owner_email.strip())
    return run(dry_run=args.dry_run, do_reset=args.reset, config=config)


if __name__ == "__main__":
    raise SystemExit(main())
