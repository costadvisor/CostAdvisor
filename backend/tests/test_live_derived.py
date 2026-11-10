"""Which data each text's live numbers read: `derived_from` (Jira 210 S5).

* the scanners find tokens in HTML (text node by text node), plain text and
  JSON, and report broken ones — a token split by markup included;
* the record lists the series, the recipe lines and the tokens; no token, NULL;
* the lever loader records it and reports a broken token without dropping
  the lever;
* every block version records it (editor save and content load share
  `add_version`);
* the backfill fills existing rows, changes nothing on a second run, and
  lists broken tokens.

Text is made up.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.market_report import MarketReport, MarketReportPanel, MarketReportSection
from app.models.strategy import Playbook, PlaybookLever
from app.services.content_drop.playbooks import lever_fields
from app.services.drop.report import TableDiff
from app.services.editorial import create_block
from app.services.live_derived import recompute
from app.services.live_values import derived_from, scan_html, scan_text


# ── Scanning (no database) ───────────────────────────────────────────────────

def test_html_is_scanned_node_by_node_and_a_split_token_is_broken():
    tokens, problems = scan_html(
        "<p>Oil {{index:brent|last}} and <strong>{{index:ttf-eu|change12m}}</strong>.</p>"
        "<p>Split {{index:<em>brent</em>|last}}</p>")
    assert [t.raw for t in tokens] == ["{{index:brent|last}}", "{{index:ttf-eu|change12m}}"]
    assert len(problems) == 1 and "split by markup" in problems[0]


def test_text_and_json_are_scanned_and_unreadable_tokens_reported():
    tokens, problems = scan_text({"cards": [{"text": "up {{index:brent|change12m}}"}],
                                  "n": 3}, "bad {{index:Brent|last}}")
    assert [t.key for t in tokens] == ["brent"]
    assert problems and "bad series key" in problems[0]


def test_the_record_lists_series_recipe_lines_and_tokens():
    tokens, _ = scan_text("{{index:ttf-eu|last}} {{index:brent|asof}} {{index:brent|last}} "
                          "{{recipe:ABR-X|Europe|Grain|weight}}")
    assert derived_from(tokens) == {
        "series": ["brent", "ttf-eu"],
        "recipe_lines": [{"code": "ABR-X", "region": "Europe", "line": "Grain"}],
        "tokens": sorted(["{{index:ttf-eu|last}}", "{{index:brent|asof}}", "{{index:brent|last}}",
                          "{{recipe:ABR-X|Europe|Grain|weight}}"]),
    }
    assert derived_from([]) is None


# ── Writers ──────────────────────────────────────────────────────────────────

def test_the_lever_loader_records_tokens_and_reports_a_broken_one():
    diff = TableDiff(table="playbook_levers")
    f = lever_fields("cat", 1, {"id": "L-1", "gemstone": "Volume Concentration",
                                "title": "Bundle while gas is {{index:ttf-eu|change12m}}",
                                "notes": "broken {{index:ttf-eu|last"}, diff)
    assert f["derived_from"]["series"] == ["ttf-eu"]
    assert any("live token" in why for _, why in diff.skipped)


def test_a_block_version_records_what_it_reads(db):
    bypass_rls_var.set(True)
    code = f"TST-{uuid.uuid4().hex[:6]}"
    block = create_block(db, team_id=None, subject_type="formula", subject_code=code,
                         block_type="current_events", region=None,
                         body_text="Gas at {{index:ttf-eu|last}}.", body_json=None,
                         body_format="text", provenance="imported", author=None)
    try:
        assert block.derived_from == {"series": ["ttf-eu"], "recipe_lines": [],
                                      "tokens": ["{{index:ttf-eu|last}}"]}
    finally:
        db.rollback()


# ── Backfill ─────────────────────────────────────────────────────────────────

@pytest.fixture
def texts(db):
    bypass_rls_var.set(True)
    slug = f"test-live-{uuid.uuid4().hex[:8]}"
    db.add(MarketReport(slug=slug, report_file=f"{slug}.html", name="Derived test"))
    db.add(Playbook(slug=slug, name="Derived test"))
    db.flush()
    db.add(MarketReportSection(slug=slug, section_id="summary", ordinal=1,
                               html="<p>Oil {{index:brent|last}}, broken {{index:x</p>"))
    db.add(MarketReportSection(slug=slug, section_id="market", ordinal=2, html="<p>No numbers.</p>"))
    db.add(MarketReportPanel(slug=slug, panel="market_drivers", ordinal=9, html="<p>-</p>",
                             data={"cards": [{"text": "{{index:ttf-eu|change12m}}"}]}))
    db.add(PlaybookLever(playbook_slug=slug, lever_code=f"L-{slug}", gemstone_code="VC",
                         title="Lever {{index:brent|asof}}", sort_order=1))
    db.commit()
    yield slug
    db.rollback()
    bypass_rls_var.set(True)
    db.execute(text("DELETE FROM playbook_levers WHERE playbook_slug = :s"), {"s": slug})
    db.execute(text("DELETE FROM playbooks WHERE slug = :s"), {"s": slug})
    db.execute(text("DELETE FROM market_reports WHERE slug = :s"), {"s": slug})
    db.commit()


def test_the_backfill_fills_rows_once_and_lists_broken_tokens(db, texts):
    first = recompute(db)
    db.commit()
    rows = {r.section_id: r for r in db.query(MarketReportSection).filter_by(slug=texts)}
    assert rows["summary"].derived_from["series"] == ["brent"]
    assert rows["market"].derived_from is None
    panel = db.query(MarketReportPanel).filter_by(slug=texts).one()
    assert panel.derived_from["series"] == ["ttf-eu"]
    lever = db.query(PlaybookLever).filter_by(playbook_slug=texts).one()
    assert lever.derived_from["tokens"] == ["{{index:brent|asof}}"]
    assert any(f"{texts}/summary" in p and "broken token" in p for p in first.problems)

    second = recompute(db)
    db.rollback()
    assert sum(second.changed.values()) == 0
