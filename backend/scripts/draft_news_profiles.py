#!/usr/bin/env python
"""
Draft a news search profile for every family with Claude (SCRUM-213 sub-task 3).

Why this exists
---------------
Writing ~25 search profiles by hand is slow. This asks Claude to draft each one
from what the platform already knows (subfamilies, products, the producers in
producer_formulas, existing editorial text) and saves it as an *inactive*
``ai_draft`` for an editor to review in Curation -> News profiles.

Safe to re-run: a profile a person has edited is never overwritten (unless
--force), and drafts are saved inactive, so nothing is searched until an
editor switches it on.

How to run (from backend/, with ANTHROPIC_API_KEY set)
------------------------------------------------------
    python scripts/draft_news_profiles.py --dry-run           # show drafts, save nothing
    python scripts/draft_news_profiles.py --family F01        # one family, by code
    python scripts/draft_news_profiles.py                     # every family without a profile
    python scripts/draft_news_profiles.py --redraft           # also replace existing ai_drafts

Cost: one Claude call per family, no web search. Roughly a cent or two each.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.constants.trust import PROVENANCE_AI_DRAFT  # noqa: E402
from app.database import SessionLocal, bypass_rls_var  # noqa: E402
from app.models import ChemicalFamily  # noqa: E402,F401  (registers all models)
from app.services import anthropic_client as claude  # noqa: E402
from app.services.news_profile_drafts import draft_profile, save_draft  # noqa: E402
from app.services.search_profiles import get_profile_row  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--family", action="append", help="family code (e.g. F01); repeatable. Default: all")
    ap.add_argument("--dry-run", action="store_true", help="print drafts, save nothing")
    ap.add_argument("--redraft", action="store_true", help="replace existing ai_draft profiles too")
    ap.add_argument("--force", action="store_true", help="overwrite even profiles a person edited")
    args = ap.parse_args()

    if not claude.is_configured():
        print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 2

    bypass_rls_var.set(True)
    db = SessionLocal()
    total_cost = 0.0
    try:
        q = db.query(ChemicalFamily)
        if args.family:
            q = q.filter(ChemicalFamily.code.in_(args.family))
        families = q.order_by(ChemicalFamily.code.nullslast(), ChemicalFamily.name).all()
        if not families:
            print("No matching families.")
            return 1

        for fam in families:
            label = f"{fam.code or '—'} {fam.name}"
            existing = get_profile_row(db, fam.id)
            if existing is not None and not args.force:
                if existing.provenance != PROVENANCE_AI_DRAFT:
                    print(f"{label}: skipped (edited by a person)")
                    continue
                if not args.redraft:
                    print(f"{label}: skipped (already has a draft; --redraft to replace)")
                    continue
            try:
                d = draft_profile(db, fam.id)
            except claude.AnthropicError as e:
                print(f"{label}: FAILED — {e}")
                db.rollback()
                continue
            total_cost += d.usage.cost_usd
            f = d.fields
            print(f"{label}: terms={f.terms} synonyms={len(f.synonyms)} producers={len(f.producer_ids)} "
                  f"regions={f.region_codes} excludes={f.exclude_terms} (${d.usage.cost_usd:.3f})")
            for note in d.dropped:
                print(f"    not used: {note}")
            if args.dry_run:
                db.rollback()
                continue
            result = save_draft(db, fam.id, d, force=args.force)
            db.commit()
            print(f"    {result}")
    finally:
        db.close()
    print(f"Done. Claude cost ≈ ${total_cost:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
