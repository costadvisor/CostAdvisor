"""Index layer loader for the content drop (spec D3; design §3.2).

Loads the index layer idempotently, in FK order, then runs the generators
that read it:

    commodity_indexes          one row per FIDX series
        ├── index_monthly_values   the monthly actuals (from HISTORY_START) per
        │                          series + the FFORE forecast (from
        │                          FORECAST_START) where the series has one
        ├── type_codes             one per FCOVERED tag, all resolved
        ├── index_cards            one per live (card slug, region) in INDEXES
        └── index_dossiers         the rich INDEXES cards, via dossier_loader;
                                   producer roles written here (`dossier_parts`)
    index_seasonal_factors     generated (index_seasonality.recompute_all)
    volatility_calibrations    generated (recompute_volatility_calibration)

**FIDX / FFORE are the series master.** The Indexes page's own `IDX` / `FORE`
are an older copy that disagrees on 2 of 64 histories and 26 of 62 forecasts;
the live database wins by the programme's own rule (analysis §6). `INDEXES`
supplies the cards and dossiers only.

**Series rows are matched by `commodity_key` and nothing else.** The rows
seeded before any drop have no key; they are region-agnostic (region lives on
`index_values`) while the drop bakes region into the key, so a name match
would collapse `lab-eu` and `lab-in` onto one row and repoint every cost model
using it — the July loader's reasoning, unchanged. Those rows are never read
for matching and never written. A derived name that happens to collide with
one of them is disambiguated instead.

What it deliberately does not load:

* `INDEX_SEASONALITY` and the shipped volatility breakpoints — generated here
  from the series, never imported (SCRUM-69, DB-7).
* Card snapshots (`currentVal`, `change`, `volPct`, `cyclePos` …) — moments
  in time that recompute from the series (see `drop/dossier_loader.py`).
* `proxy_status` / `swap_priority` on type codes — the source no longer
  carries the direct-versus-proxy signal (analysis §4.4). NULL, not guessed.

Source metadata is taken as given. A series whose `INDEX_SOURCE_META` entry
has no `proxy` key (or `proxy: null`) gets no proxy note, and `freq` is stored
as authored, whatever its wording (for example an annual price "stepped" onto
months). The forecast's vintage is not in the files: see `FORECAST_VINTAGE`.

**Never commits.** `seed_content_drop.py` owns the transaction, so a dry run is
this call followed by a rollback and the report is what the database did.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.formula_template import FormulaTemplateComponent
from app.models.index_data import CommodityIndex
from app.models.index_dossier import IndexDossier, IndexProducerRole
from app.models.index_layer import IndexCard, IndexMonthlyValue, TypeCode
from app.models.index_seasonality import IndexSeasonalFactor
from app.models.producer import Producer, ProducerAlias, ProducerFormula
from app.services.drop.dossier_loader import (
    DOSSIER_FIELDS, DossierLoadReport, _load_one, has_dossier,
)
from app.services.drop.common import BASE_PERIOD, _apply, _clean
from app.services.drop.report import LoadReport, TableDiff
from app.services.content_drop.reader import indexes_file, raw
from app.services.index_dossier import (
    DEFAULT_MIN_POINTS, DEFAULT_RUNGS, METHOD_MOM_PCT_STDEV, _all_dispersions,
    active_calibration, build_ladder, recompute_volatility_calibration,
)
from app.services.index_seasonality import recompute_all
from app.services.producers import MULTI_SEPARATOR, match_form, resolve_raw_name, upsert_alias

# What the numbers are: an index level, base 100 = January 2023, never money.
# The same value the July loader and its tests use.
VALUE_KIND = "index_level"

# FIDX carries 42 monthly points from January 2023; FFORE the six months after
# June 2026. Neither file states its months, so the anchors are recorded here
# and the lengths are checked rather than assumed.
HISTORY_START = (2023, 1)
HISTORY_MONTHS = 42
FORECAST_START = (2026, 7)
FORECAST_MONTHS = 6

# The last actual month: what screens label "Source data to <month>".
DATA_AS_OF = (HISTORY_START[0] + (HISTORY_START[1] - 1 + HISTORY_MONTHS - 1) // 12,
              (HISTORY_START[1] - 1 + HISTORY_MONTHS - 1) % 12 + 1)

# When the FFORE forecast was made. FFORE carries no date, so, like the anchors
# above, the vintage is recorded here: the points are the forecast shipped
# with the September 2026 drop. Screens label the dashed line with it ("forecast
# points from the September 2026 vintage"; design §4.2) and import it from here.
# The load checks FFORE against the digest of the content this vintage was
# recorded for, and reports a mismatch (`CHECK_FORECAST_VINTAGE`): a refreshed
# forecast must not go on carrying the old label. On a mismatch, confirm the new
# vintage, then update both constants. Recompute the digest with
# `ffore_digest(reader.raw("FFORE"))`, not with an external hash of the file.
FORECAST_VINTAGE = (2026, 9)
FORECAST_VINTAGE_FFORE_SHA256 = "32d297059c0de6eb8c232c652095caa1393bf5b049c2bed44546b769c944d910"
CHECK_FORECAST_VINTAGE = "check: forecast vintage"

# Key suffixes that are regions (drop codes, as `REGION_MAP` keys them). Other
# suffixes are data sources or places (`-ppi`, `-wb`, `-mb`, `-au`, `-us`,
# `-asia`) and give no region.
REGION_SUFFIXES = {
    "eu": "EU", "na": "NA", "cn": "CN", "in": "IN",
    "apac": "APAC", "mea": "MEA", "la": "LA", "gl": "GL",
}

# A cost line tagged `fixed` has no index (analysis §4.4). It is the one tag
# that must never become a type code.
NOT_A_TYPE_CODE = "fixed"

# The regKeys region under which a card displays a series it does not own —
# `naphtha`, `cbfs` and `pta` all show `brent` as `multi`.
PROXY_REGION = "multi"

_MULTI_REGION_SUFFIX = re.compile(r"\s*[—·-]\s*multi-region\s*$", re.IGNORECASE)
_ACRONYMS = {
    "ppi": "PPI", "wpi": "WPI", "lng": "LNG", "jkm": "JKM", "lbma": "LBMA",
    "naoh": "NaOH", "kcl": "KCl", "wb": "WB", "mb": "MB", "au": "AU",
    "ca": "CA", "us": "US",
}

# `index_dossiers.access_tier` is String(32); some roleExtra access strings are
# far longer. Read off the column so a widening applies here without an edit.
ACCESS_TIER_MAX = IndexDossier.__table__.c.access_tier.type.length


@dataclass
class IndexLoadReport(LoadReport):
    """The harness report plus the findings that are not row counts (dead
    card keys, carries, generator outputs), printed after the totals."""
    notes: list[str] = field(default_factory=list)

    def render(self, *, dry_run: bool = False, skip_limit: int = 40) -> str:
        text = super().render(dry_run=dry_run, skip_limit=skip_limit)
        if not self.notes:
            return text
        return text + "\n\nNotes:\n" + "\n".join(f"  {n}" for n in self.notes)


def _month(start: tuple[int, int], offset: int) -> tuple[int, int]:
    index = start[1] - 1 + offset
    return start[0] + index // 12, index % 12 + 1


# ── Which card speaks for a series ───────────────────────────────────────────

@dataclass(frozen=True)
class CardRef:
    """One live `regKeys` entry: card `slug` shows `series_key` for `region`."""
    slug: str
    region: str
    series_key: str
    order: int  # position in INDEXES — the tie-break, so ownership is stable
    card: dict = field(compare=False, hash=False)

    @property
    def priority(self) -> int:
        """0 — the card is the series' own (`slug == key`); 1 — the card shows
        it as one of its regions; 2 — the card borrows it as a `multi` proxy.
        A proxy card must never name the series it borrows: `naphtha` shows
        `brent`, and `brent` is not naphtha."""
        if self.slug == self.series_key:
            return 0
        return 1 if self.region != PROXY_REGION else 2


def card_refs(cards: dict, series: dict) -> tuple[dict[str, list[CardRef]], list[tuple[str, str]]]:
    """Live card references by series key, and the dead ones as
    `(feed_key, series_key)` — regKeys naming a series FIDX does not carry."""
    live: dict[str, list[CardRef]] = defaultdict(list)
    dead: list[tuple[str, str]] = []
    for order, (slug, card) in enumerate(cards.items()):
        if not isinstance(card, dict):
            continue
        for region, key in (card.get("regKeys") or {}).items():
            if key in series:
                live[key].append(CardRef(slug, region, key, order, card))
            else:
                dead.append((f"{slug}|{region}", key))
    return dict(live), dead


def owner_card(refs: list[CardRef] | None) -> CardRef | None:
    if not refs:
        return None
    return min(refs, key=lambda r: (r.priority, r.order))


def humanise_key(key: str) -> str:
    """A readable name from a series key, for the series no card shows:
    `inorg-chem-ppi` -> "Inorg Chem PPI", `zinc-gl` -> "Zinc — Global". Words
    only — nothing is expanded that the key does not say."""
    tokens = key.split("-")
    region = tokens.pop() if len(tokens) > 1 and tokens[-1] in REGION_SUFFIXES else None
    words = " ".join(_ACRONYMS.get(t, t.capitalize()) for t in tokens)
    if region is None:
        return words
    return f"{words} — {'Global' if region == 'gl' else REGION_SUFFIXES[region]}"


def series_name(key: str, owner: CardRef | None) -> str:
    """The card's own name; with the region appended when the card fans out
    over several regional series (`LAB (Linear Alkylbenzene) — EU`)."""
    if owner is None:
        return humanise_key(key)
    name = str(owner.card.get("name") or "").strip() or humanise_key(key)
    if len(owner.card.get("regKeys") or {}) > 1:
        return f"{_MULTI_REGION_SUFFIX.sub('', name)} — {owner.region}"
    return name


def source_region(key: str) -> str | None:
    suffix = key.rsplit("-", 1)[-1] if "-" in key else None
    return REGION_SUFFIXES.get(suffix) if suffix else None


def _unique_name(wanted: str, key: str, taken: set[str]) -> str:
    for candidate in (wanted, f"{wanted} ({key})", key):
        if candidate not in taken:
            return candidate
    raise ValueError(f"no free name for series {key!r} (tried {wanted!r})")


def _proxy_logic(current: dict | None, note: str | None) -> dict | None:
    """The source's proxy prose as `proxy_logic.note` — the note-only shape
    `validate_proxy_logic` allows and `proxy_derivation` treats as a
    configuration state. Executable params an admin has added are kept."""
    kept = {k: v for k, v in (current or {}).items() if k != "note"}
    if note:
        kept["note"] = note
    return kept or None


# ── 1. The price series ──────────────────────────────────────────────────────

def _series_fields(key: str, values: list, meta: dict, owner: CardRef | None) -> dict:
    m = meta.get(key) or {}
    category = str(owner.card.get("cat") or "").split(" · ")[0].strip() if owner else ""
    return {
        "value_kind": VALUE_KIND,
        # Only where the series really is 100 at January 2023. `rutile` and
        # `ilmenite` start at 103 and 110; claiming that anchor for them would
        # make their levels look comparable to the rest when they are not.
        "base_period": BASE_PERIOD if values and values[0] == 100 else None,
        "source_region": source_region(key),
        "provider": _clean(m.get("agency")),
        "frequency": _clean(m.get("freq")),
        # The card's category word ("Base metal", "Linear Alkylbenzene") — the
        # vocabulary the Index Library folds into its colour families.
        "category": category or None,
    }


def _load_series(db: Session, fidx: dict, meta: dict,
                 refs: dict[str, list[CardRef]]) -> tuple[TableDiff, dict[str, int]]:
    """Upsert by `commodity_key`. The name is set on create only, so a rerun
    never renames a row a user has already seen."""
    diff = TableDiff("commodity_indexes")
    existing = {
        ci.commodity_key: ci
        for ci in db.query(CommodityIndex).filter(CommodityIndex.commodity_key.isnot(None))
    }
    taken = {name for (name,) in db.query(CommodityIndex.name)}

    for key in sorted(fidx):
        owner = owner_card(refs.get(key))
        fields = _series_fields(key, fidx[key], meta, owner)
        note = _clean((meta.get(key) or {}).get("proxy"))
        series = existing.get(key)
        if series is None:
            name = _unique_name(series_name(key, owner), key, taken)
            taken.add(name)
            series = CommodityIndex(
                name=name, commodity_key=key,
                # Not scraped by us — the values arrive with the drop.
                scrape_enabled=False,
                proxy_logic=_proxy_logic(None, note),
                **fields,
            )
            db.add(series)
            existing[key] = series
            diff.created += 1
            continue

        changes: list = []
        for name, value in fields.items():
            _apply(series, name, value, changes)
        _apply(series, "proxy_logic", _proxy_logic(series.proxy_logic, note), changes)
        diff.updated += 1 if changes else 0
        diff.unchanged += 0 if changes else 1

    # Keyed rows the drop no longer lists: reported, left in place.
    diff.stale = sum(1 for key in existing if key not in fidx)
    db.flush()
    return diff, {key: existing[key].id for key in fidx}


# ── 2. The numbers ───────────────────────────────────────────────────────────

def _desired_points(fidx: dict, ffore: dict, diff: TableDiff):
    """`(series_key, year, month) -> (value, kind)`. A series whose length is
    not the stated one is skipped whole: its months cannot be placed."""
    points: dict[tuple[str, int, int], tuple[float, str]] = {}
    for key, values in fidx.items():
        if len(values) != HISTORY_MONTHS:
            diff.skipped.append((key, f"{len(values)} actual points, expected {HISTORY_MONTHS}"))
            continue
        forecast = ffore.get(key) or []
        if forecast and len(forecast) != FORECAST_MONTHS:
            diff.skipped.append((key, f"{len(forecast)} forecast points, expected {FORECAST_MONTHS}"))
            forecast = []
        for start, run, kind in ((HISTORY_START, values, "actual"),
                                 (FORECAST_START, forecast, "forecast")):
            for i, value in enumerate(run):
                year, month = _month(start, i)
                if value is None:
                    diff.skipped.append((f"{key} {year}-{month:02d}", "no value in the drop"))
                    continue
                points[(key, year, month)] = (value, kind)
    return points


def _load_monthly(db: Session, fidx: dict, ffore: dict, key_to_id: dict[str, int]) -> TableDiff:
    diff = TableDiff("index_monthly_values")
    desired = _desired_points(fidx, ffore, diff)
    ids = list(key_to_id.values())
    existing = {
        (v.commodity_id, v.year, v.month): v
        for v in db.query(IndexMonthlyValue).filter(IndexMonthlyValue.commodity_id.in_(ids))
    }

    wanted: set[tuple[int, int, int]] = set()
    for (key, year, month), (value, kind) in desired.items():
        commodity_id = key_to_id[key]
        wanted.add((commodity_id, year, month))
        row = existing.get((commodity_id, year, month))
        if row is None:
            db.add(IndexMonthlyValue(commodity_id=commodity_id, year=year, month=month,
                                     value=value, kind=kind))
            diff.created += 1
            continue
        changes: list = []
        _apply(row, "value", value, changes)
        _apply(row, "kind", kind, changes)
        diff.updated += 1 if changes else 0
        diff.unchanged += 0 if changes else 1

    diff.stale = sum(1 for k in existing if k not in wanted)
    db.flush()
    return diff


# ── 3. The resolution join ───────────────────────────────────────────────────

def tag_usage(combos: dict) -> dict[str, dict]:
    """Per cost-line tag, measured off FORMULA_COMBOS: the label its lines
    most often carry (ties alphabetical), and the drop's own usage snapshot —
    products, lines, summed share."""
    labels: dict[str, Counter] = defaultdict(Counter)
    pids: dict[str, set] = defaultdict(set)
    lines: Counter = Counter()
    weight: dict[str, float] = defaultdict(float)
    for pid, record in combos.items():
        for combo in (record or {}).get("combos") or []:
            for line in combo.get("lines") or []:
                if not isinstance(line, list) or len(line) < 3 or not line[2]:
                    continue
                share, label, tag = line[0], line[1], line[2]
                if label:
                    labels[tag][str(label).strip()] += 1
                pids[tag].add(pid)
                lines[tag] += 1
                if isinstance(share, (int, float)) and not isinstance(share, bool):
                    weight[tag] += share
    usage = {}
    for tag in lines:
        ranked = sorted(labels[tag].items(), key=lambda kv: (-kv[1], kv[0]))
        usage[tag] = {
            "label": ranked[0][0] if ranked else None,
            "n_formulas": len(pids[tag]),
            "n_lines": lines[tag],
            "total_weight": round(weight[tag], 4),
        }
    return usage


def _load_type_codes(db: Session, fcovered: dict, key_to_id: dict[str, int],
                     usage: dict[str, dict]) -> TableDiff:
    diff = TableDiff("type_codes")
    existing = {tc.code: tc for tc in db.query(TypeCode)}

    for code in sorted(fcovered):
        if code.strip().lower() == NOT_A_TYPE_CODE:
            diff.skipped.append((code, "the literal 'fixed' means no index — never a type code"))
            continue
        target = fcovered[code]
        resolves_to_id = key_to_id.get(target)
        if resolves_to_id is None:
            diff.skipped.append((code, f"resolves to {target!r}, which is not a FIDX series"))
            continue
        used = usage.get(code) or {}
        fields = {
            "label": used.get("label"),
            "resolves_to_id": resolves_to_id,
            "resolution": "resolved",
            "source_n_formulas": used.get("n_formulas"),
            "source_n_lines": used.get("n_lines"),
            "source_total_weight": used.get("total_weight"),
        }
        tc = existing.get(code)
        if tc is None:
            db.add(TypeCode(code=code, **fields))
            diff.created += 1
            continue
        changes: list = []
        for name, value in fields.items():
            _apply(tc, name, value, changes)
        diff.updated += 1 if changes else 0
        diff.unchanged += 0 if changes else 1

    for code, tc in existing.items():
        if code in fcovered:
            continue
        if code.strip().lower() != NOT_A_TYPE_CODE:
            diff.stale += 1
            continue
        # A `fixed` row from an earlier load is removed — unless a cost line
        # still points at it, which is a catalogue problem to fix there first.
        referenced = db.query(FormulaTemplateComponent.id).filter(
            FormulaTemplateComponent.type_code_id == tc.id).first()
        if referenced is not None:
            diff.skipped.append((code, "the literal 'fixed' is not a type code, but "
                                       "cost lines still reference it — left in place"))
            continue
        db.delete(tc)
        diff.deleted += 1

    db.flush()
    return diff


# ── 4. The display layer ─────────────────────────────────────────────────────

def _load_cards(db: Session, cards: dict, meta: dict, key_to_id: dict[str, int],
                dead: list[tuple[str, str]]) -> TableDiff:
    """One card row per live `(slug, region)`. `region` is the drop's own code
    (`EU`, `multi`, `Global`), as the July cards stored it —
    `data_resolver` maps it through REGION_MAP, falling back to GLOBAL."""
    diff = TableDiff("index_cards")
    diff.skipped.extend(
        (feed_key, f"regKeys names {series_key!r}, which FIDX does not carry")
        for feed_key, series_key in dead
    )
    existing = {c.feed_key: c for c in db.query(IndexCard)}
    wanted: set[str] = set()

    for slug, card in cards.items():
        if not isinstance(card, dict):
            continue
        defaults = set(card.get("defaultRegs") or [])
        labels = card.get("regLabels") or {}
        for region, series_key in (card.get("regKeys") or {}).items():
            commodity_id = key_to_id.get(series_key)
            if commodity_id is None:
                continue  # dead — already reported
            feed_key = f"{slug}|{region}"
            wanted.add(feed_key)
            source = meta.get(series_key) or {}
            fields = {
                "feed_slug": slug,
                "commodity_id": commodity_id,
                "region": region,
                "region_label": _clean(labels.get(region)),
                "name": _clean(card.get("name")),
                "category": _clean(card.get("cat")),
                "access": _clean(card.get("access")),
                "frequency": _clean(card.get("freq")),
                "is_default_region": region in defaults,
                # The card carries no agency; the series' source does.
                "agency": _clean(source.get("agency")),
                "source_freq": _clean(source.get("freq")),
                "source_note": _clean(source.get("proxy")),
                "used_in_formulas": card.get("usedIn"),
            }
            row = existing.get(feed_key)
            if row is None:
                db.add(IndexCard(feed_key=feed_key, **fields))
                diff.created += 1
                continue
            changes: list = []
            for name, value in fields.items():
                _apply(row, name, value, changes)
            diff.updated += 1 if changes else 0
            diff.unchanged += 0 if changes else 1

    diff.stale = sum(1 for k in existing if k not in wanted)
    db.flush()
    return diff


# ── 5. The dossiers ──────────────────────────────────────────────────────────

def effective_payload(card: dict, region: str | None) -> dict:
    """The card as shown for one region: its `_regional[region]` override
    merged over the base (an override carries only what differs). A new dict —
    the parsed drop is shared and read-only."""
    override = (card.get("_regional") or {}).get(region) if region else None
    merged = {**card, **override} if isinstance(override, dict) else dict(card)
    merged.pop("_regional", None)
    return merged


def dossier_owners(cards: dict, series: dict):
    """Which card's dossier each series gets.

    `dossier_loader` targets the card's own series and files `_regional`
    overrides as region rows on it. In September the regional carriers fan out
    to region-baked series instead (`iron-scrap-na` shows `iron-scrap-eu` for
    EU; `natural-gas` spans seven series), so that rule would put the EU
    dossier on the NA series and 49 region rows on `natural-gas`'s seven. Here
    each `(region -> series)` gets the card merged with that region's
    override, as the series-wide row of the series it names.

    One owner per series, by `CardRef.priority` then source order; the losers
    are returned as conflicts, never written over the owner.

    Returns `(owners {series_key: (slug, payload)}, conflicts [(slug,
    series_key)], unmatched [slug])`.
    """
    claims = []
    unmatched = []
    for order, (slug, card) in enumerate(cards.items()):
        if not isinstance(card, dict) or not has_dossier(card):
            continue
        targets = [(region, key) for region, key in (card.get("regKeys") or {}).items()
                   if key in series]
        if slug in series and all(key != slug for _, key in targets):
            targets.append((None, slug))
        claimed = False
        for region, key in targets:
            payload = effective_payload(card, region)
            if not any(payload.get(f) for f in DOSSIER_FIELDS):
                continue  # this region has nothing dossier-shaped to say
            ref = CardRef(slug, region or "", key, order, card)
            claims.append((ref.priority, order, slug, key, payload))
            claimed = True
        if not claimed:
            unmatched.append(slug)

    owners: dict[str, tuple[str, dict]] = {}
    conflicts: list[tuple[str, str]] = []
    for _prio, _order, slug, key, payload in sorted(claims, key=lambda c: (c[0], c[1])):
        if key in owners:
            conflicts.append((slug, key))
            continue
        owners[key] = (slug, payload)
    return owners, conflicts, unmatched


def fit_access_tier(payload: dict) -> tuple[dict, str | None]:
    """The payload with an access string too long for `access_tier` removed,
    and that string. NULL rather than a truncated fragment; the card keeps
    its own full `access` on `index_cards`."""
    role_extra = payload.get("roleExtra") or {}
    access = role_extra.get("access") or payload.get("access")
    if not access or len(str(access)) <= ACCESS_TIER_MAX:
        return payload, None
    return {**payload, "roleExtra": {**role_extra, "access": None}, "access": None}, str(access)


# The two payload lists that name companies, and the role each one files.
PRODUCER_ROLE_FIELDS = (("producer", "producers"), ("price_setter", "priceSetters"))

# `producers.source` for a company first named by a dossier.
DOSSIER_PRODUCER_SOURCE = "dossier"


def without_producers(payload: dict) -> dict:
    """The payload `_load_one` is given: everything but the producer lists,
    which `_sync_producer_roles` writes (see there for why)."""
    lists = {field_name for _, field_name in PRODUCER_ROLE_FIELDS}
    return {k: v for k, v in payload.items() if k not in lists}


def dossier_parts(name: str) -> list[str]:
    """The companies one dossier producer string names: `split_raw_name`'s
    `" / "` split, but only outside parentheses.

    A parenthetical qualifies a company, it does not list companies:
    `Maker (Site A / Site B)` is Maker. Split naively it became
    `Maker (Site A` and `Site B)`, a producer called "Site B)", and an
    alias putting that producer behind plain "Maker". Every other string
    splits as before.
    """
    text = str(name or "")
    parts: list[str] = []
    depth = start = i = 0
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth = max(depth - 1, 0)
        elif depth == 0 and text.startswith(MULTI_SEPARATOR, i):
            parts.append(text[start:i])
            i += len(MULTI_SEPARATOR)
            start = i
            continue
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _resolve(db: Session, raw_name: str, aliases: dict[str, str], *,
             create: bool) -> list[Producer] | None:
    """The producers one dossier string names, each part through
    `resolve_raw_name` (alias rows first, then SUPPLIER_ALIASES, then a new
    producer), as the July dossier loader resolved them.

    A part whose separator sits inside its parenthetical is looked up by the
    name before the parenthetical — the same `match_key` — so the resolver
    does not split it again; the full part is recorded as an alias of what it
    found. A string naming several companies is recorded as an alias of each,
    as the resolver does, unless its `match_key` is one of its parts' — that
    alias would put every company in the string behind that one name, the
    "Peru)"-behind-"Glencore" fault. With `create=False`, None when a part
    resolves to no producer.
    """
    parts = dossier_parts(raw_name)
    whole = (len(parts) > 1
             and match_form(raw_name) not in {match_form(p) for p in parts})
    producers: list[Producer] = []
    for part in parts:
        lookup = part.split(" (", 1)[0].strip() if MULTI_SEPARATOR in part else part
        resolved = resolve_raw_name(db, lookup, alias_map=aliases, create=create,
                                    source=DOSSIER_PRODUCER_SOURCE)
        if not resolved:
            return None
        for r in resolved:
            if create and lookup != part:
                upsert_alias(db, r.producer, part, source="raw")
            if create and whole:
                upsert_alias(db, r.producer, raw_name, source="split")
            producers.append(r.producer)
    return producers


def _wanted_roles(db: Session, payload: dict, aliases: dict[str, str], *,
                  create: bool) -> list[dict] | None:
    """The producer-role rows the payload asks for, filled as
    `_replace_children` fills them: first `(producer, role)` wins, because a
    `" / "` string can repeat a company named separately in the same list and
    the unique constraint would reject the second. None when `create=False`
    and a named company has no producer — the roles cannot be current."""
    rows: list[dict] = []
    seen: set = set()
    for role, field_name in PRODUCER_ROLE_FIELDS:
        for i, el in enumerate(payload.get(field_name) or []):
            if not isinstance(el, dict) or not el.get("n"):
                continue
            producers = _resolve(db, el["n"], aliases, create=create)
            if producers is None:
                return None
            for producer in producers:
                if (producer.id, role) in seen:
                    continue
                seen.add((producer.id, role))
                share = el.get("share")
                rows.append({
                    "producer_id": producer.id, "role": role,
                    # Same rule as unit 8: 0 means not disclosed.
                    "share_pct": round(float(share), 2) if share else None,
                    "share_disclosed": bool(share),
                    "location": el.get("loc") or el.get("hq"),
                    "regions_raw": el.get("regs"), "tags": el.get("tags"),
                    "raw_name": el["n"], "sort_order": i,
                })
    return rows


def _role_key(row: dict) -> tuple:
    share = row["share_pct"]
    return (str(row["producer_id"]), row["role"],
            float(share) if share is not None else None, row["share_disclosed"],
            row["location"], json.dumps(row["regions_raw"], sort_keys=True),
            json.dumps(row["tags"], sort_keys=True), row["raw_name"], row["sort_order"])


def _sync_producer_roles(db: Session, dossier: IndexDossier, payload: dict,
                         aliases: dict[str, str]) -> bool:
    """Make the dossier's producer roles what the payload says. True when
    they had to be rewritten.

    Written here rather than by `_replace_children`, whose resolver splits
    inside parentheses (see `dossier_parts`). Compared as a block, every
    column, and rewritten as a block when anything differs: a producer deleted
    elsewhere (its roles go by CASCADE) or a changed share both come back on
    the next run, and an unchanged dossier compares equal.
    """
    stored = (db.query(IndexProducerRole)
              .filter(IndexProducerRole.dossier_id == dossier.id).all())
    have = sorted(_role_key({c: getattr(r, c) for c in (
        "producer_id", "role", "share_pct", "share_disclosed", "location",
        "regions_raw", "tags", "raw_name", "sort_order")}) for r in stored)
    wanted = _wanted_roles(db, payload, aliases, create=False)
    if wanted is not None and sorted(_role_key(r) for r in wanted) == have:
        return False

    wanted = _wanted_roles(db, payload, aliases, create=True)
    for row in stored:
        db.delete(row)
    db.flush()
    for row in wanted:
        db.add(IndexProducerRole(dossier_id=dossier.id, **row))
    db.flush()
    db.expire(dossier, ["producer_roles"])
    return True


def _load_dossiers(db: Session, cards: dict, key_to_id: dict[str, int],
                   report: IndexLoadReport) -> None:
    owners, conflicts, unmatched = dossier_owners(cards, key_to_id)
    aliases = raw("SUPPLIER_ALIASES")
    producers_before = db.query(func.count(Producer.id)).scalar()
    aliases_before = db.query(func.count(ProducerAlias.id)).scalar()

    # `_load_one` records into `report` under "dossiers".
    wrapped = DossierLoadReport(report=report)
    access_dropped: list[str] = []
    for key in sorted(owners):
        slug, payload = owners[key]
        payload, dropped = fit_access_tier(payload)
        if dropped:
            access_dropped.append(f"{key} ({len(dropped)} ch)")
        commodity_id = key_to_id[key]
        diff = report.table("dossiers")
        unchanged_before = diff.unchanged if diff is not None else 0
        # Header and children through the July mapping; the producer roles
        # after it, here.
        _load_one(db, commodity_id, None, without_producers(payload), wrapped)
        dossier = db.query(IndexDossier).filter(
            IndexDossier.commodity_id == commodity_id, IndexDossier.region.is_(None)).one()
        diff = report.table("dossiers")
        if (_sync_producer_roles(db, dossier, payload, aliases)
                and diff.unchanged > unchanged_before):
            # Header and children matched, the producer roles did not.
            diff.unchanged -= 1
            diff.updated += 1

    diff = report.table("dossiers")
    if diff is None:
        diff = TableDiff("dossiers")
        report.tables.append(diff)
    owned_ids = {key_to_id[k] for k in owners}
    diff.stale = (
        db.query(func.count(IndexDossier.id))
        .filter(IndexDossier.commodity_id.in_(list(key_to_id.values())))
        .filter((IndexDossier.region.isnot(None)) | (~IndexDossier.commodity_id.in_(owned_ids)))
        .scalar()
    )

    producers = TableDiff("producers (dossier)")
    producers.created = db.query(func.count(Producer.id)).scalar() - producers_before
    # A company a dossier once named and no dossier or supplier list names
    # now: reported, kept, like every other platform row the drop dropped.
    orphans = sorted(
        name for (name,) in db.query(Producer.name)
        .filter(Producer.source == DOSSIER_PRODUCER_SOURCE)
        .filter(~db.query(IndexProducerRole.id)
                .filter(IndexProducerRole.producer_id == Producer.id).exists())
        .filter(~db.query(ProducerFormula.id)
                .filter(ProducerFormula.producer_id == Producer.id).exists())
    )
    producers.stale = len(orphans)
    report.tables.append(producers)
    if orphans:
        report.notes.append(
            f"producers (source 'dossier') no dossier or supplier list names any more, "
            f"kept: {', '.join(orphans)}")
    producer_aliases = TableDiff("producer_aliases (dossier)")
    producer_aliases.created = db.query(func.count(ProducerAlias.id)).scalar() - aliases_before
    report.tables.append(producer_aliases)

    report.notes.append(
        f"dossiers: {len(owners)} series carry a dossier from "
        f"{len({slug for slug, _ in owners.values()})} INDEXES cards; "
        f"{len(conflicts)} card/series claims lost to the series' own or regional "
        f"card: {', '.join(f'{s}->{k}' for s, k in sorted(conflicts))}"
    )
    if unmatched:
        report.notes.append(
            f"dossier cards with no live series: {', '.join(sorted(unmatched))}")
    if access_dropped:
        report.notes.append(
            f"access_tier left NULL on {len(access_dropped)} dossiers — the source "
            f"string exceeds String({ACCESS_TIER_MAX}): {', '.join(access_dropped)}")


# ── 6. The generators ────────────────────────────────────────────────────────

def _run_seasonality(db: Session, report: IndexLoadReport) -> TableDiff:
    """`index_seasonality.recompute_all` — what the scheduled job runs.
    Counted per series (twelve factor rows each)."""
    diff = TableDiff("index_seasonal_factors (series)")
    had = {cid for (cid,) in db.query(IndexSeasonalFactor.commodity_id)
           .filter(IndexSeasonalFactor.region.is_(None)).distinct()}
    names = dict(db.query(CommodityIndex.id,
                          func.coalesce(CommodityIndex.commodity_key, CommodityIndex.name)))
    result = recompute_all(db)
    for series in result.results:
        if series.status == "computed":
            if series.commodity_id in had:
                diff.updated += 1
            else:
                diff.created += 1
        elif series.status == "unchanged":
            diff.unchanged += 1
        else:
            diff.skipped.append((names.get(series.commodity_id, str(series.commodity_id)),
                                 series.reason or "insufficient history"))
    report.notes.append(
        f"seasonality: {result.computed} computed, {result.unchanged} unchanged, "
        f"{result.insufficient} insufficient")
    return diff


def _run_volatility(db: Session, report: IndexLoadReport) -> TableDiff:
    """`recompute_volatility_calibration`, gated.

    The recompute writes a new vintage on every call — right for the admin
    button, wrong for a loader that must change nothing on its second run. So
    the ladder is fitted first and compared with the active one (same method,
    series count, rung count, breakpoints at the stored precision); only a
    ladder that moved is written.
    """
    diff = TableDiff("volatility_calibrations")
    dispersions = _all_dispersions(db, min_points=DEFAULT_MIN_POINTS)
    if len(dispersions) < 2:
        diff.skipped.append(("ladder", f"only {len(dispersions)} series have "
                                       f"{DEFAULT_MIN_POINTS}+ monthly actuals"))
        return diff
    ladder = build_ladder(list(dispersions.values()), n_rungs=DEFAULT_RUNGS)

    active = active_calibration(db)
    current = ([float(b.dispersion) for b in sorted(active.breakpoints, key=lambda b: b.rung)]
               if active is not None else [])
    same = (
        active is not None
        and active.method == METHOD_MOM_PCT_STDEV
        and active.n_series == len(dispersions)
        and active.min_points == DEFAULT_MIN_POINTS
        and len(current) == len(ladder)
        # Numeric(10,4) on the breakpoint: compare at that precision.
        and all(abs(a - round(b, 4)) < 1e-4 for a, b in zip(current, ladder))
    )
    if same:
        diff.unchanged += 1
        state = "unchanged"
    else:
        recompute_volatility_calibration(
            db, n_rungs=DEFAULT_RUNGS, min_points=DEFAULT_MIN_POINTS,
            note="Fitted by the content drop load (seed_content_drop.py).")
        diff.created += 1
        state = "new vintage written"
    report.notes.append(
        f"volatility: {state}; {len(dispersions)} series, {len(ladder)} rungs, "
        f"dispersion {ladder[0]:.4f} .. {ladder[-1]:.4f}")
    return diff


# ── The forecast vintage ─────────────────────────────────────────────────────

def ffore_digest(ffore: dict) -> str:
    """sha256 of FFORE as canonical JSON (sorted keys, no spaces): the same
    content gives the same digest however the file is laid out."""
    canonical = json.dumps(ffore, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _check_forecast_vintage(ffore: dict, report: IndexLoadReport) -> TableDiff:
    """A check row, never a change: `unchanged` = 1 when FFORE is the content
    `FORECAST_VINTAGE` was recorded for, else a skip line asking for the
    vintage to be confirmed."""
    diff = TableDiff(CHECK_FORECAST_VINTAGE)
    label = f"{FORECAST_VINTAGE[0]:04d}-{FORECAST_VINTAGE[1]:02d}"
    if ffore_digest(ffore) == FORECAST_VINTAGE_FFORE_SHA256:
        diff.unchanged = 1
        report.notes.append(f"forecast vintage {label} (FFORE matches the content it "
                            "was recorded for)")
    else:
        diff.skipped.append(("FFORE", f"differs from the forecast vintage {label} was "
                                      "recorded for; confirm the new vintage and update "
                                      "FORECAST_VINTAGE and FORECAST_VINTAGE_FFORE_SHA256 "
                                      "in content_drop/indexes.py"))
        report.notes.append(f"forecast vintage UNCONFIRMED: FFORE changed since {label} "
                            "was recorded; screens still label the forecast with it")
    return diff


# ── Entry point ──────────────────────────────────────────────────────────────

def load(db: Session, report: LoadReport | None = None) -> IndexLoadReport:
    """Load the index layer and run its generators. Does not
    commit. Returns the harness report (as an `IndexLoadReport`, sharing its
    table list) with the non-row findings in `notes`."""
    out = IndexLoadReport(title=report.title if report else "content drop · indexes",
                          tables=report.tables if report else [])

    fidx = raw("FIDX")
    ffore = raw("FFORE")
    fcovered = raw("FCOVERED")
    meta = raw("INDEX_SOURCE_META")
    cards = indexes_file("INDEXES")
    refs, dead = card_refs(cards, fidx)

    series_diff, key_to_id = _load_series(db, fidx, meta, refs)
    out.tables.append(series_diff)
    out.tables.append(_load_monthly(db, fidx, ffore, key_to_id))
    out.tables.append(_load_type_codes(db, fcovered, key_to_id, tag_usage(raw("FORMULA_COMBOS"))))
    out.tables.append(_load_cards(db, cards, meta, key_to_id, dead))
    _load_dossiers(db, cards, key_to_id, out)
    out.tables.append(_run_seasonality(db, out))
    out.tables.append(_run_volatility(db, out))
    out.tables.append(_check_forecast_vintage(ffore, out))

    carries = sorted(k for k, run in ffore.items()
                     if k in fidx and run and all(v == fidx[k][-1] for v in run))
    out.notes[:0] = [
        f"{len(dead)} INDEXES regKeys name series FIDX does not carry — no card row "
        f"(listed under skipped); {len(set(fidx) - set(refs))} series have no card "
        "and are named from their key",
        f"no forecast in FFORE: {', '.join(sorted(set(fidx) - set(ffore)))}; of the "
        f"{len(ffore)} forecasts {len(carries)} are flat carries of the last actual",
        "base_period NULL (Jan 2023 is not 100): "
        + ", ".join(f"{k} ({fidx[k][0]})" for k in sorted(fidx) if fidx[k][0] != 100),
        f"no INDEX_SOURCE_META entry (provider/frequency NULL): "
        f"{', '.join(sorted(set(fidx) - set(meta)))}; META with no series: "
        f"{', '.join(sorted(set(meta) - set(fidx)))}",
    ]
    return out
