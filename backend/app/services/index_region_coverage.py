"""Per-region sourcing facts for the index library (Scrum 57 follow-up).

**Read the scope note before extending this, because both the obvious plan and
the follow-up's own framing turned out to be wrong about the data.**

The original plan was "index metadata is region-agnostic, so add a
(commodity, region) table". That was declined: Unit 2 of the data drop already
put region on `IndexCard` rather than on the series, so the per-region facts
are stored. This adds no table; it is the read that was missing.

The follow-up's framing was that the series shows ONE representative region's
metadata for all of its regions. That is true of the pre-drop
`seed_index_metadata` path. It is **not** true of the drop-loaded series, and
the real situation is starker: measured live, `access_tier`, `frequency`,
`retrieval_status` and `free_source_name` are null on **all 121** of them, while
102 of 132 cards carry access and frequency and 107 carry an agency. So the
series level is not showing the wrong region's sourcing — it is showing none at
all, and every per-region fact the drop supplied is invisible.

That also means the Index Library's data-trust chip reads `retrieval_status`
and gets null for every drop series. Making that chip per-region is not
possible from this data: a card carries access/frequency/agency and no
`retrieval_status`. The gap is reported here rather than papered over.

A field the series does not state is `silent`, never a `disagreement` —
comparing a card against a null and calling it a conflict would have reported
102 phantom conflicts and buried the 2 real ones.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexCard
from app.services.drop.catalog_loader import REGION_MAP

# Card regions that are not regions. `multi` means the card deliberately spans
# several and a blank means the drop did not say — neither is a mapping
# failure, so neither is counted as one.
NON_REGIONS = {"multi", ""}

# The drop spells the sentinel out where REGION_MAP keys it as `GL`. Aliased
# here rather than in REGION_MAP itself: that map is shared with the catalog
# loader and `data_resolver`, and widening it would change how they resolve.
EXTRA_REGION_ALIASES = {"GLOBAL": "GLOBAL"}

# Which card field is compared against which series field. Explicit because the
# names differ on each side and a positional pairing would drift.
COMPARISONS = (
    ("access", "access_tier"),
    ("frequency", "frequency"),
    ("agency", "provider"),
)


def _norm(v) -> str:
    return str(v).strip().lower() if v is not None else ""


def map_region(card_region: str | None) -> str | None:
    """Card-region vocabulary -> the app's `regions.code`, or None.

    Reuses the drop loader's REGION_MAP rather than a second copy; the resolver
    already resolves card regions through it, so a divergence here would mean
    two answers to one question. None means "not one of ours" — `multi`, blank,
    or a code with no mapping — and the caller is told which.
    """
    if card_region is None:
        return None
    raw = card_region.strip()
    if raw.lower() in NON_REGIONS:
        return None
    key = raw.upper()
    return REGION_MAP.get(key) or EXTRA_REGION_ALIASES.get(key)


def coverage(db: Session) -> dict:
    """One entry per drop-loaded series, with every card sitting on it."""
    series = (
        db.query(CommodityIndex)
        .filter(CommodityIndex.commodity_key.isnot(None))
        .order_by(CommodityIndex.name)
        .all()
    )
    ids = [c.id for c in series]
    cards = (
        db.query(IndexCard)
        .filter(IndexCard.commodity_id.in_(ids))
        .order_by(IndexCard.feed_slug, IndexCard.region)
        .all()
    ) if ids else []

    grouped: dict[int, list[IndexCard]] = {}
    for card in cards:
        grouped.setdefault(card.commodity_id, []).append(card)

    entries = []
    n_sibling_conflicts = 0
    n_duplicate_defaults = 0
    n_unmapped_cards = 0
    n_fully_silent_series = 0
    n_cards_with_facts = 0

    for ci in series:
        own = grouped.get(ci.id, [])

        # Which series fields say nothing at all. A silent field is why the
        # cards beside it are the only source of that fact, and it is a
        # completely different problem from a field that says the wrong thing.
        silent = [
            series_field for _, series_field in COMPARISONS
            if getattr(ci, series_field) is None
        ]
        if len(silent) == len(COMPARISONS):
            n_fully_silent_series += 1

        # Cards disagreeing with EACH OTHER. This is the one that says the
        # representative choice loses information, because the series value is
        # itself one of the cards whenever it is stated at all.
        sibling_conflicts: set[str] = set()
        for card_field, _ in COMPARISONS:
            distinct = {_norm(getattr(c, card_field)) for c in own if getattr(c, card_field) is not None}
            if len(distinct) > 1:
                sibling_conflicts.add(card_field)

        card_rows = []
        for card in own:
            disagrees = []
            for card_field, series_field in COMPARISONS:
                card_value = getattr(card, card_field)
                series_value = getattr(ci, series_field)
                # Only a stated series value can be disagreed with.
                if card_value is None or series_value is None:
                    continue
                if _norm(card_value) != _norm(series_value):
                    disagrees.append(card_field)

            mapped = map_region(card.region)
            raw = (card.region or "").strip()
            if raw and raw.lower() not in NON_REGIONS and mapped is None:
                n_unmapped_cards += 1
            if any(getattr(card, f) is not None for f, _ in COMPARISONS):
                n_cards_with_facts += 1

            card_rows.append({
                "feed_key": card.feed_key,
                "feed_slug": card.feed_slug,
                "region": card.region,
                "region_label": card.region_label,
                "mapped_region": mapped,
                "region_is_span": raw.lower() == "multi",
                "access": card.access,
                "frequency": card.frequency,
                "agency": card.agency,
                "sourcing_note": card.sourcing_note,
                "is_default_region": card.is_default_region,
                "disagrees_with_series": disagrees,
            })

        defaults = sum(1 for c in own if c.is_default_region)
        if defaults > 1:
            n_duplicate_defaults += 1
        if sibling_conflicts:
            n_sibling_conflicts += 1

        entries.append({
            "commodity_id": ci.id,
            "commodity_key": ci.commodity_key,
            "name": ci.name,
            "category": ci.category,
            "series": {
                "retrieval_status": ci.retrieval_status,
                "access_tier": ci.access_tier,
                "frequency": ci.frequency,
                "provider": ci.provider,
                "free_source_name": ci.free_source_name,
            },
            "series_silent_fields": silent,
            "cards": card_rows,
            "n_cards": len(card_rows),
            "sibling_conflicts": sorted(sibling_conflicts),
            "duplicate_default_regions": defaults > 1,
        })

    return {
        "summary": {
            "series": len(entries),
            "cards": len(cards),
            # The headline. These series carry no sourcing metadata of their
            # own, so their cards are the only place it exists.
            "series_with_no_series_level_sourcing": n_fully_silent_series,
            "cards_carrying_sourcing_facts": n_cards_with_facts,
            "series_with_sibling_conflicts": n_sibling_conflicts,
            "series_with_duplicate_defaults": n_duplicate_defaults,
            "cards_with_unmapped_region": n_unmapped_cards,
            # Stated in the payload, not only in a docstring: a caller building
            # a per-region trust chip needs to know the data cannot support it.
            "retrieval_status_is_series_level": True,
        },
        "entries": entries,
    }
