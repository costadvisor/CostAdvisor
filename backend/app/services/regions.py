"""Region reference-data helpers + a safety net for the region FK (Scrum 56).

The region columns on cost_models / index_values / index_overrides /
team_index_sources / freight_lanes are now FKs to `regions.code`. Several write
paths still accept a free-text region from the user (AddIndexModal uppercases
arbitrary input; CSV upload preserves whatever the file had). To avoid a raw FK
violation (500) on a legitimate action, we auto-register any region code written
through the ORM via a single `before_flush` choke point, rather than threading a
get-or-create call into every handler (easy to miss one).

This keeps "region is a row" true for new data too: a typed-in region becomes a
top-level Region row an admin can later rename or re-parent via the CRUD API.
"""
from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.region import Region
from app.models.index_data import IndexValue, IndexOverride, TeamIndexSource
from app.models.cost_model import CostModel
from app.models.freight_lane import FreightLane

# model class -> the region-bearing column attributes on it
_REGION_ATTRS: dict[type, tuple[str, ...]] = {
    IndexValue: ("region",),
    IndexOverride: ("region",),
    TeamIndexSource: ("region",),
    CostModel: ("region", "destination_region"),
    FreightLane: ("origin_region", "destination_region"),
}

# Known stray spellings -> the canonical code. These once minted duplicate
# Region rows through this safety net (merged back by the rgc2b3c4d5e6
# migration); the alias rewrite stops them from ever coming back. Keys are
# matched case-insensitively (upper()).
_REGION_ALIASES: dict[str, str] = {
    "EU": "Europe",
    "ASIA": "Asia",
    "INDIA": "India",
    "BLOBAL": "GLOBAL",
    "GLOBSL": "GLOBAL",
}


def get_or_create_region(db: Session, code: str) -> Region:
    """Return the Region for `code`, creating a top-level one if it doesn't exist."""
    region = db.query(Region).filter(Region.code == code).first()
    if region is None:
        region = Region(code=code, name=code)
        db.add(region)
        db.flush()
    return region


def _region_refs_in_flush(session: Session) -> list[tuple[object, str, str]]:
    refs: list[tuple[object, str, str]] = []
    for obj in list(session.new) + list(session.dirty):
        attrs = _REGION_ATTRS.get(type(obj))
        if not attrs:
            continue
        for attr in attrs:
            value = getattr(obj, attr, None)
            if value:
                refs.append((obj, attr, value))
    return refs


@event.listens_for(Session, "before_flush")
def _ensure_regions_exist(session: Session, flush_context, instances) -> None:
    refs = _region_refs_in_flush(session)
    if not refs:
        return

    # Canonicalise before registering: an alias or a case-variant of an
    # existing code is rewritten onto the canonical row instead of minting a
    # near-duplicate region ('EUROPE' -> 'Europe', 'BLOBAL' -> 'GLOBAL').
    # Queries run on the live connection (not session.execute) so we don't
    # trigger a re-entrant autoflush while already inside a flush.
    conn = session.connection()
    candidates = {v for _, _, v in refs}
    lowered = {c.lower() for c in candidates}
    for _, alias_target in _REGION_ALIASES.items():
        lowered.add(alias_target.lower())
    existing = conn.execute(
        text("SELECT code FROM regions WHERE lower(code) = ANY(:codes)"),
        {"codes": list(lowered)},
    ).scalars().all()
    exact = set(existing)
    by_lower = {c.lower(): c for c in existing}

    def canonical(value: str) -> str:
        if value in exact:
            return value
        alias = _REGION_ALIASES.get(value.upper())
        if alias:
            return by_lower.get(alias.lower(), alias)
        return by_lower.get(value.lower(), value)

    to_register: set[str] = set()
    for obj, attr, value in refs:
        canon = canonical(value)
        if canon != value:
            setattr(obj, attr, canon)
        if canon not in exact:
            to_register.add(canon)

    if not to_register:
        return
    # Idempotent + race-safe. Rows the flush is about to insert reference
    # regions.code, so these region rows must land first — running the INSERT
    # here guarantees that ordering.
    stmt = (
        pg_insert(Region.__table__)
        .values([{"code": c, "name": c} for c in sorted(to_register)])
        .on_conflict_do_nothing(index_elements=["code"])
    )
    conn.execute(stmt)
