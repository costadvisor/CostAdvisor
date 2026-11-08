"""The monthly news sweep (Wave 4, SCRUM-214 / NEWS-2).

Once a month (or on demand for one family) the sweep goes through the search
profiles and stores what is new:

1. **Plan.** Every *active* family profile, plus each product-line override
   under it. A manual run for one family runs that family even when its
   profile is not active yet — that is how an editor tries it for real.
2. **Read the free feeds once** (RSS/Atom and SEC; ``news_feeds``).
3. **For each profile:** check the spend cap, search with Claude (the same
   code as the SCRUM-213 test run), match the feed entries, and store the
   candidates. Each profile is committed on its own, so whatever was done
   survives a later failure or a stop at the cap.
4. **Close the run log**: counts, tokens, dollars, errors, per-profile detail.

Duplicates are never stored twice: the same link (normalised) is unique per
family in the database, and a headline nearly identical to one this family
already has — the same story on another site — is counted as a duplicate.
That is also why re-running a month adds nothing new.

The spend cap is checked before each profile against what that profile could
cost at most, so a run stops *before* going over rather than after.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.chemical_family import ChemicalFamily
from app.models.news import (
    ITEM_UNSORTED, RUN_COMPLETED, RUN_FAILED, RUN_RUNNING, RUN_STOPPED_AT_CAP,
    NewsFeed, NewsItem, NewsRun,
)
from app.models.product_line import ProductLine
from app.models.search_profile import SearchProfile
from app.services import anthropic_client as claude
from app.services.news_feeds import FeedEntry, FeedError, fetch_feed, match_entries
from app.services.news_search import load_brief_context, normalize_url, search_news
from app.services.search_profiles import get_effective_profile

logger = logging.getLogger(__name__)

# A run "running" for longer than this is treated as dead (worker restarted).
STALE_RUN = timedelta(hours=3)
# Near-duplicate headlines: how alike, and how far back to compare.
HEADLINE_SIMILARITY = 0.9
DUPLICATE_LOOKBACK = timedelta(days=120)
# Upper estimate of one profile's token cost, on top of its searches (USD).
TOKEN_ALLOWANCE_PER_PROFILE = 0.10

_WORD = re.compile(r"[a-z0-9]+")


class SweepAlreadyRunning(RuntimeError):
    pass


@dataclass
class Target:
    family_id: int
    product_line_id: int | None
    label: str


# ── Planning ─────────────────────────────────────────────────────────────────

def plan_targets(db: Session, family_id: int | None = None) -> list[Target]:
    """Active family profiles (or the one family asked for) and their line overrides."""
    q = db.query(SearchProfile).filter(SearchProfile.product_line_id.is_(None))
    if family_id is not None:
        q = q.filter(SearchProfile.family_id == family_id)
    else:
        q = q.filter(SearchProfile.is_active.is_(True))
    bases = q.all()
    names = {f.id: f.name for f in db.query(ChemicalFamily)
             .filter(ChemicalFamily.id.in_([b.family_id for b in bases] or [-1])).all()}
    targets = []
    for base in sorted(bases, key=lambda b: names.get(b.family_id, "")):
        fam = names.get(base.family_id, f"family {base.family_id}")
        targets.append(Target(base.family_id, None, fam))
        overrides = (db.query(SearchProfile, ProductLine)
                     .join(ProductLine, ProductLine.id == SearchProfile.product_line_id)
                     .filter(SearchProfile.family_id == base.family_id,
                             ProductLine.retired_at.is_(None))
                     .order_by(ProductLine.name).all())
        targets += [Target(base.family_id, line.id, f"{fam} › {line.name}") for _, line in overrides]
    return targets


def start_run(db: Session, *, trigger: str, family_id: int | None = None,
              started_by: uuid.UUID | None = None, spend_cap: float | None = None,
              now: datetime | None = None) -> NewsRun:
    """Create the run row. Refuses while another run is in progress."""
    now = now or datetime.now(timezone.utc)
    busy = (db.query(NewsRun)
            .filter(NewsRun.status == RUN_RUNNING, NewsRun.started_at > now - STALE_RUN).first())
    if busy is not None:
        raise SweepAlreadyRunning(f"a news run started at {busy.started_at:%H:%M} UTC is still going")
    run = NewsRun(period=now.strftime("%Y-%m"), trigger=trigger, family_id=family_id,
                  started_by=started_by, status=RUN_RUNNING, started_at=now,
                  spend_cap_usd=spend_cap if spend_cap is not None else get_settings().news_run_spend_cap_usd,
                  errors=[], details=[])
    db.add(run)
    db.flush()
    return run


# ── Duplicates and storing ───────────────────────────────────────────────────

def headline_key(headline: str) -> str:
    return " ".join(_WORD.findall(headline.casefold()))[:400]


def _near_duplicate(key: str, known: list[str]) -> bool:
    if len(key) < 12:                     # too short to judge: only exact links count
        return False
    return any(SequenceMatcher(None, key, k).ratio() >= HEADLINE_SIMILARITY for k in known)


def _known_headlines(db: Session, family_id: int, now: datetime) -> tuple[set[str], list[str]]:
    rows = (db.query(NewsItem.url_norm, NewsItem.headline_key)
            .filter(NewsItem.family_id == family_id, NewsItem.found_at > now - DUPLICATE_LOOKBACK).all())
    return {r[0] for r in rows}, [r[1] for r in rows]


def store_items(db: Session, run: NewsRun, target: Target, candidates: list[dict],
                now: datetime) -> tuple[int, int]:
    """Store what is new for this family. Returns (new, duplicate)."""
    urls, keys = _known_headlines(db, target.family_id, now)
    new = dup = 0
    for c in candidates:
        url_norm = normalize_url(c["url"])[:1000]
        key = headline_key(c["headline"])
        if url_norm in urls or _near_duplicate(key, keys):
            dup += 1
            continue
        item = NewsItem(
            family_id=target.family_id, product_line_id=target.product_line_id, run_id=run.id,
            url=c["url"], url_norm=url_norm, headline=c["headline"][:400], headline_key=key,
            source=(c.get("source") or None) and c["source"][:200], origin=c["origin"],
            published_date=c.get("published_date"), date_source=c.get("date_source"),
            summary=c.get("summary"), source_excerpt=c.get("source_excerpt"),
            search_relevance=c.get("search_relevance"), search_event_type=c.get("search_event_type"),
            status=ITEM_UNSORTED, found_at=now,
        )
        try:
            with db.begin_nested():       # a concurrent insert of the same link is a duplicate too
                db.add(item)
                db.flush()
        except IntegrityError:
            dup += 1
            continue
        urls.add(url_norm)
        keys.append(key)
        new += 1
    return new, dup


def _search_candidates(outcome) -> list[dict]:
    return [{
        "headline": i.headline, "url": i.url, "source": i.source, "origin": "web_search",
        "published_date": i.published_date, "date_source": i.date_source, "summary": i.summary,
        "source_excerpt": None, "search_relevance": i.relevance, "search_event_type": i.event_type,
    } for i in outcome.kept + outcome.below_threshold]


# ── The run ──────────────────────────────────────────────────────────────────

def read_feeds(db: Session, run: NewsRun, http_client=None) -> list[FeedEntry]:
    entries = []
    for feed in db.query(NewsFeed).filter(NewsFeed.enabled.is_(True)).order_by(NewsFeed.id).all():
        try:
            got = fetch_feed(feed, http_client)
            feed.last_error = None
            entries += got
        except FeedError as e:
            feed.last_error = str(e)[:500]
            run.errors = [*run.errors, {"label": f"feed: {feed.name}", "message": str(e)[:500]}]
        feed.last_fetched_at = datetime.now(timezone.utc)
    run.feed_entries_read = len(entries)
    return entries


def execute_run(db: Session, run: NewsRun, *, today: date | None = None,
                http_client=None, feed_client=None) -> NewsRun:
    """Do the work of a run created by ``start_run``. Commits as it goes."""
    settings = get_settings()
    today = today or datetime.now(timezone.utc).date()
    searches_per_profile = settings.news_sweep_searches_per_profile
    worst_case = searches_per_profile * claude.WEB_SEARCH_USD_PER_SEARCH + TOKEN_ALLOWANCE_PER_PROFILE
    try:
        targets = plan_targets(db, run.family_id)
        run.profiles_planned = len(targets)
        entries = read_feeds(db, run, feed_client)
        db.commit()

        for t in targets:
            if run.cost_usd + worst_case > run.spend_cap_usd:
                run.status = RUN_STOPPED_AT_CAP
                done = run.profiles_run
                run.errors = [*run.errors, {"label": t.label, "message": (
                    f"Stopped before this profile: ${run.cost_usd:.2f} spent of the ${run.spend_cap_usd:.2f} "
                    f"cap, and one more profile can cost up to ${worst_case:.2f}. "
                    f"{done} of {run.profiles_planned} profiles were run.")}]
                break
            detail = {"label": t.label, "family_id": t.family_id, "product_line_id": t.product_line_id,
                      "searches": 0, "found": 0, "new": 0, "duplicate": 0, "dropped": {}, "cost_usd": 0.0}
            now = datetime.now(timezone.utc)
            profile = get_effective_profile(db, t.family_id, t.product_line_id)
            candidates: list[dict] = []
            if claude.is_configured():
                outcome = search_news(profile, load_brief_context(db, profile),
                                      max_searches=searches_per_profile, today=today,
                                      http_client=http_client)
                u = outcome.usage
                run.searches += u.web_searches
                run.input_tokens += u.input_tokens
                run.output_tokens += u.output_tokens
                run.cost_usd = round(run.cost_usd + u.cost_usd, 6)
                detail.update(searches=u.web_searches, cost_usd=round(u.cost_usd, 6))
                if outcome.error:
                    run.errors = [*run.errors, {"label": t.label, "message": outcome.error[:500]}]
                candidates += _search_candidates(outcome)
                for item in outcome.dropped:
                    detail["dropped"][item.drop_reason] = detail["dropped"].get(item.drop_reason, 0) + 1
            elif not run.errors or run.errors[-1].get("label") != "Claude":
                run.errors = [*run.errors, {"label": "Claude", "message":
                                            "ANTHROPIC_API_KEY is not set: only the free feeds were read."}]
            matches = match_entries(entries, profile, today)
            candidates += matches.items
            for reason, n in matches.dropped.items():
                detail["dropped"][reason] = detail["dropped"].get(reason, 0) + n

            new, dup = store_items(db, run, t, candidates, now)
            dropped = sum(detail["dropped"].values())
            detail.update(found=len(candidates), new=new, duplicate=dup)
            run.items_found += len(candidates)
            run.items_new += new
            run.items_duplicate += dup
            run.items_dropped += dropped
            run.profiles_run += 1
            run.details = [*run.details, detail]
            db.commit()

        if run.status == RUN_RUNNING:
            run.status = RUN_COMPLETED
    except Exception as e:  # noqa: BLE001 — the run log must say what happened
        logger.exception("news run %s failed", run.id)
        db.rollback()
        run = db.get(NewsRun, run.id)
        run.status = RUN_FAILED
        run.errors = [*run.errors, {"label": "run", "message": f"{type(e).__name__}: {e}"[:500]}]
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    return run


def run_sweep(db: Session, **kwargs) -> NewsRun:
    """Start and execute a run in one go (scripts, the scheduled task)."""
    start_keys = {"trigger", "family_id", "started_by", "spend_cap"}
    run = start_run(db, **{k: v for k, v in kwargs.items() if k in start_keys})
    db.commit()
    return execute_run(db, run, **{k: v for k, v in kwargs.items() if k not in start_keys})
