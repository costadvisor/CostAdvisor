"""Free news sources for the monthly sweep (Wave 4, SCRUM-214 / NEWS-2).

Two kinds of ``NewsFeed``, both read as RSS or Atom:

* ``rss`` — trade press feeds and company press pages. An entry is news for a
  profile when it names one of the profile's search terms (terms, other names,
  CAS numbers) and none of its exclusions.
* ``sec`` — an SEC EDGAR company feed (that company's filings, e.g. 8-Ks).
  Tied to a producer: its filings are news for every profile that watches the
  producer, no keyword needed. EDGAR's Atom URL looks like
  ``https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<cik>&type=8-K&output=atom``
  and EDGAR requires a User-Agent with a contact email (``NEWS_HTTP_USER_AGENT``).

Feeds cost nothing to read, so every enabled feed is fetched once per run and
its entries are matched against every profile. Only the headline, link, date
and a short teaser (at most 280 characters, for sorting) are kept.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

from app.config import get_settings
from app.schemas.search_profile import EffectiveProfile
from app.services.news_search import (
    _domain_matches, _mentions, _parse_date, normalize_url, url_domain,
)
from app.services.news_profile_drafts import _plain_text

MAX_FEED_BYTES = 3_000_000
FEED_TIMEOUT = 20.0
EXCERPT_CHARS = 280


class FeedError(RuntimeError):
    pass


@dataclass
class FeedEntry:
    feed_id: int
    feed_name: str
    kind: str
    title: str
    url: str
    published: date | None = None
    excerpt: str = ""
    feed_family_id: int | None = None
    producer_id: object | None = None


@dataclass
class FeedMatches:
    items: list[dict] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child(el, name: str):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el, name: str) -> str:
    c = _child(el, name)
    return (c.text or "").strip() if c is not None and c.text else ""


def _feed_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date()       # RSS: "Tue, 04 Nov 2026 09:00:00 GMT"
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()   # Atom
    except ValueError:
        return _parse_date(value)


def parse_feed(content: bytes, feed) -> list[FeedEntry]:
    """RSS 2.0 or Atom, by element name rather than by trusting declarations."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise FeedError(f"not a readable RSS/Atom feed: {e}") from e

    entries = []
    for el in root.iter():
        kind = _local(el.tag)
        if kind == "item":                                   # RSS
            title, url = _text(el, "title"), _text(el, "link")
            published = _feed_date(_text(el, "pubdate") or _text(el, "date"))
            excerpt = _text(el, "description")
        elif kind == "entry":                                # Atom
            title = _text(el, "title")
            link = next((c for c in el if _local(c.tag) == "link"
                         and c.get("rel", "alternate") == "alternate"), None) or _child(el, "link")
            url = (link.get("href") if link is not None else "") or ""
            published = _feed_date(_text(el, "published") or _text(el, "updated"))
            excerpt = _text(el, "summary") or _text(el, "content")
        else:
            continue
        title = _plain_text(title)
        if not title or not url:
            continue
        entries.append(FeedEntry(
            feed_id=feed.id, feed_name=feed.name, kind=feed.kind, title=title[:400], url=url.strip(),
            published=published, excerpt=_plain_text(excerpt)[:EXCERPT_CHARS],
            feed_family_id=feed.family_id, producer_id=feed.producer_id,
        ))
    return entries


def fetch_feed(feed, client: httpx.Client | None = None) -> list[FeedEntry]:
    headers = {"User-Agent": get_settings().news_http_user_agent,
               "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}
    own = client is None
    http = client or httpx.Client(timeout=FEED_TIMEOUT, follow_redirects=True)
    try:
        resp = http.get(feed.url, headers=headers)
    except httpx.HTTPError as e:
        raise FeedError(f"could not fetch: {e}") from e
    finally:
        if own:
            http.close()
    if resp.status_code >= 400:
        raise FeedError(f"HTTP {resp.status_code}")
    if len(resp.content) > MAX_FEED_BYTES:
        raise FeedError("feed is larger than 3 MB")
    return parse_feed(resp.content, feed)


def match_entries(entries: list[FeedEntry], profile: EffectiveProfile, today: date) -> FeedMatches:
    """The entries that are news for this profile, as items ready to store."""
    since = today - timedelta(days=profile.lookback_days)
    terms = profile.search_terms
    watched = {str(p) for p in profile.producer_ids}
    out = FeedMatches()

    def drop(reason: str) -> None:
        out.dropped[reason] = out.dropped.get(reason, 0) + 1

    for e in entries:
        if e.feed_family_id is not None and e.feed_family_id != profile.family_id:
            continue                                         # a feed for another family
        text = f"{e.title} {e.excerpt}"
        if e.kind == "sec":
            if str(e.producer_id) not in watched:
                continue
        elif not any(_mentions(text, t) for t in terms):
            continue
        if any(_domain_matches(url_domain(e.url), b) for b in profile.blocked_sites):
            drop("blocked_site")
        elif any(_mentions(text, t) for t in profile.exclude_terms):
            drop("excluded_term")
        elif e.published and e.published < since:
            drop("outside_time_window")
        else:
            out.items.append({
                "headline": e.title, "url": e.url, "source": e.feed_name,
                "origin": "sec" if e.kind == "sec" else "feed",
                "published_date": e.published, "date_source": "reported" if e.published else None,
                "summary": None, "source_excerpt": e.excerpt or None,
                "search_relevance": None, "search_event_type": None,
            })
    return out

