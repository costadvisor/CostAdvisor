"""Merge rules for news search profiles (Wave 4, SCRUM-213 / NEWS-1).

Kept free of any DB code so the rules are easy to test and live in one place.

Rules:
  * most list fields are ADDITIVE: a subfamily's extra terms, exclusions,
    sites, producers and regions are added to the family's.
  * event_types REPLACES when the subfamily sets it, so it can narrow the
    event types it cares about. Empty on the family profile = all types.
  * single values (language, min_relevance, lookback_days) REPLACE when set.
  * a site that is both preferred and blocked counts as blocked.
"""
from __future__ import annotations

from app.schemas.search_profile import EVENT_TYPES, ProfileFields

DEFAULT_LANGUAGE = "en"
DEFAULT_MIN_RELEVANCE = 0.6
DEFAULT_LOOKBACK_DAYS = 31

ADDITIVE_FIELDS = (
    "terms",
    "synonyms",
    "cas_numbers",
    "exclude_terms",
    "preferred_sites",
    "blocked_sites",
    "producer_ids",
    "region_codes",
)
# regions.code is case-sensitive, so it is matched exactly; free text is not.
EXACT_MATCH_FIELDS = {"producer_ids", "region_codes"}
SCALAR_DEFAULTS = {
    "language": DEFAULT_LANGUAGE,
    "min_relevance": DEFAULT_MIN_RELEVANCE,
    "lookback_days": DEFAULT_LOOKBACK_DAYS,
}


def _union(a: list, b: list, *, casefold: bool = True) -> list:
    out, seen = [], set()
    for v in [*a, *b]:
        key = v.casefold() if casefold and isinstance(v, str) else v
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def merge_fields(base: ProfileFields, override: ProfileFields | None = None) -> dict:
    """Return the merged settings as a dict ready for EffectiveProfile(**...)."""
    override = override or ProfileFields()
    merged: dict = {}

    for name in ADDITIVE_FIELDS:
        merged[name] = _union(getattr(base, name), getattr(override, name),
                              casefold=name not in EXACT_MATCH_FIELDS)

    merged["event_types"] = list(override.event_types or base.event_types or EVENT_TYPES)

    for name, default in SCALAR_DEFAULTS.items():
        value = getattr(override, name)
        if value is None:
            value = getattr(base, name)
        merged[name] = default if value is None else value

    blocked = set(merged["blocked_sites"])
    merged["preferred_sites"] = [s for s in merged["preferred_sites"] if s not in blocked]

    return merged
