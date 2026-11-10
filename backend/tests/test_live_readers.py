"""Live numbers in every reader (Jira 210 S4), and the ticket's acceptance.

* report page (`GET /api/intel/reports/{slug}`):
    1. a live number shows the current value and its month;
    2. loading a new index point shows the new value, with no edit;
    3. removing the series shows the fallback with "as of";
    and a section without tokens is served exactly as stored;
* Strategy tab panels (`strategy.analysis`): html and `data` text filled;
* blocks (`intel_catalogue._blocks`): served form filled, editor API raw;
* levers: platform text filled, a team's own notes left as typed.

Reports, blocks and levers here are made up (no drop text); series come from
the resolver tests' fixtures.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.editorial import EditorialBlock, EditorialBlockVersion
from app.models.index_layer import IndexMonthlyValue
from app.models.market_report import MarketReport, MarketReportPanel, MarketReportSection
from app.models.strategy import LeverScore, Playbook, PlaybookLever
from app.services import intel_reference
from app.services.intel_catalogue import _blocks
from app.services.live_values import LiveText
from app.services.strategy import _authored_row, analysis
from tests.test_live_resolver import _series, made  # noqa: F401 — fixture

REPORT_AS_OF = date(2026, 8, 1)


@pytest.fixture
def report(db, made):
    """A made-up report: one section with live numbers, one without, one panel."""
    s = _series(db, made, [(2025, 6, 100, "actual"), (2026, 6, 129.9, "actual")])
    slug = f"test-live-{uuid.uuid4().hex[:8]}"
    key = s.commodity_key
    db.add(MarketReport(slug=slug, report_file=f"{slug}.html", name="Live test report",
                        as_of=REPORT_AS_OF))
    db.flush()
    db.add(MarketReportSection(
        slug=slug, section_id="summary", ordinal=1, heading="Summary",
        html=(f"<h2>Summary</h2><p>Crude stood at <strong>{{{{index:{key}|last|fallback=about 130}}}}"
              f"</strong>, {{{{index:{key}|change12m}}}} on the year.</p>")))
    db.add(MarketReportSection(
        slug=slug, section_id="market", ordinal=2, heading="Market",
        html="<h2>Market</h2><p>Capacity 400 kt, <em>unchanged</em>.</p>"))
    db.add(MarketReportPanel(
        slug=slug, panel="market_drivers", ordinal=9, heading="Market drivers",
        html=f"<p>Energy {{{{index:{key}|last}}}}</p>",
        data={"cards": [{"title": "Crude", "arrow": "↑", "text": f"up {{{{index:{key}|change12m}}}}"}]}))
    db.commit()
    intel_reference.reset_cache()
    yield {"slug": slug, "series": s}
    db.rollback()
    bypass_rls_var.set(True)
    db.execute(text("DELETE FROM market_reports WHERE slug = :s"), {"s": slug})
    db.commit()
    intel_reference.reset_cache()


def _get(client, slug):
    r = client.get(f"/api/intel/reports/{slug}")
    assert r.status_code == 200, r.text
    return r.json()


# ── Report page: the acceptance criteria ─────────────────────────────────────

def test_acceptance_1_a_live_number_shows_the_value_and_its_month(report, tenant_a, client_as):
    body = _get(client_as(tenant_a), report["slug"])
    summary = body["sections"][0]["html"]
    assert summary == ("<h2>Summary</h2><p>Crude stood at <strong>"
                       '<span class="live-value">129.9 · Jun 2026</span></strong>, '
                       '<span class="live-value">+29.9% · Jun 2026</span> on the year.</p>')
    assert "{{" not in str(body)


def test_acceptance_2_a_new_point_shows_with_no_edit(db, report, tenant_a, client_as):
    client = client_as(tenant_a)
    assert "129.9 · Jun 2026" in _get(client, report["slug"])["sections"][0]["html"]
    db.add(IndexMonthlyValue(commodity_id=report["series"].id, year=2026, month=7,
                             value=133.5, kind="actual"))
    db.commit()
    assert "133.5 · Jul 2026" in _get(client, report["slug"])["sections"][0]["html"]


def test_acceptance_3_a_removed_series_shows_the_fallback_with_as_of(db, made, report, tenant_a,
                                                                     client_as):
    sid = report["series"].id
    made["series"].remove(report["series"])     # deleted here, not by the fixture
    db.execute(text("DELETE FROM index_monthly_values WHERE commodity_id = :i"), {"i": sid})
    db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": sid})
    db.commit()
    summary = _get(client_as(tenant_a), report["slug"])["sections"][0]["html"]
    assert "about 130 (as of Aug 2026)" in summary
    assert '<span class="live-value live-missing">n/a (no current data)</span> on the year' in summary
    assert "{{" not in summary


def test_a_section_without_tokens_is_served_exactly_as_stored(report, tenant_a, client_as):
    market = _get(client_as(tenant_a), report["slug"])["sections"][1]["html"]
    assert market == "<h2>Market</h2><p>Capacity 400 kt, <em>unchanged</em>.</p>"


def test_panel_html_and_data_are_filled_on_the_report_page(report, tenant_a, client_as):
    panel = _get(client_as(tenant_a), report["slug"])["panels"][0]
    assert panel["html"] == '<p>Energy <span class="live-value">129.9 · Jun 2026</span></p>'
    assert panel["data"]["cards"][0]["text"] == "up +29.9% · Jun 2026"


# ── Strategy tab ─────────────────────────────────────────────────────────────

def test_the_strategy_tab_serves_the_same_filled_panels(db, report):
    playbook = Playbook(slug=report["slug"], name="Live test", report_slug=report["slug"])
    out = analysis(db, playbook)
    drivers = out["panels"]["market_drivers"]
    assert drivers["html"] == '<p>Energy <span class="live-value">129.9 · Jun 2026</span></p>'
    assert drivers["cards"][0]["text"] == "up +29.9% · Jun 2026"


# ── Blocks ───────────────────────────────────────────────────────────────────

def test_a_served_block_is_filled_and_the_editor_keeps_the_token(db, made):
    s = _series(db, made, [(2026, 6, 41.25, "actual")])
    code = f"TST-{uuid.uuid4().hex[:6]}"
    raw = f"Gas is at {{{{index:{s.commodity_key}|last}}}} this month."
    block = EditorialBlock(subject_type="formula", subject_code=code, block_type="current_events",
                           body_format="text", provenance="imported")
    db.add(block)
    db.flush()
    version = EditorialBlockVersion(block_id=block.id, version_no=1, body_format="text",
                                    body_text=raw, provenance="imported")
    db.add(version)
    db.flush()
    block.current_version_id = version.id
    db.commit()
    try:
        served = _blocks(db, "formula", [code], ("current_events",))[code]["current_events"]
        assert served["body"] == "Gas is at 41.25 · Jun 2026 this month."
        db.refresh(version)
        assert version.body_text == raw              # stored (and edited) with the token
    finally:
        db.rollback()
        bypass_rls_var.set(True)
        db.execute(text("UPDATE editorial_blocks SET current_version_id = NULL WHERE id = :i"),
                   {"i": str(block.id)})
        db.execute(text("DELETE FROM editorial_block_versions WHERE block_id = :i"), {"i": str(block.id)})
        db.execute(text("DELETE FROM editorial_blocks WHERE id = :i"), {"i": str(block.id)})
        db.commit()


# ── Levers ───────────────────────────────────────────────────────────────────

def test_platform_lever_text_is_filled_and_a_teams_own_notes_are_not(db, made):
    s = _series(db, made, [(2025, 6, 100, "actual"), (2026, 6, 125, "actual")])
    key = s.commodity_key
    lever = PlaybookLever(playbook_slug="x", lever_code="L1", gemstone_code="ruby",
                          title=f"Index-link while gas is {{{{index:{key}|change12m}}}}",
                          guidance=f"Last seen {{{{index:{key}|asof}}}}.",
                          default_notes=f"Base {{{{index:{key}|last}}}}", sort_order=1)
    live = LiveText(db)
    live.prepare((lever.title, lever.guidance, lever.default_notes))

    row = _authored_row(lever, None, [], 0, live)
    assert row["title"] == "Index-link while gas is +25.0% · Jun 2026"
    assert row["guidance"] == "Last seen Jun 2026."
    assert row["notes"] == row["defaults"]["notes"] == "Base 125.0 · Jun 2026"

    team_notes = f"our own note keeps {{{{index:{key}|last}}}} as typed"
    row = _authored_row(lever, LeverScore(notes=team_notes), [], 0, live)
    assert row["notes"] == team_notes
