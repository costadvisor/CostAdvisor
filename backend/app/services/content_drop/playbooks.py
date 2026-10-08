"""Playbook loader — the category strategy playbooks (spec D7; design §2.6, §3.2).

Loads, as platform rows (no `team_id`), from the
`playbooks/playbook_<slug>_appdata.json` files, each `{category, objectives,
opportunities}`:

    playbooks                   one per file, keyed by `category.id` (the
                                report slug, e.g. "coagulants")
    playbook_levers             one per opportunity, keyed by
                                `lever_code` = the opportunity's `id`
    playbook_lever_objectives   the objectives each lever serves
    playbook_objectives         the authored `{type, priority, note}` pre-fills
                                of the Key strategic objectives panel

**What a playbook carries.** `name` and `family` as authored; `report_slug` =
its own slug when MANIFEST delivers a report with that slug, else NULL
(reported: a playbook can ship before its report); `legacy_line_key`
= `family|||name`, the pre-September line it was written against; `kraljic`
as authored (`{cx, cy, badge, label, boundary}`); `last_updated` from
`lastUpdated`. `category.owner` / `annualSpend` / `hasContent` are shim fields
of the mockup, null (or constant) in every file, and are not loaded: owner and
spend are the team's (strategy_records, the buyer's own spend). A non-null
owner or spend is reported, so a number the drop starts carrying is never
dropped in silence. Any other category key goes into `meta` as given.

**What a lever carries.** `gemstone_code` from the display name through
`GEMSTONE_CODE_BY_NAME`; `title`, `guidance`, `scales` as authored; the
defaults `applies` → `default_applies`, `ease` → `default_ease`,
`status` → `default_status`, `notes` → `default_notes` (an empty string stays
an empty string, as authored); `savings_score`; `sort_order` = position in
the file. `savings_value` (null on every lever, absent on 129) is the team's
own estimate (`lever_scores.savings_value`) and has no platform column; a
non-null one is reported. `custom` is false on every authored lever and is not
stored. A value the tables' checks would refuse (an ease outside 1–5, a status
outside `LEVER_STATUSES`) is stored NULL and reported, never coerced. A lever
whose gemstone is not one of the 8 is skipped: the column is NOT NULL.

**Levers are updated in place, never deleted.** `PlaybookLever.id` anchors
team state: `lever_scores` cascade away with a lever and `strategy_actions`
lose their link. So levers upsert by `lever_code`, and a lever the drop no
longer lists is reported `stale` and left in place, like every top-level row
(`drop/report.py`). The same for a playbook the drop no longer ships.

**Objective rows follow their parent.** A lever's objective links and a
playbook's authored objectives are stated in full by the file; a row the file
no longer lists is deleted (nothing references them). Objective display names
map to codes through `OBJECTIVE_CODE_BY_NAME`; an unknown name is reported.

The report closes with check rows (`unchanged` = the count, the way the
catalogue reports its trust grades): levers by gemstone, by status and by
applies, the coagulants spot check (19 levers), and whether every playbook
shows exactly its authored levers in file order.

A playbook reaches today's product lines only through its report:
`playbooks.report_slug` → `market_report_lines` → `product_line_id`. Nothing
here joins a line directly, so the taxonomy reshape changes nothing in this
loader (design §3.2: "imports only").

Idempotent: a second run reports zero changes. **Never commits** — the CLI
(`seed_content_drop.py`) owns the transaction. Reuses the taxonomy loader's
`_diff` / `_upsert` / `_sync_children` / `_rest` (they write SQL NULL for a
None in a JSONB column); if those change, check this module too.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.models.strategy import (
    GEMSTONE_CODE_BY_NAME, GEMSTONES, LEVER_STATUSES, OBJECTIVE_CODE_BY_NAME, PRIORITIES,
    GemstoneCategory, Playbook, PlaybookLever, PlaybookLeverObjective, PlaybookObjective,
    StrategicObjective,
)
from app.services.drop.report import LoadReport, TableDiff
from app.services.content_drop.reader import (
    make_line_key, playbook_files, read_json, report_manifest,
)
from app.services.content_drop.taxonomy import _diff, _rest, _sync_children, _upsert

# The category keys that have a column (or are deliberately not loaded).
# Anything else a file's category carries goes into `playbooks.meta`.
_CATEGORY_COLUMNS = ("id", "name", "family", "lastUpdated", "kraljic")
_SHIM_FIELDS = ("owner", "hasContent", "annualSpend")
# Shim fields that would carry a buyer's value if a file ever filled them in.
_SHIM_VALUES = ("owner", "annualSpend")

SCORE_RANGE = (1, 5)

# Check rows on the load report. `unchanged` = the count; they never add to
# `changed`, so a clean second run stays at zero.
CHECK_GEMSTONE = "check: levers gemstone "
CHECK_STATUS = "check: levers status "
CHECK_APPLIES = "check: levers applies "
CHECK_LEVER_SETS = "check: playbook lever sets"
SPOT_CHECK_SLUG = "coagulants"
CHECK_SPOT = f"check: {SPOT_CHECK_SLUG} levers"


# ── What the drop says ───────────────────────────────────────────────────────

def read_playbooks() -> list[tuple[str, dict]]:
    """Every playbook file as `(file name, parsed document)`, in file-name
    order (a stable load order)."""
    return [(path.name, read_json(path)) for path in playbook_files()]


def _score(value: Any, field: str, key: str, diff: TableDiff) -> int | None:
    """A 1–5 score as authored, or NULL (reported) when it is not one."""
    if value is None:
        return None
    lo, hi = SCORE_RANGE
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        diff.skipped.append((key, f"{field} {value!r} is not a {lo}-{hi} score; stored NULL"))
        return None
    return value


def lever_fields(slug: str, pos: int, opp: dict, diff: TableDiff) -> dict | None:
    """The `playbook_levers` fields for one authored opportunity, or None
    (reported on `diff`) when it cannot be stored."""
    code = opp.get("id")
    gemstone = GEMSTONE_CODE_BY_NAME.get(opp.get("gemstone"))
    if gemstone is None:
        diff.skipped.append((code, f"gemstone {opp.get('gemstone')!r} is not one of the 8"))
        return None
    if not opp.get("title"):
        diff.skipped.append((code, "lever has no title"))
        return None

    applies = opp.get("applies")
    if applies is not None and not isinstance(applies, bool):
        diff.skipped.append((code, f"applies {applies!r} is not true/false; stored NULL"))
        applies = None
    status = opp.get("status")
    if status is not None and status not in LEVER_STATUSES:
        diff.skipped.append((code, f"status {status!r} is not a lever status; stored NULL"))
        status = None
    scales = opp.get("scales")
    if scales is not None and not isinstance(scales, list):
        diff.skipped.append((code, f"scales {scales!r} is not a list; stored NULL"))
        scales = None
    if opp.get("savings_value") is not None:
        diff.skipped.append((code, f"savings_value {opp['savings_value']!r} is team data "
                                   "(lever_scores); the platform lever has no column"))

    return {
        "playbook_slug": slug,
        "gemstone_code": gemstone,
        "title": opp["title"],
        "guidance": opp.get("guidance"),
        "default_applies": applies,
        "default_ease": _score(opp.get("ease"), "ease", code, diff),
        "savings_score": _score(opp.get("savings_score"), "savings_score", code, diff),
        "default_status": status,
        "default_notes": opp.get("notes"),
        "scales": scales,
        "sort_order": pos,
    }


def _last_updated(value: Any, slug: str, diff: TableDiff) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        diff.skipped.append((slug, f"lastUpdated {value!r} is not a date; stored NULL"))
        return None


# ── Guard ────────────────────────────────────────────────────────────────────

def _check_vocabulary(db: Session) -> None:
    """The gemstones and objectives are seeded by the migration, not here.
    Fail loudly if they are missing rather than as a raw FK violation."""
    gems = {code for (code,) in db.query(GemstoneCategory.code)}
    objectives = {code for (code,) in db.query(StrategicObjective.code)}
    missing = sorted((set(GEMSTONE_CODE_BY_NAME.values()) - gems)
                     | (set(OBJECTIVE_CODE_BY_NAME.values()) - objectives))
    if missing:
        raise RuntimeError(
            f"strategy vocabulary missing from the database: {', '.join(missing)}. "
            "The migration seeds it; run `alembic upgrade head` first."
        )


# ── The tables ───────────────────────────────────────────────────────────────

def _load_playbooks(db: Session, report: LoadReport,
                    files: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """One platform playbook per file. Returns `(slug, document)` for every
    playbook loaded, in file order."""
    diff = _diff(report, "playbooks")
    delivered = {r["slug"] for r in report_manifest()}
    current = {r.slug: r for r in db.query(Playbook)}
    loaded: list[tuple[str, dict]] = []
    seen: dict[str, str] = {}
    for fname, doc in files:
        cat = doc.get("category") or {}
        slug, name, family = cat.get("id"), cat.get("name"), cat.get("family")
        if not slug or not name:
            diff.skipped.append((fname, "category has no id or name"))
            continue
        if slug in seen:
            diff.skipped.append((fname, f"category id {slug!r} already loaded from {seen[slug]}"))
            continue
        seen[slug] = fname
        if slug not in delivered:
            diff.skipped.append((slug, "no delivered report with this slug; report_slug left NULL"))
        for key in _SHIM_VALUES:
            if cat.get(key) is not None:
                diff.skipped.append((slug, f"category.{key} {cat[key]!r} is a shim field "
                                           "(team data); not loaded"))
        fields = {
            "name": name,
            "family": family,
            "report_slug": slug if slug in delivered else None,
            "legacy_line_key": make_line_key(family, name) if family else None,
            "kraljic": cat.get("kraljic"),
            "last_updated": _last_updated(cat.get("lastUpdated"), slug, diff),
            "meta": _rest(cat, _CATEGORY_COLUMNS + _SHIM_FIELDS),
        }
        _upsert(db, diff, current, slug, fields, lambda f, s=slug: Playbook(slug=s, **f))
        loaded.append((slug, doc))
    diff.stale += len(set(current) - set(seen))
    db.flush()
    return loaded


def _load_levers(db: Session, report: LoadReport, playbooks: list[tuple[str, dict]]
                 ) -> dict[str, tuple[PlaybookLever, dict]]:
    """Every authored lever, upserted by `lever_code` — never deleted and
    reinserted (see the module docstring). Returns code → (row, authored)."""
    diff = _diff(report, "playbook_levers")
    current = {r.lever_code: r for r in db.query(PlaybookLever)}
    existing = set(current)
    loaded: dict[str, tuple[PlaybookLever, dict]] = {}
    for slug, doc in playbooks:
        for pos, opp in enumerate(doc.get("opportunities") or []):
            code = opp.get("id")
            if not code:
                diff.skipped.append((f"{slug} #{pos}", "lever has no id"))
                continue
            if code in loaded:
                diff.skipped.append((code, f"lever id already loaded from "
                                           f"{loaded[code][0].playbook_slug}"))
                continue
            fields = lever_fields(slug, pos, opp, diff)
            if fields is None:
                continue
            row = _upsert(db, diff, current, code, fields,
                          lambda f, c=code: PlaybookLever(lever_code=c, **f))
            loaded[code] = (row, opp)
    diff.stale += len(existing - set(loaded))
    db.flush()   # new levers need their ids before their objective links
    return loaded


def _load_lever_objectives(db: Session, report: LoadReport,
                           levers: dict[str, tuple[PlaybookLever, dict]]) -> None:
    """Each lever's objective links, exactly as its `objectives` array lists
    them. Links of a stale lever are left with it."""
    diff = _diff(report, "playbook_lever_objectives")
    children: dict[int, dict] = defaultdict(dict)
    for link in db.query(PlaybookLeverObjective):
        children[link.lever_id][link.objective_code] = link
    for code, (lever, opp) in levers.items():
        desired: dict[str, dict] = {}
        for name in opp.get("objectives") or []:
            objective = OBJECTIVE_CODE_BY_NAME.get(name)
            if objective is None:
                diff.skipped.append((code, f"objective {name!r} is not one of the 7"))
                continue
            desired[objective] = {}
        _sync_children(
            db, diff, children[lever.id], desired,
            lambda k, _f, lid=lever.id: PlaybookLeverObjective(lever_id=lid, objective_code=k))
    db.flush()


def _load_playbook_objectives(db: Session, report: LoadReport,
                              playbooks: list[tuple[str, dict]]) -> None:
    """Each playbook's authored `{type, priority, note}` objectives, in file
    order. Rows of a stale playbook are left with it."""
    diff = _diff(report, "playbook_objectives")
    children: dict[str, dict] = defaultdict(dict)
    for row in db.query(PlaybookObjective):
        children[row.playbook_slug][row.objective_code] = row
    for slug, doc in playbooks:
        desired: dict[str, dict] = {}
        for pos, entry in enumerate(doc.get("objectives") or []):
            code = OBJECTIVE_CODE_BY_NAME.get(entry.get("type"))
            if code is None:
                diff.skipped.append((slug, f"objective {entry.get('type')!r} is not one of the 7"))
                continue
            if code in desired:
                diff.skipped.append((slug, f"objective {entry['type']!r} is listed twice; "
                                           "the first is kept"))
                continue
            priority = entry.get("priority")
            if priority is not None and priority not in PRIORITIES:
                diff.skipped.append((slug, f"{entry['type']}: priority {priority!r} is not "
                                           f"one of {', '.join(PRIORITIES)}; stored NULL"))
                priority = None
            desired[code] = {"priority": priority, "note": entry.get("note"), "sort_order": pos}
        _sync_children(
            db, diff, children[slug], desired,
            lambda k, f, s=slug: PlaybookObjective(playbook_slug=s, objective_code=k, **f))
    db.flush()


# ── Checks ───────────────────────────────────────────────────────────────────

def _report_checks(db: Session, report: LoadReport, playbooks: list[tuple[str, dict]],
                   levers: dict[str, tuple[PlaybookLever, dict]]) -> None:
    """The distributions of the loaded levers, the coagulants spot check, and
    whether every playbook shows exactly its authored levers in file order —
    read back from the stored rows, so a stale lever still attached to a
    playbook shows up here."""
    rows = [row for row, _ in levers.values()]
    by_gem = Counter(r.gemstone_code for r in rows)
    for code, _name, _colour in GEMSTONES:
        if by_gem.get(code):
            _diff(report, f"{CHECK_GEMSTONE}{code}").unchanged = by_gem[code]
    by_status = Counter(r.default_status for r in rows)
    for status in (*LEVER_STATUSES, None):
        if by_status.get(status):
            _diff(report, f"{CHECK_STATUS}{status or 'unset'}").unchanged = by_status[status]
    by_applies = Counter(r.default_applies for r in rows)
    for value, label in ((True, "yes"), (False, "no"), (None, "unset")):
        if by_applies.get(value):
            _diff(report, f"{CHECK_APPLIES}{label}").unchanged = by_applies[value]

    stored: dict[str, list[str]] = defaultdict(list)
    for slug, code in (db.query(PlaybookLever.playbook_slug, PlaybookLever.lever_code)
                       .order_by(PlaybookLever.playbook_slug, PlaybookLever.sort_order,
                                 PlaybookLever.id)):
        stored[slug].append(code)
    sets = _diff(report, CHECK_LEVER_SETS)
    for slug, doc in playbooks:
        authored = [o.get("id") for o in doc.get("opportunities") or []]
        if stored.get(slug, []) == authored:
            sets.unchanged += 1
        else:
            extra = [c for c in stored.get(slug, []) if c not in authored]
            missing = [c for c in authored if c not in stored.get(slug, [])]
            sets.skipped.append((slug, f"stored levers differ from the file: "
                                       f"{len(missing)} missing, {len(extra)} not in the file"
                                       f"{' (stale, kept for team state)' if extra else ''}"))
    if any(slug == SPOT_CHECK_SLUG for slug, _ in playbooks):
        _diff(report, CHECK_SPOT).unchanged = len(stored.get(SPOT_CHECK_SLUG, []))


# ── Entry point ──────────────────────────────────────────────────────────────

def load(db: Session, report: LoadReport) -> LoadReport:
    """Load the playbooks, their levers and objectives. Flushes, never
    commits."""
    _check_vocabulary(db)
    playbooks = _load_playbooks(db, report, read_playbooks())
    levers = _load_levers(db, report, playbooks)
    _load_lever_objectives(db, report, levers)
    _load_playbook_objectives(db, report, playbooks)
    _report_checks(db, report, playbooks, levers)
    return report
