"""Converting the numbers typed into reports into live tokens (LIVE-4 / SCRUM-211, S4).

    propose(db)          S2's findings → rows in `report_live_conversions`
    apply_to_html(...)   approved rows → tokens in one section's HTML
    revert_html(...)     tokens → the typed numbers again (the safety check)
    apply_stored(db)     approved rows → the stored reports, now
    apply_parsed(...)    approved rows → a report as the loader parses it, on every load

**What is proposed** (reports only; decisions 4 and 6, PROPOSED for review):

* cost bars matched to recipe lines (by type code, else line name) →
  `{{bar:REPORT|code=…}}` or `{{bar:REPORT|line=…}}`;
* "as of" dates next to one series whose month could be read →
  `{{index:KEY|asof}}`;
* % moves next to one series in a sentence that clearly means a year →
  `{{index:KEY|change12m}}`.

Every token carries the typed number as its fallback. A row whose typed
number **still equals today's** is approved at once — nothing a reader sees
changes. A **drifted** one is `proposed`: readers will see the number change,
so a person approves it first.

**Applied only where it was found.** A bar is found by its label and must
still show the typed weight; a date or move by the exact text it sits in, in
which the typed number must appear once. Anything else is skipped with the
reason — never converted somewhere else. Applying is idempotent.

**Reversible, and checked.** Putting each token back to its typed number must
give back the original HTML exactly; a section that does not is left alone.
That is the proof a conversion changed nothing but the numbers.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from bs4.element import NavigableString
from sqlalchemy.orm import Session

from app.models.live_conversion import ReportLiveConversion
from app.models.market_report import MarketReportPanel, MarketReportSection
from app.services.content_drop.report_sanitize import (
    ReportRejected, parse, sanitize_html, serialize,
)
from app.services.live_resolver import report_recipe_weights
from app.services.live_values import LiveText, derived_from, scan_html, scan_text
from app.services.number_inventory import COST_BAR, DATE, PCT_MOVE, inventory
from app.services.number_matching import DRIFTED, MATCH, match_items

APPROVED = "approved"
PROPOSED = "proposed"
REJECTED = "rejected"
AUTO = "auto: typed number equals today's"


def _norm(s: str) -> str:
    return " ".join(s.split())


def _typed_re(typed: str) -> re.Pattern:
    return re.compile(re.escape(_norm(typed)).replace(r"\ ", r"\s+"))


def _in_bar(node) -> bool:
    return node.find_parent(class_="cost-bar") is not None


def _text_nodes(soup):
    return [n for n in soup.find_all(string=True) if type(n) is NavigableString and not _in_bar(n)]


def _label_without_code(label: str) -> str:
    return _norm(re.sub(r"\s*\([^)]*\)\s*", " ", label))


# ── Propose ──────────────────────────────────────────────────────────────────

@dataclass
class ProposeReport:
    created: dict[str, int] = field(default_factory=dict)
    approved_now: int = 0
    already: int = 0
    no_anchor: int = 0


def _anchor_for(html: str, typed: str, sentence: str) -> str | None:
    """The one text node the typed number sits in, found from its sentence."""
    rx = _typed_re(typed)
    sentence = _norm(sentence)
    m = rx.search(sentence)
    if not m:
        return None
    window = sentence[max(0, m.start() - 25):m.end() + 25]
    nodes = [n for n in _text_nodes(parse(html)) if rx.search(_norm(n))]
    hits = [n for n in nodes if window in _norm(n)] or [n for n in nodes if _norm(n) in sentence]
    if len({_norm(n) for n in hits}) != 1:
        return None
    anchor = _norm(hits[0])
    return anchor if len(rx.findall(anchor)) == 1 else None


def _with_fallback(token: str, typed: str) -> str:
    return f"{token[:-2]}|fallback={_norm(typed)}}}}}"


def propose(db: Session, *, as_of: date | None = None) -> ProposeReport:
    """Rows for every convertible typed number in the reports. An existing row
    (same place, same typed number) is left as it is, decision included."""
    report = ProposeReport()
    items = inventory(db, include_blocks=False)
    matches = [m for m in match_items(db, items, as_of=as_of)
               if m.status in (MATCH, DRIFTED) and m.confidence == "high"
               and m.item.kind in (COST_BAR, DATE, PCT_MOVE)]
    by_code, _ = report_recipe_weights(db)
    html_of = {(s, sid): h for s, sid, h in db.query(
        MarketReportSection.slug, MarketReportSection.section_id, MarketReportSection.html)}
    existing = {(r.slug, r.section_id, r.kind, r.anchor, r.typed)
                for r in db.query(ReportLiveConversion)}
    now = datetime.now(timezone.utc)

    for m in matches:
        it = m.item
        if it.kind == COST_BAR:
            code = it.extra.get("code")
            label = it.extra.get("label", "")
            selector = (f"code={code}" if code and by_code.get(it.doc, {}).get(code)
                        else f"line={_label_without_code(label)}")
            anchor, typed = _norm(label), _norm(it.text)
            token = f"{{{{bar:{it.doc}|{selector}|fallback={typed}}}}}"
        else:
            anchor = _anchor_for(html_of.get((it.doc, it.part), ""), it.text, it.context)
            if anchor is None:
                report.no_anchor += 1
                continue
            typed = _norm(it.text)
            token = _with_fallback(m.token, typed)

        key = (it.doc, it.part, it.kind, anchor, typed)
        if key in existing:
            report.already += 1
            continue
        existing.add(key)
        same = m.status == MATCH
        db.add(ReportLiveConversion(
            slug=it.doc, section_id=it.part, kind=it.kind, anchor=anchor, typed=typed,
            token=token, today=m.today, status=APPROVED if same else PROPOSED,
            decided_by=AUTO if same else None, decided_at=now if same else None,
        ))
        report.created[it.kind] = report.created.get(it.kind, 0) + 1
        report.approved_now += same
    db.flush()
    return report


# ── Apply / revert one section ───────────────────────────────────────────────

def apply_to_html(html: str, rows: list[ReportLiveConversion]
                  ) -> tuple[str, list[ReportLiveConversion], list[tuple[ReportLiveConversion, str]]]:
    """`html` with each row's typed number replaced by its token, where the row
    still finds it. Returns (html, applied, [(skipped row, why)])."""
    soup = parse(html)
    applied: list[ReportLiveConversion] = []
    skipped: list[tuple[ReportLiveConversion, str]] = []

    bars = {}
    for bar in soup.find_all(class_="cost-bar"):
        label = bar.find(class_="label")
        if label is not None:
            bars.setdefault(_norm(label.get_text(" ")), []).append(bar)
    for row in (r for r in rows if r.kind == COST_BAR):
        found = bars.get(row.anchor, [])
        if len(found) != 1:
            skipped.append((row, "bar not found" if not found else "label appears on several bars"))
            continue
        weight = found[0].find(class_="weight")
        shown = _norm(weight.get_text(" ")) if weight is not None else ""
        if shown == row.token:
            applied.append(row)                  # converted already
        elif shown != row.typed:
            skipped.append((row, f"the bar now shows {shown!r}, not {row.typed!r}"))
        else:
            weight.clear()
            weight.append(NavigableString(row.token))
            applied.append(row)

    by_anchor: dict[str, list[ReportLiveConversion]] = defaultdict(list)
    for row in (r for r in rows if r.kind != COST_BAR):
        by_anchor[row.anchor].append(row)
    nodes = _text_nodes(soup)
    for anchor, group in by_anchor.items():
        spans, ok = [], []
        for row in group:
            hits = list(_typed_re(row.typed).finditer(anchor))
            if len(hits) != 1:
                skipped.append((row, f"the number appears {len(hits)} times in its text"))
                continue
            spans.append((hits[0].start(), hits[0].end(), row))
            ok.append(row)
        if not ok:
            continue
        converted = anchor
        for start, end, row in sorted(spans, key=lambda x: -x[0]):
            converted = converted[:start] + row.token + converted[end:]
        if any(_norm(n) == converted for n in nodes):
            applied += ok                        # converted already
            continue
        found = [n for n in nodes if _norm(n) == anchor]
        if len(found) != 1:
            why = "text not found" if not found else "the text appears more than once"
            skipped += [(row, why) for row in ok]
            continue
        node = found[0]
        s = str(node)
        for _start, _end, row in sorted(spans, key=lambda x: -x[0]):
            m = list(_typed_re(row.typed).finditer(s))[0]
            s = s[:m.start()] + row.token + s[m.end():]
        node.replace_with(NavigableString(s))
        applied += ok
    return serialize(soup.contents), applied, skipped


def revert_html(html: str, rows: list[ReportLiveConversion]) -> str:
    """Each row's token back to its typed number (the check that a conversion
    changed nothing else)."""
    soup = parse(html)
    for node in soup.find_all(string=True):
        if type(node) is not NavigableString or "{{" not in node:
            continue
        s = str(node)
        for row in rows:
            s = s.replace(row.token, row.typed)
        if s != str(node):
            node.replace_with(NavigableString(s))
    return serialize(soup.contents)


# ── Stored reports, and every load ───────────────────────────────────────────

@dataclass
class ApplyReport:
    sections_changed: int = 0
    panels_changed: int = 0
    applied: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    not_reversible: list[str] = field(default_factory=list)


# Section 7 ("strategic") is cut into these panels for the Strategy tab; a
# conversion in it is looked for in each of them.
STRATEGIC_PANELS = ("pestel", "porter", "market_drivers", "kraljic", "outlook")


def _rows_for_panel(approved: dict, slug: str, panel: str) -> list:
    rows = list(approved.get((slug, panel), []))
    if panel in STRATEGIC_PANELS:
        rows += approved.get((slug, "strategic"), [])
    return rows


def _approved(db: Session, slugs=None) -> dict[tuple[str, str], list[ReportLiveConversion]]:
    q = db.query(ReportLiveConversion).filter(ReportLiveConversion.status == APPROVED)
    if slugs is not None:
        q = q.filter(ReportLiveConversion.slug.in_(list(slugs)))
    out: dict[tuple[str, str], list[ReportLiveConversion]] = defaultdict(list)
    for row in q:
        out[(row.slug, row.section_id)].append(row)
    return out


def _convert(html: str, rows, where: str, report: ApplyReport, count: bool) -> str | None:
    """The converted HTML, or None when nothing changes or the check fails."""
    new, applied, skipped = apply_to_html(html, rows)
    if count:
        for row in applied:
            report.applied[row.kind] = report.applied.get(row.kind, 0) + 1
        report.skipped += [f"{where} #{row.id} {row.kind} {row.typed!r}: {why}" for row, why in skipped]
    if new == html:
        return None
    if revert_html(new, rows) != revert_html(html, rows):
        report.not_reversible.append(where)
        return None
    return new


def apply_stored(db: Session) -> ApplyReport:
    """Approved rows applied to the reports as stored, sections and the panels
    cut from them. Flushes; the caller commits (or rolls back for a dry run)."""
    report = ApplyReport()
    approved = _approved(db)
    if not approved:
        return report
    slugs = {slug for slug, _ in approved}
    for row in db.query(MarketReportSection).filter(MarketReportSection.slug.in_(slugs)):
        rows = approved.get((row.slug, row.section_id))
        if rows:
            new = _convert(row.html, rows, f"{row.slug}/{row.section_id}", report, count=True)
            if new is not None:
                row.html = new
                row.derived_from = derived_from(scan_html(new)[0])
                report.sections_changed += 1
    for row in db.query(MarketReportPanel).filter(MarketReportPanel.slug.in_(slugs)):
        rows = _rows_for_panel(approved, row.slug, row.panel)
        if rows:
            new = _convert(row.html, rows, f"{row.slug}/panel {row.panel}", report, count=False)
            if new is not None:
                row.html = new
                row.derived_from = derived_from(scan_html(new)[0] + scan_text(row.data)[0])
                report.panels_changed += 1
    db.flush()
    return report


def apply_parsed(db: Session, slug: str, sections: dict, panels: dict) -> list[str]:
    """For the report loader: approved rows applied to a freshly parsed report
    (its `sections` / `panels` field dicts, changed in place). Returns notes
    for what could not be applied."""
    approved = _approved(db, [slug])
    if not approved:
        return []
    report = ApplyReport()
    for parts, count in ((sections, True), (panels, False)):
        for name, f in parts.items():
            rows = approved.get((slug, name)) if count else _rows_for_panel(approved, slug, name)
            if not rows:
                continue
            new = _convert(f["html"], rows, f"{slug}/{name}", report, count=count)
            if new is not None:
                f["html"] = new
                f["derived_from"] = derived_from(scan_html(new)[0] + scan_text(f.get("data"))[0])
    return ([f"live conversion skipped: {s}" for s in report.skipped]
            + [f"live conversion not applied (not reversible): {w}" for w in report.not_reversible])


# ── Verification (S5) ────────────────────────────────────────────────────────

@dataclass
class VerifyReport:
    sections: int = 0
    panels: int = 0
    tokens: int = 0
    missing: list[str] = field(default_factory=list)     # a value fell back: no data now
    leftovers: list[str] = field(default_factory=list)   # a raw token would reach a reader
    unsafe: list[str] = field(default_factory=list)      # filled HTML failed the sanitiser


def verify(db: Session) -> VerifyReport:
    """Every section and panel carrying tokens, filled as a reader gets it:
    no raw token left, still clean HTML, and which values fall back."""
    report = VerifyReport()
    live = LiveText(db)
    parts = ([("section", r.slug, r.section_id, r.html, r.derived_from)
              for r in db.query(MarketReportSection).filter(MarketReportSection.derived_from.isnot(None))]
             + [("panel", r.slug, r.panel, r.html, r.derived_from)
                for r in db.query(MarketReportPanel).filter(MarketReportPanel.derived_from.isnot(None))])
    for kind, slug, name, html, record in parts:
        where = f"{slug}/{kind} {name}"
        setattr(report, kind + "s", getattr(report, kind + "s") + 1)
        report.tokens += len(record.get("tokens", []))
        out = live.html(html)
        if "{{" in out:
            report.leftovers.append(where)
        try:
            if sanitize_html(out) != out:
                report.unsafe.append(where)
        except ReportRejected:
            report.unsafe.append(where)
        if "live-missing" in out:
            report.missing.append(where)
    return report
