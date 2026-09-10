"""Producer canonicalisation (Wave 3, SCRUM-77 / INT-3).

**Alias resolution is not a function**, and this module exists because three
measured facts make the obvious one-line version wrong:

1. **One raw string can name several companies.** 40 of the 901 distinct raw
   supplier names contain `" / "` ("Sinopec / PetroChina", "BASF SE / Hexion /
   INEOS Melamines"). A canonicalise-to-one-string helper collapses those into
   a fictional company; `resolve_raw_name` returns a **list**.
2. **`SUPPLIER_ALIASES.json` is a partial map.** Its 189 entries cover 185 of
   901 distinct names (20.5%) and 691 of 2,237 rows (30.9%). Treating it as
   *the* canonicalisation leaves most names unresolved and silently duplicated,
   so an unmapped name mints its own producer rather than being dropped — and
   the fact that it was unmapped is recorded.
3. **45 canonical values also appear as raw names**, so resolution needs a
   **fixpoint pass**: `"BASF SE" -> "BASF"` and `"BASF" -> ...` can chain, and
   one lookup would stop halfway. `_follow` walks the chain with a cycle guard.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.dimension import normalize_value
from app.models.producer import Producer, ProducerAlias, ProducerFormula

# The separator the source uses to name several companies in one string.
MULTI_SEPARATOR = " / "

# A parenthetical qualifies the company's product line, not the company:
# "BASF (Uvinul line)" is BASF. Stripped only for *matching*; the raw string is
# always preserved on the alias row so the qualifier is not lost.
_TRAILING_PAREN = " ("

# How deep an alias chain may go before we call it a cycle. The measured data
# needs 2; the guard exists so a bad decision-file import cannot hang a load.
MAX_ALIAS_HOPS = 8


def split_raw_name(raw: str) -> list[str]:
    """One raw supplier string -> the company names it actually asserts."""
    parts = [p.strip() for p in str(raw or "").split(MULTI_SEPARATOR)]
    return [p for p in parts if p]


def match_form(name: str) -> str:
    """The form used for lookup: normalised, with a trailing parenthetical
    dropped so a product-line qualifier does not fork the company."""
    text = str(name or "").strip()
    if _TRAILING_PAREN in text:
        text = text.split(_TRAILING_PAREN, 1)[0].strip()
    return normalize_value(text)


@dataclass
class ResolvedProducer:
    producer: Producer
    raw_value: str
    # The company name this part of the raw string resolved to.
    matched_name: str
    # True when the source string named several companies, so a reviewer can
    # see why one row produced N.
    from_split: bool
    # True when nothing in the alias map covered it and a producer was minted.
    minted: bool


def _follow(alias_map: dict[str, str], start: str) -> str:
    """Walk an alias chain to its fixpoint.

    `SUPPLIER_ALIASES` maps raw -> canonical, and 45 canonical values are
    themselves keys, so a single lookup lands mid-chain. Bounded, so a
    self-referential decision-file row cannot hang the load.
    """
    seen = {start}
    current = start
    for _ in range(MAX_ALIAS_HOPS):
        nxt = alias_map.get(current)
        if not nxt or nxt == current or nxt in seen:
            return current
        seen.add(nxt)
        current = nxt
    return current


def get_or_create_producer(
    db: Session, name: str, *, hq_country: str | None = None, source: str = "loader"
) -> tuple[Producer, bool]:
    normalized = normalize_value(name)
    row = db.query(Producer).filter(Producer.normalized_name == normalized).first()
    if row is not None:
        if hq_country and not row.hq_country:
            row.hq_country = hq_country
            db.flush()
        return row, False
    row = Producer(name=str(name).strip(), normalized_name=normalized,
                   hq_country=hq_country, source=source)
    db.add(row)
    db.flush()
    return row, True


def upsert_alias(
    db: Session, producer: Producer, raw_value: str, *, source: str = "loader"
) -> ProducerAlias:
    """Record a raw string as naming this producer.

    Keyed on the FULL normalised string, not the match form: keying on the
    match form meant "BASF (Uvinul line)" collided with the bare "BASF" row and
    the qualifier was silently dropped. Both rows now exist and both resolve,
    because `match_key` is what resolution reads.
    """
    normalized = normalize_value(raw_value)
    row = (
        db.query(ProducerAlias)
        .filter(ProducerAlias.normalized == normalized,
                ProducerAlias.producer_id == producer.id)
        .first()
    )
    if row is not None:
        return row
    row = ProducerAlias(producer_id=producer.id, raw_value=str(raw_value),
                        normalized=normalized, match_key=match_form(raw_value),
                        source=source)
    db.add(row)
    db.flush()
    return row


def resolve_raw_name(
    db: Session,
    raw: str,
    *,
    alias_map: dict[str, str] | None = None,
    create: bool = True,
    source: str = "loader",
) -> list[ResolvedProducer]:
    """Resolve one raw supplier string to the producers it names.

    Returns a list because a `" / "` string genuinely names several companies.
    An unmapped name mints its own producer (marked `minted`) rather than being
    dropped — the alias map covers under a third of the rows, so dropping the
    remainder would lose most of the data while reporting success.
    """
    parts = split_raw_name(raw)
    from_split = len(parts) > 1
    out: list[ResolvedProducer] = []
    normalised_map = {normalize_value(k): v for k, v in (alias_map or {}).items()}

    for part in parts:
        # Existing alias rows win — that is where a decision-file import lands.
        existing = (
            db.query(ProducerAlias)
            .filter(ProducerAlias.match_key == match_form(part))
            .all()
        )
        if existing:
            # Dedupe by company: several alias rows legitimately share one
            # `match_key` (the bare name, the qualified spellings, the whole
            # multi-company string), and they mostly point at the SAME producer.
            # Iterating the rows would return one ResolvedProducer per spelling.
            by_producer = {}
            for alias in existing:
                by_producer.setdefault(alias.producer_id, alias)
            for alias in by_producer.values():
                if create:
                    # Record THIS spelling too. Without it a qualified string
                    # ("BASF (Uvinul line)") resolves correctly but is never
                    # stored, so the qualifier is lost the moment the bare name
                    # was seen first.
                    upsert_alias(db, alias.producer, part,
                                 source="split" if from_split else "raw")
                out.append(ResolvedProducer(
                    producer=alias.producer, raw_value=str(raw),
                    matched_name=alias.producer.name, from_split=from_split,
                    minted=False,
                ))
            continue

        canonical_key = _follow(normalised_map, match_form(part))
        canonical = normalised_map.get(canonical_key) or normalised_map.get(
            normalize_value(part))
        minted_name = canonical or part
        was_mapped = canonical is not None

        if not create:
            continue
        producer, created = get_or_create_producer(db, minted_name, source=source)
        upsert_alias(db, producer, part,
                     source="split" if from_split else ("alias_map" if was_mapped else "raw"))
        if str(raw) != part:
            # Keep the whole original string resolvable too, so a later lookup
            # of the exact source value finds every company it named.
            upsert_alias(db, producer, raw, source="split")
        out.append(ResolvedProducer(
            producer=producer, raw_value=str(raw), matched_name=producer.name,
            from_split=from_split, minted=not was_mapped,
        ))
    return out


def upsert_producer_formula(
    db: Session,
    producer: Producer,
    *,
    subject_code: str,
    template_id: uuid.UUID | None = None,
    region: str | None = None,
    share: float | None = None,
    hq_country: str | None = None,
    regions_raw: list | None = None,
    tags: list | None = None,
    raw_name: str | None = None,
    source: str = "loader",
) -> tuple[ProducerFormula, bool]:
    """Link a producer to something it makes.

    Returns `(row, created)` so a loader's diff report can tell an insert from
    an upsert without a second existence query — without it the report claims
    every row as created on every run and a second run stops looking idempotent
    even though the data is.

    **`share = 0` is recorded as not disclosed**, not as a zero: 2,215 of 2,237
    source rows carry 0 and several notes say the breakdown is not public, so
    storing the number alone ships "BASF — 0% market share" to a customer.
    """
    disclosed = share is not None and float(share) > 0
    row = (
        db.query(ProducerFormula)
        .filter(ProducerFormula.producer_id == producer.id,
                ProducerFormula.subject_code == subject_code,
                ProducerFormula.region.is_(None) if region is None
                else ProducerFormula.region == region)
        .first()
    )
    if row is None:
        row = ProducerFormula(
            producer_id=producer.id, subject_code=subject_code,
            template_id=template_id, region=region,
            share_pct=float(share) if disclosed else None,
            share_disclosed=disclosed, hq_country=hq_country,
            regions_raw=regions_raw, tags=tags, raw_name=raw_name, source=source,
        )
        db.add(row)
        db.flush()
        return row, True

    row.template_id = template_id or row.template_id
    row.share_pct = float(share) if disclosed else None
    row.share_disclosed = disclosed
    row.hq_country = hq_country or row.hq_country
    if regions_raw is not None:
        row.regions_raw = regions_raw
    if tags is not None:
        row.tags = tags
    if raw_name is not None:
        row.raw_name = raw_name
    db.flush()
    return row, False


def producer_portfolio(db: Session, producer_id: uuid.UUID) -> list[ProducerFormula]:
    """What this producer makes — the question `Supplier` could never answer,
    because a supplier row only exists inside a team that buys from it."""
    return (
        db.query(ProducerFormula)
        .filter(ProducerFormula.producer_id == producer_id)
        .order_by(ProducerFormula.subject_code)
        .all()
    )
