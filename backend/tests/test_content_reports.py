"""The report loader (app/services/content_drop/reports.py) and its allowlist
sanitiser (app/services/content_drop/report_sanitize.py).

The sanitiser's allowlist is the corpus's own vocabulary, no more: every
delivered report loads with zero rejections, and anything outside the list
rejects the report. What is stored is canonical (sanitising it again changes
nothing) and loses no text. The coagulants report — the demo's opening screen —
joins to the lines it serves and keeps its Kraljic position. Joins resolve to
product lines by key, then by former key; a key that is not a current line
keeps its row with no line. A second load changes nothing.

Expected numbers come from the drop at test time (`tests/content_drop_expect.py`
and the drop files), never from constants: the drop changes with every pull.
The database must already hold the product lines (a taxonomy load): build it
with `scripts/ops/rebuild_env.py build ... --only taxonomy,...,reports` or a
full build. Assertions are on totals and stored state, never on `created`.
The module works in one transaction and rolls it back at the end.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date

import pytest

from app.database import SessionLocal, bypass_rls_var
from app.models.market_report import (
    MarketReport, MarketReportLine, MarketReportPanel, MarketReportSection,
)
from app.models.product_line import ProductLine
from app.services.drop.report import LoadReport
from app.services.content_drop import reader, reports
from app.services.content_drop import report_sanitize as rs
from tests import content_drop_expect as expect

pytestmark = pytest.mark.skipif(not reader.drop_available(),
                                reason="content drop not extracted (set CONTENT_DROP_DIR)")

# Parser findings on known quirks of individual reports (slug → note prefixes).
# A regenerated report that loses or gains one of these is worth a look.
EXPECTED_NOTES = {
    "synthetic_alcohols": ["porter: not one three-column table"],
    "organic_pigments": ["strategic: carries an outlook block"],
    "anionic_labs_chain": ["strategic: carries an outlook block"],
    "vitamin_d_chain": ["tech: 1 unclosed <strong>"],
}
REBUILD = ("the database holds no product lines: build it with "
           "`scripts/ops/rebuild_env.py build ... --only taxonomy,indexes,reports,playbooks` "
           "(or a full build), then copy it to the _test database")

_SECTION_RE = re.compile(r"<section\b[^>]*\bid=\"([\w-]+)\"[^>]*>(.*?)</section>", re.S)
_H3_RE = re.compile(r"<h3\b[^>]*>(.*?)</h3>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


# Line keys and report text are read from the drop at test time; licensed names
# and prose never live in this file.
def _entry(slug: str) -> dict:
    return next(r for r in reader.report_manifest() if r["slug"] == slug)


def _source(slug: str) -> tuple[str, str]:
    return reports.read_report(_entry(slug)["delivered"])


def _parse(slug: str, source: str | None = None) -> reports.ParsedReport:
    text, digest = _source(slug)
    return reports.parse_report(_entry(slug), source if source is not None else text,
                                in_v1_scope=True, source_sha256=digest)


def _has_outlook(slug: str) -> bool:
    """Read off the raw file, independently of the loader's parse: section 7
    has an h3 that says Outlook."""
    sections = _SECTION_RE.findall(_source(slug)[0])
    return any("outlook" in _TAG_RE.sub("", h).lower()
               for h in _H3_RE.findall(sections[-1][1]))


def _coagulants_lines() -> tuple[set[str], set[str]]:
    """(the coagulants report's own v1 lines, the lines its old keys map to)."""
    entry = _entry("coagulants")
    v1 = {k for k, f in reader.scope()["report_map"].items() if f == entry["delivered"]}
    joined = {k for s, k in expect.report_joins() if s == "coagulants"}
    return v1, joined - v1


def _v1_files() -> set[str]:
    delivered = {r["delivered"] for r in reader.report_manifest()}
    return set(reader.scope()["report_map"].values()) & delivered


def _expected_line_slugs() -> tuple[dict[str, str], dict[str, list[str]]]:
    """(line key → its one report, line key → the several reports reaching it),
    from the independently derived joins: a line with its own report_map entry
    takes that report; otherwise one report is one, more are several."""
    by_line: dict[str, set[str]] = defaultdict(set)
    for slug, key in expect.report_joins():
        by_line[key].add(slug)
    slug_by_file = {r["delivered"]: r["slug"] for r in reader.report_manifest()}
    mapped = {k: slug_by_file[f] for k, f in reader.scope()["report_map"].items()
              if f in slug_by_file}
    single, several = {}, {}
    for key, slugs in by_line.items():
        if key in mapped:
            single[key] = mapped[key]
        elif len(slugs) == 1:
            single[key] = next(iter(slugs))
        else:
            several[key] = sorted(slugs)
    return single, several


def _total(report: LoadReport, table: str) -> int:
    diff = report.table(table)
    return diff.created + diff.updated + diff.unchanged


@pytest.fixture(scope="module")
def parsed() -> dict[str, reports.ParsedReport]:
    return {e["slug"]: _parse(e["slug"]) for e in reader.report_manifest()}


@pytest.fixture(scope="module")
def loaded():
    """One session and one transaction for the module: load the reports twice
    over the product lines already in the database, yield, roll back."""
    token = bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        if db.query(ProductLine.id).first() is None:
            pytest.fail(REBUILD, pytrace=False)
        first = reports.load(db, LoadReport(title="test reports 1"))
        db.flush()
        second = reports.load(db, LoadReport(title="test reports 2"))
        db.flush()
        yield db, first, second
    finally:
        db.rollback()
        db.close()
        bypass_rls_var.reset(token)


# ── The sanitiser ────────────────────────────────────────────────────────────

def test_the_allowlist_is_the_measured_one():
    assert len(rs.ALLOWED_TAGS) == 25
    assert len(rs.ALL_ATTRIBUTES) == 21
    assert len(rs.ALLOWED_CLASSES) == 31
    assert len(rs.ALLOWED_CSS_PROPERTIES) == 15


def test_the_corpus_uses_every_allowed_token():
    """No slack: every tag, attribute, class and CSS property on the list is
    used somewhere inside the delivered reports' sections."""
    tags, attrs, classes, props = set(), set(), set(), set()
    for entry in reader.report_manifest():
        soup = rs.parse(reader.report_html(entry["delivered"]))
        for section in soup.find_all("section"):
            for el in section.find_all(True):
                tags.add(el.name)
                attrs.update(el.attrs)
                classes.update(rs.classes_of(el))
                props.update(p for p, _v in rs.css_declarations(el.get("style", "")))
    assert tags == rs.ALLOWED_TAGS
    assert attrs == rs.ALL_ATTRIBUTES
    assert classes == rs.ALLOWED_CLASSES
    assert props == rs.ALLOWED_CSS_PROPERTIES


def test_a_clean_fragment_is_normalised_and_stable():
    fragment = (
        '<p class="src">A &mdash; B&rsquo;s &amp; C&nbsp;D &lt;x&gt;</p>'
        '<svg viewBox="0 0 260 260" width="260" xmlns="http://www.w3.org/2000/svg" '
        'style="font-family:Inter,sans-serif"><!-- a note -->'
        '<rect x="30" y="10" width="110" height="110" fill="#dbeafe" rx="4"/>'
        '<text x="85" y="70" text-anchor="middle" font-size="10">Leverage</text></svg>'
    )
    out = rs.sanitize_html(fragment)
    assert out.startswith('<p class="src">A — B’s &amp; C\u00a0D &lt;x&gt;</p>')
    assert 'viewBox="0 0 260 260"' in out
    assert '<rect x="30" y="10" width="110" height="110" fill="#dbeafe" rx="4"/>' in out
    assert "note" not in out and "<!--" not in out
    assert rs.sanitize_html(out) == out


@pytest.mark.parametrize("fragment", [
    '<img src="x.png">',
    '<a href="https://example.com">x</a>',
    '<script>alert(1)</script>',
    '<style>p{}</style>',
    '<iframe></iframe>',
    '<section id="x">nested</section>',
    '<div onclick="alert(1)">x</div>',
    '<div class="insight evil">x</div>',
    '<p id="x">x</p>',
    '<th colspan="2">x</th>',
    '<td colspan="two">x</td>',
    '<p style="position:fixed">x</p>',
    '<p style="background:url(https://example.com/x.png)">x</p>',
    '<p style="color:expression(alert(1))">x</p>',
    '<p style="width:10%;color:#fff;background:javascript:x">x</p>',
    '<svg xmlns="http://example.com/ns"></svg>',
    '<svg><p>html inside svg</p></svg>',
    '<svg><foreignObject></foreignObject></svg>',
    '<svg><text x="1" y="1"><tspan>x</tspan></text></svg>',
    '<svg><svg></svg></svg>',
    '<rect x="1" y="1" width="1" height="1" fill="#fff" rx="1"/>',
    '<svg><circle cx="1" cy="1" r="1" fill="red" stroke="#fff" stroke-width="2"/></svg>',
    '<svg><text x="1" y="1" transform="translate(1 1)">x</text></svg>',
])
def test_anything_outside_the_allowlist_is_rejected(fragment):
    with pytest.raises(rs.ReportRejected) as exc:
        rs.sanitize_html(fragment)
    assert exc.value.violations


def test_an_unclosed_tag_is_measured_and_closed_at_its_parent():
    raw = "<p><strong>Bold lead. Rest of it.</p><p class=\"src\">Source.</p>"
    assert rs.unclosed_tags(raw) == {"strong": 1}
    assert rs.sanitize_html(raw) == (
        "<p><strong>Bold lead. Rest of it.</strong></p><p class=\"src\">Source.</p>")


# ── One report, without a database ───────────────────────────────────────────

def test_every_report_parses_and_none_is_rejected(parsed):
    counts = expect.report_counts()
    assert len(parsed) == counts["reports"]
    assert sum(len(p.sections) for p in parsed.values()) == counts["sections"]
    assert sum(len(p.panels) for p in parsed.values()) == counts["panels"]
    as_of = {p.fields["as_of"] for p in parsed.values()}
    assert None not in as_of
    for slug, p in parsed.items():
        assert tuple(p.sections) == reports.SECTION_IDS, slug
        want = set(reports.PANEL_ORDER) - (set() if _has_outlook(slug) else {"outlook"})
        assert set(p.panels) == want, slug
        assert p.fields["as_of"].day == 1
        assert p.fields["kraljic"]["quadrant"] in {"Leverage", "Strategic", "Bottleneck",
                                                   "Non-critical"}, slug
    noted = {slug: p.notes for slug, p in parsed.items() if p.notes}
    assert set(noted) == set(EXPECTED_NOTES)
    for slug, prefixes in EXPECTED_NOTES.items():
        assert len(noted[slug]) == len(prefixes), noted[slug]
        assert all(n.startswith(pre) for n, pre in zip(noted[slug], prefixes)), noted[slug]


def test_the_stored_html_loses_no_text_and_is_canonical(parsed):
    for entry in reader.report_manifest():
        soup = rs.parse(reader.report_html(entry["delivered"]))
        p = parsed[entry["slug"]]
        for section in soup.find_all("section"):
            html = p.sections[section["id"]]["html"]
            assert rs.text_of(rs.parse(html)) == rs.text_of(section), (entry["slug"],
                                                                       section["id"])
            assert rs.sanitize_html(html) == html
            assert "&mdash;" not in html and "&rsquo;" not in html
        for panel in p.panels.values():
            assert rs.sanitize_html(panel["html"]) == panel["html"]


def test_the_vitamin_d_section_is_stored_balanced(parsed):
    html = parsed["vitamin_d_chain"].sections["tech"]["html"]
    assert html.count("<strong>") == html.count("</strong>")
    # The source's stray <strong> ends with its paragraph, not the section.
    assert '<p class="src">CostAdvisor product line extraction' in html


def test_the_coagulants_report_cut(parsed):
    p = parsed["coagulants"]
    entry = _entry("coagulants")
    source, _ = _source("coagulants")
    assert p.fields["name"] == p.fields["old_line_name"] == entry["name"]
    assert p.fields["old_line_key"] == reader.make_line_key(entry["family"], entry["name"])
    # The header names the month the report is dated.
    as_of: date = p.fields["as_of"]
    assert f"{as_of:%B} {as_of.year}" in rs.text_of(rs.parse(source).find("header"))
    # The dot read off the chart is where the playbook (authored separately)
    # puts it.
    playbook = reader.read_json(reader.drop_dir() / "playbooks"
                                / "playbook_coagulants_appdata.json")["category"]["kraljic"]
    kraljic = p.fields["kraljic"]
    assert (kraljic["cx"], kraljic["cy"]) == (playbook["cx"], playbook["cy"])
    assert kraljic["quadrant"] == playbook["label"]
    assert kraljic["lead"] and kraljic["lead"] in source
    assert kraljic["note"].startswith(f"Kraljic: {playbook['label']}")
    headings = [s["heading"] for s in p.sections.values()]
    assert [h.split(".", 1)[0] for h in headings] == [str(i) for i in range(1, 8)]
    assert all(h in source for h in headings)
    assert [s["ordinal"] for s in p.sections.values()] == list(range(1, 8))
    assert all(s["html"].startswith("<h2>") for s in p.sections.values())
    raw_sections = dict(_SECTION_RE.findall(source))
    for sid, section in p.sections.items():
        assert section["src_block_count"] == raw_sections[sid].count('class="src"'), sid
    assert p.fields["word_count"] == sum(s["word_count"] for s in p.sections.values())

    panels = p.panels
    assert [panels[k]["ordinal"] for k in reports.PANEL_ORDER[:10]] == list(range(1, 11))
    assert not any(v["html"].lstrip().startswith(("<h2", "<h3")) for v in panels.values())
    assert panels["pestel"]["heading"].startswith("PESTEL")
    assert panels["pestel"]["heading"] in source
    pestel = panels["pestel"]["data"]["rows"]
    assert [r["factor"] for r in pestel] == [
        "Political", "Economic", "Social", "Technological", "Environmental", "Legal"]
    porter = panels["porter"]["data"]["rows"]
    assert len(porter) == 5 and all(r["force"] in source for r in porter)
    assert {"Supplier power", "Buyer power"} <= {r["force"] for r in porter}
    assert porter[0]["question"] and porter[0]["question"] in source
    text = rs.text_of(rs.parse(source))
    assert all(r["assessment"] and r["assessment"] in text for r in porter)
    drivers = panels["market_drivers"]["data"]["cards"]
    authored = [rs.text_of(card.find("h4"))
                for card in rs.parse(raw_sections["strategic"]).find_all(class_="driver-card")]
    assert len(drivers) == len(authored) > 0
    for card, heading in zip(drivers, authored):
        arrow = heading[0] if heading[0] in reports.DRIVER_ARROWS else None
        assert card["arrow"] == arrow
        assert card["title"] == (heading[1:].strip() if arrow else heading)
    narrative = panels["kraljic"]["data"]["narrative"]
    assert panels["kraljic"]["data"]["quadrant"] == playbook["label"]
    assert narrative and all(n and n in text for n in narrative)
    # Section 7's one attribution travels with its last block.
    assert panels["kraljic"]["html"].rstrip().endswith("</p>")
    assert 'class="src"' in panels["kraljic"]["html"]


def test_the_kraljic_quadrant_agrees_with_every_playbook(parsed):
    """Every playbook that has a delivered report carries a Kraljic badge, and
    it names the quadrant the report's chart puts the dot in."""
    checked = 0
    slugs = set()
    for path in reader.playbook_files():
        category = reader.read_json(path)["category"]
        slugs.add(category["id"])
        if category["id"] not in parsed:
            continue
        kraljic = category.get("kraljic")
        assert kraljic, category["id"]
        assert kraljic["badge"] == parsed[category["id"]].fields["kraljic"]["quadrant"].lower()
        checked += 1
    assert checked == len(slugs & set(parsed)) > 0


def test_a_synthetic_report_with_one_extra_tag_is_rejected():
    text, _ = _source("coagulants")
    tampered = text.replace('<section id="overview">', '<section id="overview"><iframe></iframe>', 1)
    with pytest.raises(rs.ReportRejected, match="overview: tag <iframe>"):
        _parse("coagulants", tampered)


def test_a_report_missing_a_canonical_block_is_rejected():
    text, _ = _source("coagulants")
    tampered = text.replace("<h3>Kraljic positioning</h3>", "<h4>Kraljic positioning</h4>")
    with pytest.raises(rs.ReportRejected, match="lacks its kraljic block"):
        _parse("coagulants", tampered)


# ── The line join, without a database ────────────────────────────────────────

def test_the_join_rows_match_the_independent_derivation():
    joins = reports.derive_report_lines()
    flat = {(slug, key) for slug, lines in joins.items() for key in lines}
    assert flat == expect.report_joins()
    assert len(flat) == expect.report_counts()["joins"]
    # report_map wins on its own keys: every one of them, and nothing else.
    report_map = reader.scope()["report_map"]
    sources = Counter(src for lines in joins.values() for src in lines.values())
    assert sources["report_map"] == len(report_map)
    assert sources["key_map"] == len(flat) - len(report_map)
    assert len({s for s, lines in joins.items() if "report_map" in lines.values()}) == \
        len(_v1_files())
    # The merged coagulants report: its v1 lines, plus the other halves of the
    # old keys it merged.
    v1, mapped = _coagulants_lines()
    assert v1 and mapped
    assert joins["coagulants"] == {**{k: "key_map" for k in mapped},
                                   **{k: "report_map" for k in v1}}
    # An old key that is still a record's line key joins to itself.
    assert joins["musk_compounds"] == {reader.line_key_of("F27-GAL-SOL"): "key_map"}
    # Reports that reach no line at all.
    reached = {s for s, _k in expect.report_joins()}
    assert {s for s, lines in joins.items() if not lines} == \
        {r["slug"] for r in reader.report_manifest()} - reached


def test_every_report_map_key_is_a_current_line():
    assert set(reader.scope()["report_map"]) <= set(expect.product_lines())


def test_the_line_report_slugs():
    single, several = reports.derive_line_report_slugs()
    want_single, want_several = _expected_line_slugs()
    assert single == want_single
    assert several == want_several
    v1 = reader.scope()["report_map"]
    slug_by_file = {r["delivered"]: r["slug"] for r in reader.report_manifest()}
    assert all(single[key] == slug_by_file[file] for key, file in v1.items())
    _, mapped = _coagulants_lines()
    assert mapped <= set(several)


# ── The load ─────────────────────────────────────────────────────────────────

def test_load_reaches_the_drop_counts(loaded):
    db, first, _ = loaded
    counts = expect.report_counts()
    assert _total(first, "market_reports") == counts["reports"]
    assert _total(first, "market_report_sections") == counts["sections"]
    assert _total(first, "market_report_panels") == counts["panels"]
    assert _total(first, "market_report_lines") == counts["joins"]
    assert not [s for s in first.table("market_reports").skipped if "REJECTED" in s[1]]
    assert db.query(MarketReport).count() == counts["reports"]
    assert db.query(MarketReportSection).count() == counts["sections"]
    assert db.query(MarketReportPanel).count() == counts["panels"]
    assert db.query(MarketReportLine).count() == counts["joins"]
    assert db.query(MarketReport).filter(MarketReport.in_v1_scope.is_(True)).count() == \
        len(_v1_files())
    # Joins that reach no current line keep their row with no line: exactly
    # the keys the drop's axis does not publish.
    unresolved = {(r.slug, r.line_key) for r in db.query(MarketReportLine)
                  .filter(MarketReportLine.product_line_id.is_(None))}
    assert unresolved == {(s, k) for s, k in expect.report_joins()
                          if expect.resolve_line_key(k) is None}
    assert len(unresolved) == counts["joins_not_current_line"]
    resolved_lines = {expect.resolve_line_key(k) for _s, k in expect.report_joins()} - {None}
    assert first.table(reports.LINES_CHECK).unchanged == len(resolved_lines)


def test_every_resolved_join_points_at_its_line(loaded):
    db, _, _ = loaded
    key_of = dict(db.query(ProductLine.id, ProductLine.line_key))
    for row in db.query(MarketReportLine).filter(MarketReportLine.product_line_id.isnot(None)):
        assert key_of[row.product_line_id] == expect.resolve_line_key(row.line_key), row.line_key
    # Every report_map join resolves directly: its key is the line's own key.
    for row in db.query(MarketReportLine).filter_by(source="report_map"):
        assert key_of[row.product_line_id] == row.line_key


def test_a_second_load_changes_nothing(loaded):
    _, _, second = loaded
    assert second.changed == 0, second.render()


def test_coagulants_serves_its_lines(loaded):
    db, _, _ = loaded
    v1, mapped = _coagulants_lines()
    ferric = reader.line_key_of("BCI-FECL3-LIQ")
    assert ferric in v1
    rows = {r.line_key: r for r in db.query(MarketReportLine).filter_by(slug="coagulants")}
    assert set(rows) == v1 | mapped
    lines = {pl.line_key: pl for pl in
             db.query(ProductLine).filter(ProductLine.line_key.in_(list(rows)))}
    for key, row in rows.items():
        assert row.product_line_id == lines[key].id
    assert "BCI-FECL3-LIQ" in reader.pids_on_line(ferric)
    assert all(lines[k].report_slug == "coagulants" for k in v1)
    # Several reports reach the mapped lines: left for the join table, not guessed.
    assert all(lines[k].report_slug is None for k in mapped)

    report = db.get(MarketReport, "coagulants")
    assert report.kraljic["quadrant"] and report.as_of is not None
    assert [s.section_id for s in report.sections] == list(reports.SECTION_IDS)
    assert {p.panel for p in report.panels} >= {"pestel", "porter", "market_drivers", "kraljic"}


def test_report_slugs_fill_lines_outside_v1_and_leave_v1_alone(loaded):
    db, first, _ = loaded
    single, several = reports.derive_line_report_slugs()
    stored = dict(db.query(ProductLine.line_key, ProductLine.report_slug))
    assert all(stored[k] == v for k, v in single.items() if k in stored)
    assert all(stored[k] is None for k in several if k in stored)
    diff = first.table(reports.REPORT_SLUG_ROW)
    assert len(diff.skipped) == len([k for k in several if k in stored])


def test_a_line_that_has_a_slug_keeps_it(loaded):
    db, _, _ = loaded
    single, _ = reports.derive_line_report_slugs()
    v1 = set(reader.scope()["report_map"])
    stored = {k for (k,) in db.query(ProductLine.line_key)}
    key = sorted(k for k in single if k not in v1 and k in stored)[0]
    line = db.query(ProductLine).filter(ProductLine.line_key == key).one()
    line.report_slug = "an_editors_choice"
    db.flush()
    try:
        again = reports.load(db, LoadReport(title="own slug"))
        db.flush()
        assert line.report_slug == "an_editors_choice"
        assert again.changed == 0
        assert any(k == key and "not overwritten" in why
                   for k, why in again.table(reports.REPORT_SLUG_ROW).skipped)
    finally:
        line.report_slug = single[key]
        db.flush()


def test_a_renamed_line_keeps_its_joins_through_its_former_key(loaded):
    """A line renamed since the join was written (its old key now in
    `former_keys`) still receives the join: same row, same line, no change."""
    db, _, _ = loaded
    ferric = reader.line_key_of("BCI-FECL3-LIQ")
    line = db.query(ProductLine).filter(ProductLine.line_key == ferric).one()
    before = {(r.slug, r.line_key): r.product_line_id
              for r in db.query(MarketReportLine).filter_by(product_line_id=line.id)}
    assert before
    saved = (line.line_key, line.name, line.former_keys)
    savepoint = db.begin_nested()
    try:
        family = ferric.split(reader.LINE_KEY_SEP)[0]
        line.line_key = reader.make_line_key(family, "A test rename")
        line.name = "A test rename"
        line.former_keys = [*(line.former_keys or []), ferric]
        db.flush()
        again = reports.load(db, LoadReport(title="renamed line"))
        db.flush()
        assert again.changed == 0, again.render()
        after = {(r.slug, r.line_key): r.product_line_id
                 for r in db.query(MarketReportLine).filter_by(product_line_id=line.id)}
        assert after == before
    finally:
        savepoint.rollback()
        db.expire_all()
    line = db.query(ProductLine).filter(ProductLine.line_key == ferric).one()
    assert (line.line_key, line.name, line.former_keys) == saved


def test_a_rejected_report_is_reported_and_left_as_it_was(loaded, monkeypatch):
    db, _, _ = loaded
    before = {s.section_id: s.html for s in db.get(MarketReport, "coagulants").sections}
    real = reports.read_report

    def tampered(file):
        text, digest = real(file)
        if file == _entry("coagulants")["delivered"]:
            text = text.replace("<h2>1. Overview</h2>", '<h2 onclick="alert(1)">1. Overview</h2>')
        return text, digest

    monkeypatch.setattr(reports, "read_report", tampered)
    again = reports.load(db, LoadReport(title="tampered"))
    db.flush()
    rejected = [why for slug, why in again.table("market_reports").skipped if slug == "coagulants"]
    assert rejected and rejected[0].startswith("REJECTED")
    assert "attribute 'onclick' on <h2>" in rejected[0]
    assert again.changed == 0
    db.expire_all()
    assert {s.section_id: s.html for s in db.get(MarketReport, "coagulants").sections} == before


def test_a_reload_repairs_damaged_rows(loaded):
    db, _, _ = loaded
    report = db.get(MarketReport, "coagulants")
    quadrant = report.kraljic["quadrant"]
    report.kraljic = {"cx": 0, "cy": 0}
    section = db.query(MarketReportSection).filter_by(slug="coagulants",
                                                      section_id="overview").one()
    section.html = "<p>edited</p>"
    db.delete(db.query(MarketReportPanel).filter_by(slug="coagulants", panel="porter").one())
    db.add(MarketReportLine(slug="coagulants", line_key="Nowhere|||Stray", source="key_map"))
    joined = db.query(MarketReportLine).filter_by(slug="coagulants", source="report_map").first()
    joined.product_line_id = None
    db.flush()

    repaired = reports.load(db, LoadReport(title="repair"))
    db.flush()
    assert repaired.table("market_reports").updated == 1
    assert repaired.table("market_report_sections").updated == 1
    assert repaired.table("market_report_panels").created == 1
    lines = repaired.table("market_report_lines")
    assert (lines.deleted, lines.updated) == (1, 1)
    db.expire_all()
    assert db.get(MarketReport, "coagulants").kraljic["quadrant"] == quadrant
    assert db.query(MarketReportLine).filter_by(
        slug="coagulants", source="report_map", product_line_id=None).count() == 0
    again = reports.load(db, LoadReport(title="after repair"))
    db.flush()
    assert again.changed == 0, again.render()
