"""Index dossier loader (Wave 3, DB-7).

Loads the **structured** half of `INDEXES.json` — 54 entries, 38 of which carry
a full dossier, plus per-region overrides on 16 of them.

What it deliberately does not load, and why:

* `currentVal` / `change` / `up` / `snapshot` / `cyclePos` / `volPct` — computed
  snapshots. `volPct` is additionally self-contradictory (three series carry two
  different values across their own cards), and the ladder it feeds is
  regenerated in `services/index_dossier` rather than imported.
* `season` / `seasonNote` — reproduce from the series; SCRUM-69 generates
  `index_seasonal_factor` and never imports it.
* `dyn3m` / `dyn24m` / `signals3m` / `signals24m` / `chainNote` /
  `upstreamNote` / `producersNote` / `psNote` / `volNote` / `outlook` — prose,
  which belongs in unit 7's `subject_type='index'` editorial blocks.
* `regKeys` / `regLabels` / `defaultRegs` / `usedIn` / `formulaUsage` — the card
  and catalog layers already hold these (units 2 and 3b).

Producers and price-setters resolve through **unit 8's** alias layer, so the
company record has one master.

Never deletes: a dossier the drop stops mentioning is reported stale, not
removed. Child rows *are* replaced as a block, because a partially-updated
driver list would be worse than either the old or the new one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.index_data import CommodityIndex
from app.models.index_dossier import (
    IndexChainNode, IndexDossier, IndexDriver, IndexNegotiationPointer,
    IndexProducerRole, IndexRoleFlag, IndexSplit, normalize_signal,
)
from app.services.drop.reader import DropNotAvailable, drop_root, read_raw
from app.services.drop.report import LoadReport, TableDiff
from app.services.producers import resolve_raw_name

# Fields skipped on purpose, listed so the omission is auditable rather than
# looking like the loader forgot them.
SKIPPED_COMPUTED = (
    "currentVal", "change", "up", "snapshot", "cyclePos", "volPct", "usedIn",
)
SKIPPED_DERIVABLE = ("season", "seasonNote")
SKIPPED_PROSE = (
    "dyn3m", "dyn24m", "signals3m", "signals24m", "chainNote", "upstreamNote",
    "producersNote", "psNote", "volNote", "outlook",
)
SKIPPED_ELSEWHERE = ("regKeys", "regLabels", "defaultRegs", "formulaUsage")

_R_VALUE = re.compile(r"r\s*=\s*(-?\d*\.?\d+)")
_WEEKS = re.compile(r"(\d+)\s*(?:[-–—]\s*(\d+))?\s*week", re.I)
_MONTHS = re.compile(r"(\d+)\s*(?:[-–—]\s*(\d+))?\s*month", re.I)
_QUARTERS = re.compile(r"(\d+)\s*(?:[-–—]\s*(\d+))?\s*quarter", re.I)
_DAYS = re.compile(r"(\d+)\s*(?:[-–—]\s*(\d+))?\s*day", re.I)
_IMMEDIATE = re.compile(r"immediate|same[- ]month|concurrent", re.I)


def parse_lag_days(raw: str | None) -> tuple[int | None, int | None]:
    """Bounds in days for a free-text lag, or (None, None).

    38 distinct lag strings in the source. The raw string stays authoritative;
    these bounds exist so a caller can sort and threshold, and they are left
    NULL rather than guessed whenever the string does not parse — a lag silently
    invented as zero would read as "arrives immediately", the opposite of
    "unknown".
    """
    text = (raw or "").strip()
    if not text:
        return None, None
    if _IMMEDIATE.search(text):
        return 0, 0
    for pattern, per in ((_DAYS, 1), (_WEEKS, 7), (_MONTHS, 30), (_QUARTERS, 91)):
        m = pattern.search(text)
        if m:
            lo = int(m.group(1)) * per
            hi = int(m.group(2)) * per if m.group(2) else lo
            return lo, hi
    return None, None


def parse_correlation(raw) -> float | None:
    """A coefficient from either a bare number or an "r=0.82 vs ..." string."""
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if -1.0 <= value <= 1.0 else None
    m = _R_VALUE.search(str(raw or ""))
    if not m:
        return None
    value = float(m.group(1))
    return value if -1.0 <= value <= 1.0 else None


def _pct(raw) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@dataclass
class DossierLoadReport:
    report: LoadReport = field(default_factory=lambda: LoadReport("Index dossiers"))
    notes: list[str] = field(default_factory=list)
    unmatched_series: list[str] = field(default_factory=list)
    # Two dossier keys claiming one series — reported by name, never silently
    # overwritten. See `_targets_for`.
    shared_series_conflicts: list[tuple[str, str]] = field(default_factory=list)

    def render(self) -> str:
        lines = [self.report.render()]
        if self.unmatched_series:
            lines.append(
                f"\nDossier keys with no loaded series ({len(self.unmatched_series)}): "
                + ", ".join(sorted(self.unmatched_series)[:12])
            )
        if self.shared_series_conflicts:
            lines.append(
                f"\nDossier keys skipped because another dossier already holds their "
                f"series ({len(self.shared_series_conflicts)}):"
            )
            for key, series in sorted(self.shared_series_conflicts):
                lines.append(f"  {key} -> {series}")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


def _diff(report: LoadReport, name: str) -> TableDiff:
    existing = report.table(name)
    if existing is not None:
        return existing
    row = TableDiff(table=name)
    report.tables.append(row)
    return row


def _same_children(existing: IndexDossier, payload: dict) -> bool:
    """Is the stored child set already exactly what the payload says?

    Compared as a block rather than field by field, because the children are
    replaced as a block — so "unchanged" has to mean the whole set matches.
    """
    drivers = [
        (d.category, d.provider,
         float(d.correlation) if d.correlation is not None else None,
         d.lag_raw, d.signal_raw, d.move_raw, d.move_up)
        for d in sorted(existing.drivers, key=lambda d: d.sort_order)
    ]
    want_drivers = [
        (el.get("cat"), el.get("src"), parse_correlation(el.get("corr")),
         el.get("lag"), el.get("signal"), el.get("move"), el.get("moveUp"))
        for el in (payload.get("upstreamDrivers") or []) if isinstance(el, dict)
    ]
    if drivers != want_drivers:
        return False

    chain = [(c.position, c.node_type, c.label, c.detail)
             for c in sorted(existing.chain, key=lambda c: c.position)]
    want_chain = []
    for i, el in enumerate((payload.get("chain") or [])):
        if not isinstance(el, dict):
            continue
        if el.get("a"):
            want_chain.append((i, "transform", el["a"], None))
        elif el.get("l"):
            want_chain.append((i, "node", el["l"], el.get("s")))
    if chain != want_chain:
        return False

    splits = [(s.split_type, s.label, float(s.pct) if s.pct is not None else None, s.note)
              for s in sorted(existing.splits, key=lambda s: (s.split_type, s.sort_order))]
    want_splits = []
    for kind in ("demand", "supply"):
        for el in (payload.get(kind) or []):
            if isinstance(el, dict) and el.get("l"):
                want_splits.append((kind, el["l"], _pct(el.get("v")), el.get("t") or None))
    if splits != want_splits:
        return False

    pointers = [(p.title, p.body)
                for p in sorted(existing.pointers, key=lambda p: p.sort_order)]
    want_pointers = [(el.get("title"), el.get("body"))
                     for el in (payload.get("negPointers") or [])
                     if isinstance(el, dict) and el.get("title")]
    return pointers == want_pointers


def _replace_children(db: Session, dossier: IndexDossier, payload: dict,
                      out: DossierLoadReport) -> None:
    for child in (dossier.drivers, dossier.chain, dossier.flags,
                  dossier.splits, dossier.producer_roles, dossier.pointers):
        for row in list(child):
            db.delete(row)
    db.flush()

    for i, el in enumerate(payload.get("upstreamDrivers") or []):
        if not isinstance(el, dict):
            continue
        lo, hi = parse_lag_days(el.get("lag"))
        db.add(IndexDriver(
            dossier_id=dossier.id, category=el.get("cat"), provider=el.get("src"),
            correlation=parse_correlation(el.get("corr")),
            lag_raw=el.get("lag"), lag_days_min=lo, lag_days_max=hi,
            signal_raw=el.get("signal"),
            signal_strength=normalize_signal(el.get("signal")),
            move_raw=el.get("move"), move_up=el.get("moveUp"), sort_order=i,
        ))

    for i, el in enumerate(payload.get("chain") or []):
        if not isinstance(el, dict):
            continue
        if el.get("a"):
            db.add(IndexChainNode(dossier_id=dossier.id, position=i,
                                  node_type="transform", label=el["a"]))
        elif el.get("l"):
            db.add(IndexChainNode(dossier_id=dossier.id, position=i,
                                  node_type="node", label=el["l"],
                                  detail=el.get("s")))

    for i, el in enumerate(payload.get("roles") or []):
        if isinstance(el, dict) and el.get("name"):
            db.add(IndexRoleFlag(dossier_id=dossier.id, flag_kind="role",
                                 label=el["name"][:300], detail=el.get("desc"),
                                 sort_order=i))
    for i, el in enumerate(payload.get("sust") or []):
        if isinstance(el, dict) and el.get("flag"):
            db.add(IndexRoleFlag(
                dossier_id=dossier.id, flag_kind="sustainability",
                severity=el.get("type") if el.get("type") in ("ok", "warn", "info") else None,
                label=el["flag"][:300], detail=el.get("desc") or el.get("name"),
                sort_order=i,
            ))

    for kind in ("demand", "supply"):
        for i, el in enumerate(payload.get(kind) or []):
            if isinstance(el, dict) and el.get("l"):
                db.add(IndexSplit(dossier_id=dossier.id, split_type=kind,
                                  label=el["l"][:160], pct=_pct(el.get("v")),
                                  note=(el.get("t") or None), sort_order=i))

    for i, el in enumerate(payload.get("negPointers") or []):
        if isinstance(el, dict) and el.get("title"):
            db.add(IndexNegotiationPointer(
                dossier_id=dossier.id, title=el["title"][:300],
                body=el.get("body"), sort_order=i))

    # Producers + price setters, resolved through unit 8's alias layer.
    seen: set[tuple[str, str]] = set()
    for role, field_name in (("producer", "producers"), ("price_setter", "priceSetters")):
        for i, el in enumerate(payload.get(field_name) or []):
            if not isinstance(el, dict) or not el.get("n"):
                continue
            for resolved in resolve_raw_name(db, el["n"], alias_map=None,
                                             source="dossier"):
                key = (str(resolved.producer.id), role)
                if key in seen:
                    # A `" / "` string can resolve to a company already named
                    # separately in the same list; the unique constraint would
                    # reject the duplicate.
                    continue
                seen.add(key)
                share = el.get("share")
                db.add(IndexProducerRole(
                    dossier_id=dossier.id, producer_id=resolved.producer.id,
                    role=role,
                    share_pct=float(share) if share else None,
                    # Same rule as unit 8: 0 means not disclosed.
                    share_disclosed=bool(share),
                    location=el.get("loc") or el.get("hq"),
                    regions_raw=el.get("regs"), tags=el.get("tags"),
                    raw_name=el["n"], sort_order=i,
                ))
    db.flush()


def _load_one(db: Session, commodity_id: int, region: str | None, payload: dict,
              out: DossierLoadReport) -> None:
    diff = _diff(out.report, "dossiers" if region is None else "regional dossiers")
    q = db.query(IndexDossier).filter(IndexDossier.commodity_id == commodity_id)
    q = q.filter(IndexDossier.region.is_(None)) if region is None \
        else q.filter(IndexDossier.region == region)
    dossier = q.first()

    header = {
        "quote_type": (payload.get("roleExtra") or {}).get("type"),
        "formula_role": (payload.get("roleExtra") or {}).get("formula"),
        "access_tier": (payload.get("roleExtra") or {}).get("access")
        or payload.get("access"),
        "anchor_correlation_raw": (payload.get("roleExtra") or {}).get("corr"),
    }
    header["anchor_correlation"] = parse_correlation(header["anchor_correlation_raw"])

    if dossier is None:
        dossier = IndexDossier(commodity_id=commodity_id, region=region, **header)
        db.add(dossier)
        db.flush()
        _replace_children(db, dossier, payload, out)
        diff.created += 1
        return

    header_same = all(
        (getattr(dossier, k) if k != "anchor_correlation"
         else (float(dossier.anchor_correlation)
               if dossier.anchor_correlation is not None else None)) == v
        for k, v in header.items()
    )
    if header_same and _same_children(dossier, payload):
        diff.unchanged += 1
        return

    for k, v in header.items():
        setattr(dossier, k, v)
    _replace_children(db, dossier, payload, out)
    diff.updated += 1


DOSSIER_FIELDS = ("upstreamDrivers", "chain", "roles", "producers", "negPointers")


def has_dossier(payload: dict) -> bool:
    """Does this entry carry dossier content — on itself or on an override?

    The `_regional` check is load-bearing: **all 16 entries carrying
    `_regional` have no dossier fields at the top level**, but their overrides
    do (`iron-scrap-na._regional.NA` carries `upstreamDrivers`, `chain`,
    `roles`, `producers`). Testing only the parent silently skipped every one of
    them, which is how the first run reported "0 regional overrides" while the
    source had 16 entries' worth.
    """
    if any(payload.get(f) for f in DOSSIER_FIELDS):
        return True
    return any(
        isinstance(o, dict) and any(o.get(f) for f in DOSSIER_FIELDS)
        for o in (payload.get("_regional") or {}).values()
    )


def _targets_for(key: str, payload: dict, series_ids: dict) -> list[tuple[int, str]]:
    """Which loaded series this dossier is about.

    A dossier key is a *card* slug, and the series behind it may be named
    differently or shared. Three shapes in the data:

    * the key IS a series key (27 of 38) — one target;
    * the key fans out through `regKeys` to several region-baked series
      (`lab` -> lab-eu/-in/-mea/-apac/-na) — one target each, and the region
      already lives in the series key, so it is NOT also written to `region`;
      double-encoding it is exactly the mistake unit 2 avoided;
    * `regKeys` is `{"multi": <series>}`, meaning the card shares a series with
      other cards (`naphtha`, `cbfs` and `pta` all resolve to `brent`). Those
      collide, and the caller reports the loser rather than overwriting.
    """
    if key in series_ids:
        return [(series_ids[key], key)]
    out = []
    for series_key in (payload.get("regKeys") or {}).values():
        if series_key in series_ids and (series_ids[series_key], series_key) not in out:
            out.append((series_ids[series_key], series_key))
    return out


def load_dossiers(db: Session) -> DossierLoadReport:
    """Load every structured dossier. Does not commit."""
    if not drop_root().exists():
        raise DropNotAvailable("costadvisor-data drop not present")

    out = DossierLoadReport()
    payloads = read_raw("INDEXES")

    series_ids = {
        (row.commodity_key or row.name): row.id
        for row in db.query(CommodityIndex).all()
    }

    # Which dossier key owns each series' series-wide row. First writer wins and
    # the rest are reported, so a shared series never silently loses two of the
    # three dossiers claiming it.
    claimed: dict[int, str] = {}
    dossier_keys = 0
    fanned = 0
    regional = 0

    candidates = [
        (key, payload) for key, payload in payloads.items()
        if isinstance(payload, dict) and has_dossier(payload)
    ]
    # **Direct key matches first.** A dossier whose key IS the series key is the
    # specific one, and a generic key that fans out through `regKeys` must not
    # take its slot: iterating in source order let `electricity` claim `elec-cn`
    # and `elec-eu` before their own dedicated dossiers were reached, so the two
    # most specific dossiers in the file were the ones reported as conflicts.
    candidates.sort(key=lambda kv: 0 if kv[0] in series_ids else 1)

    for key, payload in candidates:
        targets = _targets_for(key, payload, series_ids)
        if not targets:
            out.unmatched_series.append(key)
            continue
        if len(targets) > 1:
            fanned += 1

        for commodity_id, series_key in targets:
            owner = claimed.get(commodity_id)
            if owner is not None and owner != key:
                out.shared_series_conflicts.append((key, series_key))
                continue
            claimed[commodity_id] = key
            dossier_keys += 1
            _load_one(db, commodity_id, None, payload, out)

            for region, override in (payload.get("_regional") or {}).items():
                if not isinstance(override, dict):
                    continue
                # An override carries only the fields that differ, so it is
                # merged onto the base payload — loading it bare would blank
                # everything the override happens not to mention.
                merged = {**payload, **override}
                merged.pop("_regional", None)
                # Only 10 of the 16 `_regional` carriers have any override with
                # dossier content, and most of those have just one — the other
                # regions are card metadata. Storing a row for them would put
                # empty dossiers in the table that read as "we have nothing to
                # say about this region" rather than "this region has no
                # dossier".
                if not any(merged.get(f) for f in DOSSIER_FIELDS):
                    continue
                _load_one(db, commodity_id, region, merged, out)
                regional += 1

    out.notes.append(
        f"{dossier_keys} series dossiers and {regional} regional overrides loaded "
        f"({fanned} dossier keys fanned out across several region-baked series via "
        "regKeys); computed snapshots (currentVal/change/snapshot/cyclePos/volPct), "
        "derivable series (season/seasonNote) and prose (dyn*/signals*/notes) "
        "deliberately not imported"
    )
    out.notes.append(
        "volPct is skipped because it is editorial and self-contradictory — three "
        "series carry two different values across their own cards (elec-cn 12 and "
        "55, elec-eu 55 and 65, corn 45 and 48). The ladder is regenerated from "
        "the series instead"
    )
    return out
