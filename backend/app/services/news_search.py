"""Searching the news for one profile (Wave 4, SCRUM-213 test run; reused by SCRUM-214).

Flow: effective profile -> a search brief -> Claude with the server-side web
search tool -> Claude reports what it found through a ``report_news`` tool
call (structured output) -> we check and filter the items in code.

Two things are deliberately done in code rather than trusted to the model:

* **Links are verified.** An item is only kept if its URL actually appeared in
  a web search result in this conversation, so a misremembered or invented
  link can never reach an editor.
* **The profile's hard rules are re-applied.** Blocked sites, excluded terms,
  the lookback window, wanted event types and the relevance threshold are all
  checked again, and every dropped item says why, so an editor tuning a
  profile can see what each rule is doing.

Only headline, link, date, source and our own short summary are kept — never
article text (the epic's copyright rule).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.models.producer import Producer
from app.models.region import Region
from app.models.chemical_family import ChemicalFamily
from app.models.product_line import ProductLine
from app.schemas.search_profile import EVENT_TYPES, EffectiveProfile
from app.services import anthropic_client as claude

REPORT_TOOL = "report_news"
MAX_CONTINUATIONS = 3
# A call with less time than this left is not worth starting.
MIN_CALL_SECONDS = 10
MAX_PRODUCERS_IN_BRIEF = 25
MAX_ALIASES_PER_PRODUCER = 2

EVENT_LABELS = {
    "new_plant": "new plants or lines opening",
    "capacity_change": "capacity expansions, cuts or restarts",
    "new_supplier": "new suppliers entering the market",
    "technology": "new technology or cheaper processes",
    "closure": "plant closures or exits",
    "regulation": "regulation, tariffs or trade measures",
    "m_and_a": "mergers, acquisitions and divestments",
}

REPORT_TOOL_DEF = {
    "name": REPORT_TOOL,
    "description": ("Report every candidate news item found, each with an honest relevance score, "
                    "plus a short note on what the searches turned up. Call exactly once, at the end."),
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "headline": {"type": "string", "description": "The headline as published."},
                        "url": {"type": "string", "description": "Exactly as it appeared in the search results."},
                        "published_date": {"type": ["string", "null"],
                                           "description": "YYYY-MM-DD. null if the page does not show a date: "
                                                          "report the item anyway."},
                        "source": {"type": "string", "description": "Publication or site name."},
                        "summary": {"type": "string", "description": "At most two sentences, in your own words. Never copy article text."},
                        "event_type": {"type": "string", "enum": [*EVENT_TYPES, "other"]},
                        "relevance": {"type": "number", "minimum": 0, "maximum": 1,
                                      "description": "Scored on the scale in your instructions; low scores are fine."},
                    },
                    "required": ["headline", "url", "source", "summary", "event_type", "relevance"],
                },
            },
            "notes": {"type": "string",
                      "description": "One or two sentences for the editor: what the searches mostly returned, "
                                     "and why there are few or no items if so (e.g. 'results were mostly from "
                                     "2025', 'mostly consumer boat content')."},
        },
        "required": ["items", "notes"],
    },
}

SYSTEM_PROMPT = """You are a market-intelligence researcher for CostAdvisor, a platform that helps \
industrial buyers understand what they should pay for chemicals and materials. You search the news \
for events that move supply, demand or cost for one product family.

Your job is to gather candidates, not to make the final call. Report every item that is plausibly \
relevant, each with an honest relevance score; the editor's settings decide what reaches a person, \
and low-scored items help them tune those settings. Leaving an item out hides it from that tuning, \
so when in doubt, report it with a lower score.

Relevance scale (be consistent):
- 0.85-1.0  a direct event for this family: a plant or line opening, closing, expanding or cutting; \
a producer bought, sold or merged; a new regulation, tariff or trade measure on it; a price or \
shortage event
- 0.6-0.85  clearly about this family but less direct: producer results or strategy that affect \
supply, demand shifts in its main end markets, new technology or process news
- 0.3-0.6  related but indirect: adjacent products, upstream raw materials, general market commentary
- below 0.3 only passing mentions; you may leave these out

Dates: report an item even when its page shows no date (published_date null). Leave out only items \
you can see are older than the time window.

Always leave out: advertisements, product listings, retail and consumer content, how-to articles, \
stock tips and share-price commentary. Prefer primary and trade sources over aggregators. Summaries \
must be in your own words, at most two sentences — never copy article text.

When you have finished searching, call the report_news tool exactly once with every candidate and a \
short note. Use URLs exactly as they appeared in your search results."""


@dataclass
class NewsItem:
    headline: str
    url: str
    source: str
    summary: str
    event_type: str
    relevance: float
    published_date: date | None = None
    date_source: str | None = None
    drop_reason: str | None = None


@dataclass
class SearchOutcome:
    kept: list[NewsItem] = field(default_factory=list)
    below_threshold: list[NewsItem] = field(default_factory=list)
    dropped: list[NewsItem] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    usage: claude.Usage = field(default_factory=claude.Usage)
    window_start: date | None = None
    error: str | None = None
    # How many distinct pages the searches returned, and Claude's own note on
    # them: so "0 items" always comes with a reason an editor can act on.
    pages_seen: int = 0
    claude_note: str | None = None


# ── The brief ────────────────────────────────────────────────────────────────

@dataclass
class BriefContext:
    family_name: str
    line_name: str | None
    producers: list[str]
    regions: list[str]
    subfamily_name: str | None = None


def load_brief_context(db: Session, profile: EffectiveProfile) -> BriefContext:
    family = db.get(ChemicalFamily, profile.family_id)
    line = db.get(ProductLine, profile.product_line_id) if profile.product_line_id else None
    producers = []
    if profile.producer_ids:
        rows = db.query(Producer).filter(Producer.id.in_(profile.producer_ids)).all()
        for p in sorted(rows, key=lambda r: r.name)[:MAX_PRODUCERS_IN_BRIEF]:
            aliases = sorted({a.raw_value for a in p.aliases if a.raw_value.casefold() != p.name.casefold()},
                             key=len)[:MAX_ALIASES_PER_PRODUCER]
            producers.append(f"{p.name} (also: {', '.join(aliases)})" if aliases else p.name)
    regions = []
    if profile.region_codes:
        rows = db.query(Region).filter(Region.code.in_(profile.region_codes)).all()
        regions = [r.name for r in rows]
    return BriefContext(
        family_name=family.name if family else f"family {profile.family_id}",
        line_name=line.name if line else None,
        subfamily_name=line.subfamily.name if line and line.subfamily else None,
        producers=producers, regions=regions,
    )


def build_brief(profile: EffectiveProfile, ctx: BriefContext, today: date, max_searches: int) -> str:
    since = today - timedelta(days=profile.lookback_days)
    subject = ctx.family_name
    if ctx.line_name:
        subject += f" — specifically the product line {ctx.line_name}"
        if ctx.subfamily_name:
            subject += f" (sub-family {ctx.subfamily_name})"
    lines = [
        f"Product family: {subject}",
        f"Time window: news published from {since.isoformat()} to {today.isoformat()}.",
    ]
    if profile.search_terms:
        lines.append(f"Names and identifiers it goes by: {', '.join(profile.search_terms)}")
    if ctx.producers:
        lines.append("Producers to watch: " + "; ".join(ctx.producers))
    if ctx.regions:
        lines.append(f"Regions that matter most: {', '.join(ctx.regions)}")
    lines.append("Kinds of events that matter: "
                 + "; ".join(EVENT_LABELS[e] for e in profile.event_types if e in EVENT_LABELS))
    if profile.preferred_sites:
        lines.append(f"Trusted sources to favour: {', '.join(profile.preferred_sites)}")
    if profile.exclude_terms:
        lines.append(f"Not relevant (ignore items about these): {', '.join(profile.exclude_terms)}")
    lines.append(f"Language of sources: {profile.language}")
    lines.append(f"\nYou may run up to {max_searches} web searches. Spread them across the "
                 "names, the main producers and the event types rather than repeating one query.")
    lines.append("Report every plausible candidate with an honest relevance score, including items "
                 "whose date you cannot see; the editor's settings decide what is kept.")
    return "\n".join(lines)


# ── Checking what came back ──────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower() or "https", host, path, parts.query, ""))


def url_domain(url: str) -> str:
    return urlsplit(url.strip()).netloc.lower().removeprefix("www.")


def _domain_matches(domain: str, rule: str) -> bool:
    return domain == rule or domain.endswith("." + rule)


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _mentions(text: str, term: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


def search_result_pages(content: list[dict]) -> dict[str, str | None]:
    """Every page the web search returned: normalised URL -> its page age."""
    pages: dict[str, str | None] = {}
    for block in content:
        if block.get("type") == "web_search_tool_result" and isinstance(block.get("content"), list):
            for r in block["content"]:
                if r.get("url"):
                    pages.setdefault(normalize_url(r["url"]), r.get("page_age"))
    return pages


_AGO = re.compile(r"^(\d+)\s+(hour|day|week|month|year)s?\s+ago$", re.IGNORECASE)
_AGO_DAYS = {"hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}
_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%B %Y")


def parse_page_age(value, today: date) -> date | None:
    """The search engine's page age ("April 30, 2025", "3 days ago", ...) as a date."""
    if not value:
        return None
    text = str(value).strip()
    m = _AGO.match(text)
    if m:
        return today - timedelta(days=int(m.group(1)) * _AGO_DAYS[m.group(2).lower()])
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text[:40], fmt).date()
        except ValueError:
            continue
    return _parse_date(text)


def search_queries(content: list[dict]) -> list[str]:
    return [b["input"]["query"] for b in content
            if b.get("type") == "server_tool_use" and b.get("name") == "web_search"
            and isinstance(b.get("input"), dict) and b["input"].get("query")]


def classify_items(raw_items: list[dict], profile: EffectiveProfile, pages: dict[str, str | None],
                   today: date) -> tuple[list[NewsItem], list[NewsItem], list[NewsItem]]:
    """Split reported items into kept / below threshold / dropped (with reasons).

    ``pages`` is what the web search actually returned (URL -> page age): an
    item whose link is not among them is dropped, and when Claude could not
    read a date, the page age stands in, so an old article without a visible
    date cannot pass as this month's news.
    """
    since = today - timedelta(days=profile.lookback_days)
    kept, below, dropped, urls_done = [], [], [], set()
    for raw in raw_items:
        try:
            item = NewsItem(
                headline=str(raw.get("headline") or "").strip()[:400],
                url=str(raw.get("url") or "").strip(),
                source=str(raw.get("source") or "").strip()[:200],
                summary=str(raw.get("summary") or "").strip()[:600],
                event_type=raw.get("event_type") if raw.get("event_type") in (*EVENT_TYPES, "other") else "other",
                relevance=max(0.0, min(1.0, float(raw.get("relevance") or 0))),
                published_date=_parse_date(raw.get("published_date")),
            )
        except (TypeError, ValueError):
            continue
        norm, domain = normalize_url(item.url), url_domain(item.url)
        if item.published_date:
            item.date_source = "reported"
        elif norm in pages:
            item.published_date = parse_page_age(pages[norm], today)
            item.date_source = "search_result" if item.published_date else None
        text = f"{item.headline} {item.summary}"
        if not item.headline or not item.url:
            item.drop_reason = "incomplete"
        elif norm in urls_done:
            item.drop_reason = "duplicate"
        elif norm not in pages:
            item.drop_reason = "link_not_in_search_results"
        elif any(_domain_matches(domain, b) for b in profile.blocked_sites):
            item.drop_reason = "blocked_site"
        elif any(_mentions(text, t) for t in profile.exclude_terms):
            item.drop_reason = "excluded_term"
        elif item.published_date and item.published_date < since:
            item.drop_reason = "outside_time_window"
        elif item.event_type != "other" and item.event_type not in profile.event_types:
            item.drop_reason = "event_type_not_wanted"
        urls_done.add(norm)

        if item.drop_reason:
            dropped.append(item)
        elif item.relevance < profile.min_relevance:
            below.append(item)
        else:
            kept.append(item)
    kept.sort(key=lambda i: i.relevance, reverse=True)
    below.sort(key=lambda i: i.relevance, reverse=True)
    return kept, below, dropped


# ── The search itself ────────────────────────────────────────────────────────

def search_news(profile: EffectiveProfile, ctx: BriefContext, *, max_searches: int,
                today: date | None = None, http_client=None,
                time_budget: float | None = None) -> SearchOutcome:
    """Run one profile's search. Makes API calls; never touches the database.

    ``time_budget`` (seconds) bounds the whole search. The profile test run
    sets it so an editor always gets an answer before the proxy in front of
    the API gives up (Cloudflare cuts requests off at about 100s): once 60%
    of the budget is used Claude is asked to report what it has, and no call
    may outlive the budget. The monthly sweep runs in the background and
    does not need one.
    """
    today = today or datetime.now(timezone.utc).date()
    started = time.monotonic()
    outcome = SearchOutcome(window_start=today - timedelta(days=profile.lookback_days))
    messages = [{"role": "user", "content": build_brief(profile, ctx, today, max_searches)}]
    all_content: list[dict] = []
    reported: list[dict] | None = None
    must_report = False

    for step in range(MAX_CONTINUATIONS + 1):
        remaining = None if time_budget is None else time_budget - (time.monotonic() - started)
        if remaining is not None and remaining < MIN_CALL_SECONDS:
            outcome.error = ("The search ran out of time before Claude reported. "
                             "Try again, or test with fewer searches.")
            break
        late = remaining is not None and remaining < time_budget * 0.4
        force_report = (must_report or late or step == MAX_CONTINUATIONS
                        or outcome.usage.web_searches >= max_searches)
        if force_report and messages[-1]["role"] == "assistant":
            messages.append({"role": "user", "content": "Now call report_news with what you found."})
        try:
            resp = claude.create_message(
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=[claude.web_search_tool(max(1, max_searches - outcome.usage.web_searches),
                                              profile.blocked_sites or None), REPORT_TOOL_DEF],
                tool_choice={"type": "tool", "name": REPORT_TOOL} if force_report else None,
                max_tokens=8000,
                client=http_client,
                timeout=remaining,
                retries=0 if time_budget is not None else 2,
            )
        except claude.AnthropicError as e:
            outcome.error = str(e)
            break
        outcome.usage.add(resp.usage)
        all_content.extend(resp.content)
        reported_input = resp.tool_input(REPORT_TOOL)
        if reported_input is not None:
            reported = reported_input.get("items") or []
            note = str(reported_input.get("notes") or "").strip()
            outcome.claude_note = note[:600] or None
            break
        # pause_turn: a long server-side search turn; end_turn without a report:
        # ask for the report. Either way, continue the same conversation.
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "pause_turn":
            messages.append({"role": "user", "content": "Now call report_news with what you found."})
            must_report = True

    outcome.queries = search_queries(all_content)
    pages = search_result_pages(all_content)
    outcome.pages_seen = len(pages)
    if outcome.claude_note is None:
        # No report (or no note in it): Claude's last words are the best explanation.
        text = "".join(b.get("text", "") for b in all_content if b.get("type") == "text").strip()
        outcome.claude_note = text[-600:] or None
    if reported is None:
        outcome.error = outcome.error or "Claude did not report any results."
        return outcome
    outcome.kept, outcome.below_threshold, outcome.dropped = classify_items(
        reported, profile, pages, today)
    return outcome
