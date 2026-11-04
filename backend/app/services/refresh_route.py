"""Refresh route: how does each index get new values? (Jira 209)

The ticket's first requirement is that every index has a defined update
method. Nothing in the schema says which one an index has — it is implied by
four different places (the content drop's `commodity_key`, the scraper
registry, `proxy_logic`, the source label on stored values) — so this reads
them together and gives every index exactly ONE route:

    composite      computed live from other indexes; nothing to refresh
    content_drop   loaded from Laurent's content repo; refreshed only when the
                   drop is re-extracted and re-loaded (a manual step by design)
    proxy          derived from another index by an executable `proxy_logic`
    scraper        a registered scraper with `scrape_enabled`, run by the
                   weekly `scrape_all` job
    manual_upload  has values written by the super-admin upload
    none           nothing will ever update it

Team sources and provider feeds are per team, not per index, so they are
reported beside the route (`team_sources`) rather than as one.

Each route carries the interval at which new data is expected, which is what
"late" is judged against (209 S4).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.index_data import CommodityIndex, IndexValue, TeamIndexSource
from app.services.proxy_derivation import derivation_spec
from app.services.scraper import SCRAPER_REGISTRY, SCRAPER_SOURCE_LABELS
from app.services.series_health import frequency_interval_months

COMPOSITE = "composite"
CONTENT_DROP = "content_drop"
PROXY = "proxy"
SCRAPER = "scraper"
MANUAL_UPLOAD = "manual_upload"
NONE = "none"

ROUTES = (CONTENT_DROP, SCRAPER, PROXY, MANUAL_UPLOAD, COMPOSITE, NONE)

# PROPOSED (Jira 209). How often the content drop is expected to be re-extracted
# and re-loaded. Its series carry their own frequency, but none of them can be
# newer than the last drop, so the drop's cadence is what they are judged by.
CONTENT_DROP_INTERVAL_MONTHS = 3


@dataclass
class RefreshRoute:
    commodity_id: int
    name: str
    commodity_key: str | None
    route: str
    source: str | None              # scraper source label, "content repo", base index ...
    detail: str
    frequency: str | None
    expected_interval_months: int | None
    team_sources: int = 0           # teams with their own scrape / provider source for it


def _interval(frequency: str | None) -> int | None:
    """Months between new observations, from the stated frequency (free text
    in the drop; the leading word decides — see series_health)."""
    return frequency_interval_months(frequency)


def _route_for(s: CommodityIndex, uploaded: set[int]) -> tuple[str, str | None, str, int | None]:
    """(route, source, detail, expected interval) for one index, most specific first."""
    if s.composite_expression:
        return COMPOSITE, None, "computed live from other indexes", None
    if s.commodity_key:
        return (CONTENT_DROP, "content repo",
                "re-extract and re-load the content drop to refresh",
                CONTENT_DROP_INTERVAL_MONTHS)
    spec, _why = derivation_spec(s)
    if spec is not None:
        return PROXY, str(spec.get("base_index")), f"derived: {spec.get('operation')}", _interval(s.frequency)
    if s.name in SCRAPER_REGISTRY and s.scrape_enabled:
        label = SCRAPER_SOURCE_LABELS.get(s.name, "auto")
        # Scrapers write the quarterly store, so a quarter is the most a missing
        # frequency can mean for them.
        return (SCRAPER, label, f"weekly scrape_all via {SCRAPER_REGISTRY[s.name].__name__}",
                _interval(s.frequency) or 3)
    if s.id in uploaded:
        return MANUAL_UPLOAD, "admin upload", "values come from the super-admin upload", _interval(s.frequency)
    why = ("a scraper is registered but scrape_enabled is off" if s.name in SCRAPER_REGISTRY
           else "no scraper, content source, proxy spec or upload")
    return NONE, None, why, None


def refresh_routes(db: Session) -> list[RefreshRoute]:
    """Every index (FX included) with its one route, ordered by name."""
    uploaded = {
        cid for (cid,) in db.query(IndexValue.commodity_id)
        .filter(IndexValue.source == "admin_upload").distinct()
    }
    team_counts = dict(
        db.query(TeamIndexSource.commodity_id, func.count(func.distinct(TeamIndexSource.team_id)))
        .filter(TeamIndexSource.source_type.in_(("scrape_url", "provider_credential")))
        .group_by(TeamIndexSource.commodity_id)
        .all()
    )
    out = []
    for s in db.query(CommodityIndex).order_by(CommodityIndex.name):
        route, source, detail, interval = _route_for(s, uploaded)
        out.append(RefreshRoute(
            commodity_id=s.id, name=s.name, commodity_key=s.commodity_key,
            route=route, source=source, detail=detail, frequency=s.frequency,
            expected_interval_months=interval, team_sources=int(team_counts.get(s.id, 0)),
        ))
    return out


def route_counts(rows: list[RefreshRoute]) -> dict[str, int]:
    counts = Counter(r.route for r in rows)
    return {route: counts.get(route, 0) for route in ROUTES}
