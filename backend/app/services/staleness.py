"""Stale flags on text (LIVE-5 / SCRUM-212, with SCRUM-192 folded in).

Live tokens (210, LIVE-4) keep the numbers in a text right; the words around
them cannot follow ("prices rose sharply"), and many typed numbers are not
converted yet. This marks the text that is out of date, says why, and lets a
person clear the flag after reading it again.

Texts: report sections and panels, platform levers, platform blocks.

**Reasons** (all PROPOSED, for review with Alexis):

    data_moved     a series the text reads moved more than MOVE_PCT since the
                   text's baseline (the radar's 10%)
    data_late      a series it reads is late by the app-wide rule (Jira 209 S4)
    typed_drifted  it still holds typed numbers that no longer match our data
                   (LIVE-4's check)
    old_text       written more than OLD_AFTER_MONTHS ago
    expired        past its `expires_at` (blocks that set one)

**Baseline** (S2): the data a text was written against — for each series it
reads, the latest actual month and value at the text's date. The text's
date: a report's month, a playbook's `last_updated`, a block version's date —
or, for a block loaded from the content drop (whose load date is not when it
was written), the drop's data month.

**Review** (S5): a person who read the text again re-pins its baseline to
today's data and acknowledges what was flagged — the drifted-number count and
the late series as they stand. The text is flagged again only if something
moves further: more drift, a newer late month, another 10% move, or six more
months.

**Reader note** (S4): one calm sentence for whoever reads the text; the full
reasons are for staff. Age alone gets no reader note (see `reader_note`).

Nothing here commits: the caller (daily task, CLI) commits or rolls back.
"""
from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.editorial import EditorialBlock, EditorialBlockVersion
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexMonthlyValue
from app.models.market_report import MarketReport, MarketReportPanel, MarketReportSection
from app.models.strategy import Playbook, PlaybookLever
from app.services.content_drop.report_sanitize import parse
from app.services.number_inventory import inventory
from app.services.number_matching import DRIFTED, match_items
from app.services.series_health import STALE as HEALTH_STALE
from app.services.series_health import series_health_bulk

# PROPOSED (LIVE-5): the radar's threshold for a move that matters.
MOVE_PCT = 10.0
# PROPOSED (LIVE-5): text older than this is flagged for a re-read.
OLD_AFTER_MONTHS = 6

DATA_MOVED = "data_moved"
DATA_LATE = "data_late"
TYPED_DRIFTED = "typed_drifted"
OLD_TEXT = "old_text"
EXPIRED = "expired"
REASONS = (DATA_MOVED, TYPED_DRIFTED, DATA_LATE, EXPIRED, OLD_TEXT)   # most serious first

SECTION, PANEL, LEVER, BLOCK = "section", "panel", "lever", "block"
KINDS = (SECTION, PANEL, LEVER, BLOCK)
STRATEGIC_PANELS = ("pestel", "porter", "market_drivers", "kraljic", "outlook")


def _mi(year: int, month: int) -> int:
    return year * 12 + month - 1


def _ym(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _label(ym: str) -> str:
    y, m = ym.split("-")
    return f"{calendar.month_abbr[int(m)]} {y}"


@dataclass
class Text:
    kind: str
    row: object                      # the ORM row carrying the staleness columns
    where: str                       # "slug/section", "lever code", "code/type"
    written: date | None             # the text's own date
    series: list[str]                # series keys from derived_from
    expires_at: date | None = None


# ── Reading the texts and the data ───────────────────────────────────────────

def drop_data_month(db: Session) -> date | None:
    """The newest actual month in the content series: when drop text was written for."""
    last = (db.query(IndexMonthlyValue.year * 12 + IndexMonthlyValue.month - 1)
            .join(CommodityIndex, CommodityIndex.id == IndexMonthlyValue.commodity_id)
            .filter(CommodityIndex.commodity_key.isnot(None), IndexMonthlyValue.kind == "actual")
            .order_by((IndexMonthlyValue.year * 12 + IndexMonthlyValue.month - 1).desc())
            .limit(1).scalar())
    return date(last // 12, last % 12 + 1, 1) if last is not None else None


def _series_of(record: dict | None) -> list[str]:
    return list((record or {}).get("series") or [])


def texts(db: Session) -> list[Text]:
    """Every platform text that can go stale, with its date and the series it reads."""
    drop_month = drop_data_month(db)
    as_of = dict(db.query(MarketReport.slug, MarketReport.as_of))
    out: list[Text] = []
    for r in db.query(MarketReportSection):
        out.append(Text(SECTION, r, f"{r.slug}/{r.section_id}", as_of.get(r.slug) or drop_month,
                        _series_of(r.derived_from)))
    for r in db.query(MarketReportPanel):
        out.append(Text(PANEL, r, f"{r.slug}/panel {r.panel}", as_of.get(r.slug) or drop_month,
                        _series_of(r.derived_from)))
    updated = dict(db.query(Playbook.slug, Playbook.last_updated))
    for r in db.query(PlaybookLever):
        out.append(Text(LEVER, r, r.lever_code, updated.get(r.playbook_slug) or drop_month,
                        _series_of(r.derived_from)))
    for b, v in (db.query(EditorialBlock, EditorialBlockVersion)
                 .join(EditorialBlockVersion, EditorialBlockVersion.id == EditorialBlock.current_version_id)
                 .filter(EditorialBlock.team_id.is_(None))):
        written = drop_month if b.provenance == "imported" else (v.created_at.date() if v.created_at else None)
        out.append(Text(BLOCK, b, f"{b.subject_code}/{b.block_type}", written,
                        _series_of(b.derived_from), expires_at=b.expires_at))
    return out


def _actuals(db: Session, keys: set[str]) -> dict[str, dict[int, float]]:
    """series key -> {month index: actual value}."""
    out: dict[str, dict[int, float]] = defaultdict(dict)
    if not keys:
        return out
    for key, y, m, v in (db.query(CommodityIndex.commodity_key, IndexMonthlyValue.year,
                                  IndexMonthlyValue.month, IndexMonthlyValue.value)
                         .join(IndexMonthlyValue, IndexMonthlyValue.commodity_id == CommodityIndex.id)
                         .filter(CommodityIndex.commodity_key.in_(keys), IndexMonthlyValue.kind == "actual")):
        out[key][_mi(y, m)] = float(v)
    return out


def _point_at(values: dict[int, float], when: date) -> dict | None:
    """The latest actual at or before `when`'s month."""
    upto = [m for m in values if m <= _mi(when.year, when.month)]
    if not upto:
        return None
    m = max(upto)
    return {"month": _ym(m), "value": values[m]}


def _typed_drift(db: Session, as_of: date) -> tuple[dict, Counter]:
    """Drifted typed numbers: per report part (slug, part) the numbers as typed,
    and per block id a count."""
    by_part: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_block: Counter = Counter()
    for m in match_items(db, inventory(db), as_of=as_of):
        if m.status != DRIFTED:
            continue
        if m.item.source == "report":
            by_part[(m.item.doc, m.item.part)].append(m.item.text)
        elif m.item.ref:
            by_block[m.item.ref] += 1
    return by_part, by_block


def _drift_for(t: Text, by_part: dict, by_block: Counter) -> int:
    if t.kind == SECTION:
        return len(by_part.get((t.row.slug, t.row.section_id), []))
    if t.kind == PANEL:
        if t.row.panel not in STRATEGIC_PANELS:
            return len(by_part.get((t.row.slug, t.row.panel), []))
        # Section 7 is cut into several panels: count only the drifted numbers
        # this panel actually holds.
        text = " ".join(parse(t.row.html).get_text(" ").split())
        return sum(1 for typed in by_part.get((t.row.slug, "strategic"), []) if typed in text)
    if t.kind == BLOCK:
        return by_block[str(t.row.id)]
    return 0


# ── Baselines (S2) ───────────────────────────────────────────────────────────

def fill_baselines(db: Session) -> int:
    """A baseline for every text that reads series and has none yet: the data
    at the text's own date. A reviewed baseline is never overwritten."""
    items = [t for t in texts(db) if t.series and t.row.baseline is None and t.written]
    values = _actuals(db, {k for t in items for k in t.series})
    for t in items:
        t.row.baseline = {
            "as_of": t.written.isoformat(),
            "series": {k: p for k in t.series if (p := _point_at(values.get(k, {}), t.written))},
        }
    db.flush()
    return len(items)


# ── Reasons (S1) ─────────────────────────────────────────────────────────────

def reasons_for(t: Text, today: date, values: dict, health: dict, drift: int) -> list[dict]:
    base = t.row.baseline or {}
    ack = base.get("ack") or {}
    out: list[dict] = []

    moves = []
    for key, point in (base.get("series") or {}).items():
        now = values.get(key)
        if not now or not point or not point.get("value"):
            continue
        latest = max(now)
        pct = (now[latest] / point["value"] - 1) * 100
        if abs(pct) > MOVE_PCT:
            moves.append({"series": key, "from": point["month"], "to": _ym(latest), "pct": round(pct, 1)})
    if moves:
        out.append({"code": DATA_MOVED, "moves": moves,
                    "detail": "; ".join(f"{m['series']} {m['pct']:+.1f}% since {_label(m['from'])}"
                                        for m in moves)})

    acked = int(ack.get(TYPED_DRIFTED) or 0)
    if drift > acked:
        out.append({"code": TYPED_DRIFTED, "count": drift,
                    "detail": f"{drift} typed number(s) no longer match our data"})

    late = []
    for key in t.series:
        h = health.get(key)
        if h is not None and h.state == HEALTH_STALE and (ack.get(DATA_LATE) or {}).get(key) != h.last_actual:
            late.append((key, h.last_actual))
    if late:
        out.append({"code": DATA_LATE, "series": [k for k, _ in late],
                    "detail": "late data: " + ", ".join(f"{k} (last {_label(m)})" for k, m in late)})

    if t.expires_at and t.expires_at < today:
        out.append({"code": EXPIRED, "detail": f"expired {t.expires_at.isoformat()}"})

    reviewed = base.get("reviewed_at")
    since = max([d for d in (t.written, date.fromisoformat(reviewed[:10]) if reviewed else None) if d],
                default=None)
    if since:
        months = _mi(today.year, today.month) - _mi(since.year, since.month)
        if months > OLD_AFTER_MONTHS:
            out.append({"code": OLD_TEXT, "months": months,
                        "detail": f"{'reviewed' if reviewed else 'written'} {_label(_ym(_mi(since.year, since.month)))}, "
                                  f"{months} months ago"})
    return out


def reader_note(reasons: list[dict] | None) -> str | None:
    """One calm sentence for a reader; None when the text is current — or
    when age is its only reason: a report already shows its date to readers,
    and every text ages together when the content drop is not re-loaded (the
    Refresh status page alerts that), so age alone is a note for staff."""
    by = {r["code"]: r for r in reasons or []}
    by.pop(OLD_TEXT, None)
    if not by:
        return None
    if DATA_MOVED in by:
        m = by[DATA_MOVED]["moves"][0]
        return (f"May be out of date: {m['series']} has moved {m['pct']:+.0f}% since this was written "
                f"({_label(m['from'])}).")
    if TYPED_DRIFTED in by:
        return "May be out of date: some figures here no longer match our latest data."
    if DATA_LATE in by:
        return "Based on data that is now late; figures may be out of date."
    return "This outlook has passed its date."


# ── The check (S3) ───────────────────────────────────────────────────────────

@dataclass
class CheckReport:
    checked: Counter = field(default_factory=Counter)        # kind -> texts
    stale: Counter = field(default_factory=Counter)          # kind -> stale texts
    by_reason: Counter = field(default_factory=Counter)      # reason -> texts
    newly_stale: int = 0
    cleared: int = 0
    baselines_filled: int = 0

    def summary(self) -> str:
        return (f"{sum(self.stale.values())} stale text(s) of {sum(self.checked.values())} "
                f"({self.newly_stale} newly stale, {self.cleared} cleared); "
                + ", ".join(f"{n} {r}" for r, n in self.by_reason.most_common()))


def check(db: Session, *, today: date | None = None) -> CheckReport:
    """Fill missing baselines, then give every text its reasons. Flushes."""
    today = today or date.today()
    report = CheckReport(baselines_filled=fill_baselines(db))
    items = texts(db)
    keys = {k for t in items for k in t.series}
    values = _actuals(db, keys)
    health = {}
    if keys:
        ids = dict(db.query(CommodityIndex.id, CommodityIndex.commodity_key)
                   .filter(CommodityIndex.commodity_key.in_(keys)))
        health = {ids[h.commodity_id]: h for h in series_health_bulk(
            db, as_of=today, commodity_ids=list(ids))}
    by_part, by_block = _typed_drift(db, today)
    now = datetime.now(timezone.utc)

    for t in items:
        reasons = reasons_for(t, today, values, health, _drift_for(t, by_part, by_block))
        stale = bool(reasons)
        report.checked[t.kind] += 1
        if stale:
            report.stale[t.kind] += 1
            for r in reasons:
                report.by_reason[r["code"]] += 1
        report.newly_stale += stale and not t.row.is_stale
        report.cleared += t.row.is_stale and not stale
        t.row.stale_reasons = reasons or None
        t.row.is_stale = stale
        t.row.stale_checked_at = now
    db.flush()
    return report


# ── Review (S5) ──────────────────────────────────────────────────────────────

MODELS = {SECTION: MarketReportSection, PANEL: MarketReportPanel,
          LEVER: PlaybookLever, BLOCK: EditorialBlock}


def mark_reviewed(db: Session, kind: str, row_id, *, by: str, today: date | None = None) -> object:
    """A person read the text again: re-pin its baseline to today's data and
    acknowledge what was flagged. Flushes; returns the row."""
    today = today or date.today()
    row = db.get(MODELS[kind], row_id)
    if row is None:
        raise LookupError(f"no {kind} {row_id}")
    keys = _series_of(row.derived_from)
    values = _actuals(db, set(keys))
    flagged = {r["code"]: r for r in (row.stale_reasons or [])}
    ack: dict = {}
    if TYPED_DRIFTED in flagged:
        ack[TYPED_DRIFTED] = flagged[TYPED_DRIFTED]["count"]
    if DATA_LATE in flagged and keys:
        ids = dict(db.query(CommodityIndex.id, CommodityIndex.commodity_key)
                   .filter(CommodityIndex.commodity_key.in_(keys)))
        ack[DATA_LATE] = {ids[h.commodity_id]: h.last_actual
                          for h in series_health_bulk(db, as_of=today, commodity_ids=list(ids))}
    row.baseline = {
        "as_of": today.isoformat(),
        "series": {k: p for k in keys if (p := _point_at(values.get(k, {}), today))},
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_by": by,
        "ack": ack,
    }
    row.stale_reasons = None
    row.is_stale = False
    row.stale_checked_at = datetime.now(timezone.utc)
    db.flush()
    return row
