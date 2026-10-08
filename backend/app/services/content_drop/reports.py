"""Report loader — the delivered market reports (spec D6; design §2.6, §3.2).

Loads, as platform rows (no `team_id`):

    market_reports          one per MANIFEST entry, keyed by slug
    market_report_sections  the seven `<section id>` fragments of each, sanitised
    market_report_panels    the same content cut for the Strategy tab: the six
                            sections 1–6 as panels, section 7 split at its <h3>
                            into pestel / porter / market_drivers / kraljic
                            (+ outlook, in the files that carry one)
    market_report_lines     the join from a report to the product lines it serves
    product_lines.report_slug  filled for lines outside v1 that one report reaches

**What a report row carries.** `name`, `family` and `old_line_name` from
MANIFEST (report names are pre-September line names), `old_line_key` =
`family|||name`, `as_of` from the header's "CostAdvisor · <Month> <Year>"
(first of that month), `in_v1_scope` when the file is a `v1_scope.report_map`
target, `source_sha256` of the file's bytes, `word_count` (prose, all seven
sections) and `kraljic`.

**Sections** keep their `<h2>`; the wrapper `<section>` is stripped and
`heading` is the h2 text. **Panels** carry their heading in `heading` and
leave it out of `html`. Section 7's blocks are found by their h3 wording
(PESTEL, Porter, Market drivers, Kraljic, Outlook). The four canonical blocks
must each be there once; ordinals are fixed (sections 1–6, then pestel 7,
porter 8, market_drivers 9, kraljic 10, outlook 11) whatever the authored
order. Section 7 ends with one `.src` attribution for the whole section; the
split is positional, so it travels with the last block (the Kraljic panel,
or Outlook where a file ends with one).

`panels.data` holds what reads reliably out of the markup; the html is the
fallback wherever it does not:

* pestel — `{columns, rows: [{factor, text, implication}]}` from its table;
* porter — `{columns, rows: [{force, question, assessment, implication}]}`:
  `force` is the parenthesised force name when the cell has one, `question`
  the bold question before it. A table that is not three columns gets no
  data;
* market_drivers — `{cards: [{title, arrow, text}]}`, `arrow` only when the
  heading starts with ↑ ↓ → ↔;
* kraljic — the report's `kraljic` plus `narrative` (its paragraphs).

**Kraljic** is read from the chart, not assumed: `cx`/`cy` of the SVG circle,
`quadrant` = the label of the SVG quadrant rectangle that contains the dot,
`lead` = the narrative's opening bold statement as authored, `note` = the
author's HTML comment inside the SVG (comments are dropped from the stored
HTML, so this is where it survives).

**Sanitised or rejected.** Every section goes through `report_sanitize` (the
measured allowlist). A report with any violation, or without its seven
sections / four canonical section-7 blocks, is REJECTED: a skip line on
`market_reports` saying why, and nothing written for it. A previously loaded
report that now fails is left as it was (reported, not deleted).

**The line join** (`derive_report_lines`), per report:

* `report_map`: `v1_scope.report_map` (line key → report file). Wins over
  key_map for the same line.
* `key_map`: the report's old keys through `supply_axis.key_map`. The old
  keys are `family|||MANIFEST name` plus the `report_old_line` of the v1 lines
  the report serves — the second adds the old keys a merged report absorbed,
  which MANIFEST does not name. An old key that is still a record's line key
  in FORMULA_COMBOS joins to that key itself.
* `product_line_id` is the current product line whose `line_key` is the join
  key, else the one current line that lists the key in its `former_keys` (a
  line renamed since the join was written) — `taxonomy.line_index`, the rule
  the catalogue and the placements use. A key that is neither (the record key
  of a product whose line is not yet published, or a retired line) keeps its
  row with `product_line_id` NULL: the join is recorded but leads to no line.
  Consumers must not show such a key (design decision 37).

**product_lines.report_slug**: the taxonomy loader writes it on every line with
this module's `derive_line_report_slugs`, so after a taxonomy load this step
finds every slug already in place and changes nothing. It still fills a line
outside v1 that exactly one report reaches when the slug is missing, and never
touches a v1 line. A line several reports reach is left alone and reported:
picking one would be a judgement the drop does not make
(`market_report_lines` holds every join).

Idempotent by comparison, like the other content loaders: a second run
reports zero changes. Report and section/panel/line rows the drop no longer
produces are deleted (they are derived from the files and nothing references
them); a report the MANIFEST no longer lists is reported stale and kept.

**Never commits.** The CLI owns the transaction (`seed_content_drop.py`).
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from bs4.element import Comment, Tag
from sqlalchemy.orm import Session

from app.models.market_report import (
    SECTION_IDS, MarketReport, MarketReportLine, MarketReportPanel, MarketReportSection,
)
from app.models.product_line import ProductLine
from app.services.drop.reader import DropNotAvailable
from app.services.drop.report import LoadReport
from app.services.content_drop import report_sanitize as rs
from app.services.content_drop.reader import (
    LINE_KEY_SEP, axis, drop_dir, line_key_of, make_line_key, raw, report_html,
    report_manifest, scope,
)
from app.services.content_drop.taxonomy import _diff, _sync_children, _upsert, line_index

# Section 7's blocks, found by their h3 wording (lowercased substring).
SECTION7_PANELS = (
    ("pestel", "pestel"),
    ("porter", "porter"),
    ("market drivers", "market_drivers"),
    ("kraljic", "kraljic"),
    ("outlook", "outlook"),
)
CANONICAL_SECTION7 = ("pestel", "porter", "market_drivers", "kraljic")
PANEL_ORDER = SECTION_IDS[:6] + CANONICAL_SECTION7 + ("outlook",)
PANEL_ORDINAL = {panel: pos for pos, panel in enumerate(PANEL_ORDER, start=1)}

DRIVER_ARROWS = frozenset("↑↓→↔")
_MONTHS = {m: i for i, m in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"), start=1)}
_AS_OF_RE = re.compile(r"CostAdvisor\s*·\s*(" + "|".join(_MONTHS) + r")\s+(\d{4})")
_PARENTHESISED_RE = re.compile(r"\(([^()]+)\)\s*$")
_RAW_SECTION_RE = re.compile(r"<section\b[^>]*\bid=\"([\w-]+)\"[^>]*>(.*?)</section>", re.S)

# Report rows (on top of the table rows) that carry checks, not writes.
STRUCTURE_CHECK = "check: report structure"
LINES_CHECK = "check: lines with a report"
REPORT_SLUG_ROW = "product_lines.report_slug"


# ── Parsing one report (no database) ─────────────────────────────────────────

@dataclass
class ParsedReport:
    slug: str
    fields: dict                       # MarketReport columns
    sections: dict[str, dict]          # section_id → MarketReportSection columns
    panels: dict[str, dict]            # panel → MarketReportPanel columns
    notes: list[str] = field(default_factory=list)


def _structure_error(message: str) -> rs.ReportRejected:
    return rs.ReportRejected([message])


def _tags(nodes) -> list[Tag]:
    return [n for n in nodes if isinstance(n, Tag)]


def _as_of(soup) -> date | None:
    header = soup.find("header")
    match = _AS_OF_RE.search(rs.text_of(header)) if header else None
    return date(int(match.group(2)), _MONTHS[match.group(1)], 1) if match else None


def _number(value: str) -> int | float:
    num = float(value)
    return int(num) if num.is_integer() else num


def _kraljic(block: list, notes: list[str]) -> dict | None:
    """The dot, its quadrant (from the chart's own rectangles and labels),
    the narrative's lead and the author's placement comment."""
    svg = next((el if el.name == "svg" else el.find("svg")
                for el in _tags(block) if el.name == "svg" or el.find("svg")), None)
    circle = svg.find("circle") if svg else None
    if circle is None or not (circle.get("cx") and circle.get("cy")):
        notes.append("kraljic: no chart dot; kraljic left NULL")
        return None
    cx, cy = _number(circle["cx"]), _number(circle["cy"])

    labels = [(float(t["x"]), float(t["y"]), rs.text_of(t)) for t in svg.find_all("text")
              if t.get("x") and t.get("y")]
    quadrant = None
    for rect in svg.find_all("rect"):
        if not all(rect.get(a) for a in ("x", "y", "width", "height")):
            continue
        x, y = float(rect["x"]), float(rect["y"])
        w, h = float(rect["width"]), float(rect["height"])
        if x < cx < x + w and y < cy < y + h:
            inside = [text for tx, ty, text in labels if x < tx < x + w and y < ty < y + h]
            quadrant = inside[0] if len(inside) == 1 else None
            break
    if quadrant is None:
        notes.append(f"kraljic: dot ({cx}, {cy}) is not inside one labelled quadrant; "
                     "quadrant left NULL")

    paragraphs = [p for el in _tags(block) for p in ([el] if el.name == "p" else el.find_all("p"))
                  if "src" not in rs.classes_of(p)]
    analysis = next((el.find(class_="analysis") for el in _tags(block)
                     if el.find(class_="analysis")), None)
    first_p = analysis.find("p") if analysis else (paragraphs[0] if paragraphs else None)
    lead_tag = first_p.find("strong") if first_p else None
    comment = next((c for c in svg.descendants if isinstance(c, Comment)), None)
    return {
        "cx": cx,
        "cy": cy,
        "quadrant": quadrant,
        "lead": rs.text_of(lead_tag) if lead_tag else None,
        "note": " ".join(str(comment).split()) if comment else None,
        "narrative": [rs.text_of(p) for p in paragraphs],
    }


def _table_rows(block: list) -> tuple[list[str], list[list[Tag]]] | None:
    tables = [el for el in _tags(block) if el.name == "table"]
    if len(tables) != 1:
        return None
    table = tables[0]
    columns = [rs.text_of(th) for th in table.find_all("th")]
    body = table.find("tbody") or table
    return columns, [row.find_all("td") for row in body.find_all("tr")]


def _pestel(block: list, notes: list[str]) -> dict | None:
    parsed = _table_rows(block)
    if not parsed or any(len(cells) != 3 for cells in parsed[1]):
        notes.append("pestel: not one three-column table; data left NULL (html kept)")
        return None
    columns, rows = parsed
    if len(rows) != 6:
        notes.append(f"pestel: {len(rows)} rows, not 6")
    return {"columns": columns, "rows": [
        {"factor": rs.text_of(a), "text": rs.text_of(b), "implication": rs.text_of(c)}
        for a, b, c in rows]}


def _porter(block: list, notes: list[str]) -> dict | None:
    parsed = _table_rows(block)
    if not parsed or any(len(cells) != 3 for cells in parsed[1]):
        notes.append("porter: not one three-column table; data left NULL (html kept)")
        return None
    columns, rows = parsed
    if len(rows) != 5:
        notes.append(f"porter: {len(rows)} rows, not 5")
    out = []
    for first, assessment, implication in rows:
        cell = rs.text_of(first)
        named = _PARENTHESISED_RE.search(cell)
        question = first.find("strong")
        out.append({
            "force": named.group(1).strip() if named else cell,
            "question": rs.text_of(question) if named and question else None,
            "assessment": rs.text_of(assessment),
            "implication": rs.text_of(implication),
        })
    return {"columns": columns, "rows": out}


def _market_drivers(block: list, notes: list[str]) -> dict | None:
    cards = [card for el in _tags(block) for card in el.find_all(class_="driver-card")]
    out = []
    for card in cards:
        heading, body = card.find("h4"), card.find("p")
        if heading is None or body is None:
            notes.append("market_drivers: a card without its heading or text; data left NULL")
            return None
        title = rs.text_of(heading)
        arrow = title[0] if title and title[0] in DRIVER_ARROWS else None
        out.append({"title": title[1:].strip() if arrow else title, "arrow": arrow,
                    "text": rs.text_of(body)})
    if not out:
        notes.append("market_drivers: no driver cards; data left NULL")
        return None
    return {"cards": out}


def _section7_blocks(section: Tag, notes: list[str]) -> dict[str, tuple[str, list]]:
    """panel → (h3 text, the block's nodes after its h3). Raises on a missing
    or repeated canonical block; an unrecognised block is noted, not stored."""
    blocks: list[tuple[str | None, str, list]] = []
    preamble: list = []
    for node in section.children:
        if isinstance(node, Tag) and node.name == "h3":
            heading = rs.text_of(node)
            panel = next((p for key, p in SECTION7_PANELS if key in heading.lower()), None)
            blocks.append((panel, heading, []))
        elif blocks:
            blocks[-1][2].append(node)
        elif not (isinstance(node, Tag) and node.name == "h2"):
            preamble.append(node)
    if rs.word_count(preamble):
        notes.append("strategic: content before the first <h3> is in the section, not a panel")
    found: dict[str, tuple[str, list]] = {}
    for panel, heading, nodes in blocks:
        if panel is None:
            notes.append(f"strategic: unrecognised block {heading!r} is in the section, "
                         "not a panel")
            continue
        if panel in found:
            raise _structure_error(f"section 7 repeats its {panel} block")
        found[panel] = (heading, nodes)
    missing = [p for p in CANONICAL_SECTION7 if p not in found]
    if missing:
        raise _structure_error(f"section 7 lacks its {', '.join(missing)} block(s)")
    authored = [panel for panel, _h, _n in blocks if panel]
    if "outlook" in found:
        notes.append(f"strategic: carries an outlook block (authored order: {', '.join(authored)})")
    return found


def parse_report(entry: dict, source: str, *, in_v1_scope: bool,
                 source_sha256: str | None = None) -> ParsedReport:
    """Everything one report loads as, from its MANIFEST entry and its HTML.
    Raises `ReportRejected` for anything outside the allowlist or a broken
    section structure."""
    slug = entry["slug"]
    notes: list[str] = []
    soup = rs.parse(source)

    found = soup.find_all("section")
    ids = [s.get("id") for s in found]
    if tuple(ids) != SECTION_IDS:
        raise _structure_error(f"sections are {ids}, expected {list(SECTION_IDS)}")

    problems: list[str] = []
    for section in found:
        extra = sorted(set(section.attrs) - {"id"})
        if extra:
            problems.append(f"attribute(s) {extra} on <section id={section['id']}>")
        problems += [f"{section['id']}: {v}" for v in rs.violations(section.children)]
    if problems:
        raise rs.ReportRejected(problems)

    for sid, body in _RAW_SECTION_RE.findall(source):
        for tag, n in rs.unclosed_tags(body).items():
            notes.append(f"{sid}: {n} unclosed <{tag}>, closed where its parent ends")

    sections: dict[str, dict] = {}
    panels: dict[str, dict] = {}
    for ordinal, section in enumerate(found, start=1):
        sid = section["id"]
        children = list(section.children)
        h2 = section.find("h2")
        heading = rs.text_of(h2) if h2 else None
        sections[sid] = {
            "ordinal": ordinal,
            "heading": heading,
            "html": rs.serialize(children).strip(),
            "word_count": rs.word_count(children),
            "src_block_count": len(section.find_all(class_="src")),
        }
        if sid != "strategic":
            body = [n for n in children if n is not h2]
            panels[sid] = {"ordinal": PANEL_ORDINAL[sid], "heading": heading,
                           "html": rs.serialize(body).strip(), "data": None}

    kraljic = None
    for panel, (heading, nodes) in _section7_blocks(found[-1], notes).items():
        data = None
        if panel == "pestel":
            data = _pestel(nodes, notes)
        elif panel == "porter":
            data = _porter(nodes, notes)
        elif panel == "market_drivers":
            data = _market_drivers(nodes, notes)
        elif panel == "kraljic":
            data = _kraljic(nodes, notes)
            if data is not None:
                kraljic = {k: v for k, v in data.items() if k != "narrative"}
        panels[panel] = {"ordinal": PANEL_ORDINAL[panel], "heading": heading,
                         "html": rs.serialize(nodes).strip(), "data": data}

    as_of = _as_of(soup)
    if as_of is None:
        notes.append("header states no 'CostAdvisor · <Month> <Year>'; as_of left NULL")
    family, name = entry.get("family"), entry["name"]
    return ParsedReport(
        slug=slug,
        fields={
            "report_file": entry["delivered"],
            "name": name,
            "family": family,
            "old_line_name": name,
            "old_line_key": make_line_key(family, name) if family else None,
            "as_of": as_of,
            "in_v1_scope": in_v1_scope,
            "source_sha256": source_sha256,
            "kraljic": kraljic,
            "word_count": sum(s["word_count"] for s in sections.values()),
        },
        sections=sections,
        panels=panels,
        notes=notes,
    )


def read_report(file: str) -> tuple[str, str]:
    """(text, sha256 of the file's bytes). The hash is of the delivered bytes
    (some files are CRLF), so it matches `sha256sum` on the file."""
    text = report_html(file)                    # also refuses a path
    digest = hashlib.sha256((drop_dir() / "reports" / file).read_bytes()).hexdigest()
    return text, digest


# ── The line join (no database) ──────────────────────────────────────────────

def derive_report_lines() -> dict[str, dict[str, str]]:
    """slug → {line_key: source} for every MANIFEST report (see the module
    docstring for the rules). report_map wins over key_map on the same line."""
    manifest = report_manifest()
    slug_by_file = {r["delivered"]: r["slug"] for r in manifest}
    doc = scope()

    old_keys: dict[str, set[str]] = defaultdict(set)
    for r in manifest:
        if r.get("family"):
            old_keys[r["slug"]].add(make_line_key(r["family"], r["name"]))
    for line in doc.get("lines") or []:
        slug = slug_by_file.get(line.get("report_file"))
        if slug and line.get("report_old_line"):
            old_keys[slug].add(line["report_old_line"])

    key_map: dict[str, list[dict]] = defaultdict(list)
    for row in axis().get("key_map") or []:
        key_map[row["todays_key"]].append(row)
    record_keys = {k for k in map(line_key_of, raw("FORMULA_COMBOS")) if k}

    joins: dict[str, dict[str, str]] = {r["slug"]: {} for r in manifest}
    for slug, keys in old_keys.items():
        for old in sorted(keys):
            if old in record_keys:
                joins[slug].setdefault(old, "key_map")
            for row in key_map.get(old, ()):
                if row.get("new_line") and row.get("new_family"):
                    joins[slug].setdefault(make_line_key(row["new_family"], row["new_line"]),
                                           "key_map")
    for line_key, file in (doc.get("report_map") or {}).items():
        slug = slug_by_file.get(file)
        if slug:
            joins[slug][line_key] = "report_map"
    return joins


def derive_line_report_slugs(joins: dict[str, dict[str, str]] | None = None
                             ) -> tuple[dict[str, str], dict[str, list[str]]]:
    """(line_key → slug, line_key → [slugs]): the report slug of every line one
    report reaches — a v1 line's own `report_map` slug, otherwise the one
    report that reaches it — and, separately, the lines several reach."""
    joins = derive_report_lines() if joins is None else joins
    by_line: dict[str, dict[str, str]] = defaultdict(dict)
    for slug, lines in joins.items():
        for key, source in lines.items():
            by_line[key][slug] = source
    single: dict[str, str] = {}
    several: dict[str, list[str]] = {}
    for key, slugs in by_line.items():
        mapped = [s for s, source in slugs.items() if source == "report_map"]
        if len(mapped) == 1:
            single[key] = mapped[0]
        elif len(slugs) == 1:
            single[key] = next(iter(slugs))
        else:
            several[key] = sorted(slugs)
    return single, several


# ── Loading ──────────────────────────────────────────────────────────────────

def _load_reports(db: Session, report: LoadReport) -> set[str]:
    """Reports, sections and panels. Returns the slugs that have a report row."""
    diff = _diff(report, "market_reports")
    sdiff = _diff(report, "market_report_sections")
    pdiff = _diff(report, "market_report_panels")
    check = _diff(report, STRUCTURE_CHECK)

    manifest = report_manifest()
    v1_files = set((scope().get("report_map") or {}).values())
    current = {r.slug: r for r in db.query(MarketReport)}
    parsed: dict[str, ParsedReport] = {}
    for entry in manifest:
        slug = entry["slug"]
        try:
            text, digest = read_report(entry["delivered"])
        except (DropNotAvailable, ValueError) as exc:
            diff.skipped.append((slug, f"not loaded — {exc}"))
            continue
        try:
            p = parse_report(entry, text, in_v1_scope=entry["delivered"] in v1_files,
                             source_sha256=digest)
        except rs.ReportRejected as exc:
            diff.skipped.append((slug, f"REJECTED, not loaded — {exc}"))
            continue
        parsed[slug] = p
        _upsert(db, diff, current, slug, p.fields, lambda f, s=slug: MarketReport(slug=s, **f))
        if p.notes:
            check.skipped += [(slug, note) for note in p.notes]
        else:
            check.unchanged += 1
    diff.stale += len(set(current) - {e["slug"] for e in manifest})
    db.flush()

    sections: dict[str, dict] = defaultdict(dict)
    for row in db.query(MarketReportSection):
        sections[row.slug][row.section_id] = row
    panels: dict[str, dict] = defaultdict(dict)
    for row in db.query(MarketReportPanel):
        panels[row.slug][row.panel] = row
    for slug, p in parsed.items():
        _sync_children(db, sdiff, sections[slug], p.sections,
                       lambda sid, f, s=slug: MarketReportSection(slug=s, section_id=sid, **f))
        _sync_children(db, pdiff, panels[slug], p.panels,
                       lambda panel, f, s=slug: MarketReportPanel(slug=s, panel=panel, **f))
    db.flush()
    return set(current)


def _near_misses(slug: str) -> str:
    """For a report that reaches no line: key_map rows with the same line name
    under another family — named as candidates, never joined."""
    entry = next(r for r in report_manifest() if r["slug"] == slug)
    same_name = sorted({row["todays_key"] for row in axis().get("key_map") or []
                        if row["todays_key"].partition(LINE_KEY_SEP)[2] == entry["name"]})
    return f"; key_map has {', '.join(same_name)} (family differs, not joined)" if same_name else ""


def _load_lines(db: Session, report: LoadReport, slugs: set[str],
                joins: dict[str, dict[str, str]]) -> None:
    diff = _diff(report, "market_report_lines")
    lines = line_index(db)
    existing: dict[str, dict] = defaultdict(dict)
    for row in db.query(MarketReportLine):
        existing[row.slug][row.line_key] = row

    for slug in sorted(joins):
        if slug not in slugs:
            continue                            # no report row (rejected on first load)
        desired = {}
        for key, source in sorted(joins[slug].items()):
            line_id = lines.get(key)
            if line_id is None:
                diff.skipped.append((f"{slug} → {key}", "no current product line has this "
                                     "key or former key (line not yet published, retired "
                                     "or not loaded); product_line_id NULL"))
            desired[key] = {"product_line_id": line_id, "source": source}
        if not desired:
            diff.skipped.append((slug, "the report reaches no current line through "
                                       "report_map or key_map" + _near_misses(slug)))
        _sync_children(db, diff, existing[slug], desired,
                       lambda key, f, s=slug: MarketReportLine(slug=s, line_key=key, **f))
    db.flush()

    check = _diff(report, LINES_CHECK)
    check.unchanged = (db.query(MarketReportLine.product_line_id)
                       .filter(MarketReportLine.product_line_id.isnot(None))
                       .distinct().count())


def _fill_line_slugs(db: Session, report: LoadReport,
                     joins: dict[str, dict[str, str]]) -> None:
    """`product_lines.report_slug` for lines outside v1 that one report reaches
    and that have none yet. v1 lines (report_map) belong to the taxonomy loader,
    which writes the same rule on every line; this is the fill for a taxonomy
    load that predates it, and a check (all unchanged) otherwise."""
    diff = _diff(report, REPORT_SLUG_ROW)
    single, several = derive_line_report_slugs(joins)
    v1 = set(scope().get("report_map") or {})
    rows = db.query(ProductLine).filter(ProductLine.line_key.in_(set(single) | set(several)))
    for row in rows:
        key = row.line_key
        if key in several:
            diff.skipped.append((key, f"{len(several[key])} reports reach this line "
                                      f"({', '.join(several[key])}); report_slug not set"))
        elif row.report_slug == single[key]:
            diff.unchanged += 1
        elif key in v1:
            diff.skipped.append((key, f"v1 line has report_slug {row.report_slug!r}, "
                                      f"report_map says {single[key]!r}; not overwritten"))
        elif row.report_slug is None:
            row.report_slug = single[key]
            diff.updated += 1
        else:
            diff.skipped.append((key, f"has report_slug {row.report_slug!r}; "
                                      f"not overwritten with {single[key]!r}"))
    db.flush()


def load(db: Session, report: LoadReport) -> LoadReport:
    """Load the reports and their joins. Flushes, never commits."""
    slugs = _load_reports(db, report)
    joins = derive_report_lines()
    _load_lines(db, report, slugs, joins)
    _fill_line_slugs(db, report, joins)
    return report
