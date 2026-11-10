"""Converting typed report numbers into live tokens (LIVE-4 S4 + S5 checks).

* a bar converts only where its label is found once and still shows the
  typed weight; a date or move only in the exact text it was found in, where
  the number appears once — anything else is skipped with the reason;
* applying twice changes nothing more; reverting gives the original back;
* propose: a typed number equal to today's is approved at once, a drifted
  one waits for a person;
* apply to the stored report converts only approved rows, records
  `derived_from`, and the page then shows today's range;
* the loader path converts a freshly parsed report the same way.

Made-up report text; the recipe world comes from the live-bar tests.
"""
from __future__ import annotations

from app.models.live_conversion import ReportLiveConversion
from app.models.market_report import MarketReportSection
from app.services.content_drop.report_sanitize import sanitize_html
from app.services.live_conversions import (
    APPROVED, PROPOSED, apply_parsed, apply_stored, apply_to_html, propose, revert_html, verify,
)
from app.services.live_values import LiveText
from tests.test_live_bars import BAR_HTML, ONE, TWO, world  # noqa: F401 — fixture


def _row(id_, kind, anchor, typed, token):
    r = ReportLiveConversion(slug="rep", section_id="process", kind=kind, anchor=anchor,
                             typed=typed, token=token, status=APPROVED)
    r.id = id_
    return r


HTML = sanitize_html(
    BAR_HTML.format(label="Iron (FE-MB)", fills=TWO, token="30–49%")
    + "<p>Data as of <strong>June 2026</strong> for gas. Prices rose 12% in the year, 12% again.</p>")
BAR = _row(1, "cost_bar", "Iron (FE-MB)", "30–49%", "{{bar:rep|code=FE-MB|fallback=30–49%}}")
DATE = _row(2, "date", "June 2026", "June 2026", "{{index:ttf-eu|asof|fallback=June 2026}}")


# ── Where a conversion applies ───────────────────────────────────────────────

def test_a_bar_and_a_date_convert_where_found_and_revert_exactly():
    out, applied, skipped = apply_to_html(HTML, [BAR, DATE])
    assert {r.id for r in applied} == {1, 2} and skipped == []
    assert '<span class="weight">{{bar:rep|code=FE-MB|fallback=30–49%}}</span>' in out
    assert "<strong>{{index:ttf-eu|asof|fallback=June 2026}}</strong>" in out
    assert revert_html(out, [BAR, DATE]) == HTML
    again, applied2, _ = apply_to_html(out, [BAR, DATE])     # idempotent
    assert again == out and {r.id for r in applied2} == {1, 2}


def test_changed_or_unclear_text_is_skipped_never_forced():
    moved = _row(3, "cost_bar", "Iron (FE-MB)", "31–49%", "{{bar:rep|code=FE-MB}}")
    gone = _row(4, "cost_bar", "Nickel", "5%", "{{bar:rep|line=Nickel}}")
    twice = _row(5, "pct_move", "Prices rose 12% in the year, 12% again.", "12%",
                 "{{index:ttf-eu|change12m}}")
    out, applied, skipped = apply_to_html(HTML, [moved, gone, twice])
    assert out == HTML and applied == []
    why = {r.id: w for r, w in skipped}
    assert "now shows '30–49%'" in why[3]
    assert why[4] == "bar not found"
    assert why[5] == "the number appears 2 times in its text"


def test_a_label_on_several_bars_is_skipped():
    html = sanitize_html(BAR_HTML.format(label="Packaging", fills=ONE, token="4%") * 2)
    _out, applied, skipped = apply_to_html(
        html, [_row(6, "cost_bar", "Packaging", "4%", "{{bar:rep|line=Packaging}}")])
    assert applied == [] and skipped[0][1] == "label appears on several bars"


# ── Propose and apply on a report ────────────────────────────────────────────

def _process_section(db, world):
    """Iron typed as today's 26–49% (approved at once); the margin typed
    10–14% while the recipes say 9–14% (drifted, waits for a person)."""
    html = sanitize_html(
        BAR_HTML.format(label=f"Iron ({world['code']})", fills=TWO, token="26–49%")
        + BAR_HTML.format(label="Supplier margin", fills=TWO, token="10–14%"))
    db.add(MarketReportSection(slug=world["slug"], section_id="process", ordinal=5, html=html))
    db.flush()
    return html


def test_propose_approves_what_still_matches_and_holds_back_what_drifted(db, world):
    _process_section(db, world)
    report = propose(db)
    rows = {r.anchor: r for r in db.query(ReportLiveConversion).filter_by(slug=world["slug"])}
    iron, margin = rows[f"Iron ({world['code']})"], rows["Supplier margin"]
    assert (iron.status, iron.typed, iron.today) == (APPROVED, "26–49%", "26–49%")
    assert iron.token == f"{{{{bar:{world['slug']}|code={world['code']}|fallback=26–49%}}}}"
    assert (margin.status, margin.today) == (PROPOSED, "9–14%")
    assert margin.token.startswith(f"{{{{bar:{world['slug']}|line=Supplier margin|")
    assert report.created["cost_bar"] == 2 and report.approved_now == 1
    assert propose(db).created == {}                               # nothing twice


def test_apply_converts_only_approved_rows_and_the_page_shows_today(db, world):
    original = _process_section(db, world)
    propose(db)
    result = apply_stored(db)
    section = db.query(MarketReportSection).filter_by(slug=world["slug"]).one()
    assert result.applied == {"cost_bar": 1} and result.not_reversible == []
    assert "{{bar:" in section.html and "10–14%" in section.html      # margin not yet approved
    assert section.derived_from["bars"] == [{"report": world["slug"], "code": world["code"], "line": None}]
    shown = LiveText(db).html(section.html)
    assert '<span class="weight">26–49%</span>' in shown and "{{" not in shown

    db.query(ReportLiveConversion).filter_by(slug=world["slug"], anchor="Supplier margin").update(
        {"status": APPROVED})
    apply_stored(db)
    shown = LiveText(db).html(db.query(MarketReportSection).filter_by(slug=world["slug"]).one().html)
    assert '<span class="weight">9–14%</span>' in shown          # today's range, not the typed one
    assert 'class="fill lo" style="width:9%"' in shown
    assert original != shown


def test_the_loader_converts_a_freshly_parsed_report(db, world):
    html = _process_section(db, world)
    propose(db)
    sections = {"process": {"html": html, "derived_from": None}}
    notes = apply_parsed(db, world["slug"], sections, {})
    assert notes == []
    assert "{{bar:" in sections["process"]["html"]
    assert sections["process"]["derived_from"]["bars"]


def test_verify_finds_no_leftover_token_and_clean_html(db, world):
    _process_section(db, world)
    propose(db)
    apply_stored(db)
    report = verify(db)
    assert report.sections >= 1 and report.tokens >= 1
    assert report.leftovers == [] and report.unsafe == [] and report.missing == []
