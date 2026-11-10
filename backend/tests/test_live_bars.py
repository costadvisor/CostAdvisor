"""Live cost-structure bars (LIVE-4 S3).

* `{{bar:REPORT|code=...}}` / `{{bar:REPORT|line=...}}` parse, and a bad one
  says why;
* the range is the min–max weight of that input across the platform recipes
  of the report's own product lines (by type code, or by line name);
* in a report the bar's weight is filled as plain text and its fills are
  redrawn: `lo` as wide as the low end, `hi` the gap up to the high end —
  added when a single value becomes a range, removed when a range collapses;
* an unknown input shows the fallback and leaves the drawing alone.

A made-up family, line, report and recipes, flushed and rolled back.
"""
from __future__ import annotations

import uuid

import pytest

from app.database import bypass_rls_var
from app.models.chemical_family import ChemicalFamily
from app.models.formula_template import FormulaTemplate, FormulaTemplateComponent
from app.models.index_layer import TypeCode
from app.models.market_report import MarketReport, MarketReportLine
from app.models.product_line import ProductLine
from app.services.content_drop.report_sanitize import sanitize_html
from app.services.live_tokens import BAR, RANGE, TokenError, parse_token, range_text
from app.services.live_values import LiveText

BAR_HTML = ('<div class="cost-bar"><span class="label">{label}</span><div class="track">'
            '{fills}</div><span class="weight">{token}</span></div>')
ONE = '<div class="fill lo" style="width:12%"></div>'
TWO = '<div class="fill lo" style="width:30%"></div><div class="fill hi" style="width:19%"></div>'


@pytest.fixture
def world(db, tenant_a):
    """Report `slug` serving one line, with two platform recipes:
    FE-TEST lines 26% and 49%; 'Supplier margin' 9% and 14%."""
    bypass_rls_var.set(True)
    tag = uuid.uuid4().hex[:6]
    fam = ChemicalFamily(name=f"Test family {tag}")
    db.add(fam)
    db.flush()
    line = ProductLine(family_id=fam.id, name=f"Test line {tag}", line_key=f"Test family|||{tag}")
    db.add(line)
    # "ambiguous" is the one resolution allowed without a target series.
    code = TypeCode(code=f"FE-T{tag}".upper(), resolution="ambiguous")
    slug = f"test_bars_{tag}"
    db.add_all([code, MarketReport(slug=slug, report_file=f"{slug}.html", name="Bars test")])
    db.flush()
    db.add(MarketReportLine(slug=slug, line_key=line.line_key, product_line_id=line.id,
                            source="report_map"))
    for i, (iron, margin) in enumerate(((26, 9), (49, 14))):
        t = FormulaTemplate(name=f"Bars {tag} {i}", code=f"TST-{tag}-{i}", team_id=None,
                            product_line_id=line.id, created_by=tenant_a["user_id"])
        db.add(t)
        db.flush()
        db.add_all([
            FormulaTemplateComponent(template_id=t.id, name="Iron unit", component_type="index",
                                     type_code_id=code.id, region="Europe", weight_pct=iron),
            FormulaTemplateComponent(template_id=t.id, name="Supplier margin",
                                     component_type="fixed", region="Europe", weight_pct=margin),
        ])
    db.flush()
    yield {"slug": slug, "code": code.code}
    db.rollback()


def test_bar_tokens_parse_and_say_why_when_bad():
    t = parse_token("{{bar:air_separation_gases|code=FE-MB}}")
    assert (t.kind, t.measure, t.report, t.code, t.line) == (BAR, RANGE, "air_separation_gases", "FE-MB", None)
    assert parse_token("{{bar:x|line=Supplier margin}}").line == "Supplier margin"
    with pytest.raises(TokenError) as e:
        parse_token("{{bar:x|share=FE-MB}}")
    assert "code=TYPECODE or line=NAME" in e.value.why
    assert range_text(30, 49) == "30–49%" and range_text(12.2, 11.8) == "12%"


def test_a_coded_bar_is_filled_and_its_single_fill_becomes_a_range(db, world):
    html = sanitize_html(BAR_HTML.format(label="Iron", fills=ONE,
                                         token=f"{{{{bar:{world['slug']}|code={world['code']}}}}}"))
    out = LiveText(db).html(html)
    assert out == ('<div class="cost-bar"><span class="label">Iron</span><div class="track">'
                   '<div class="fill lo" style="width:26%"></div>'
                   '<div class="fill hi" style="width:23%"></div></div>'
                   '<span class="weight">26–49%</span></div>')
    assert sanitize_html(out) == out


def test_a_bar_by_line_name_redraws_its_range(db, world):
    html = sanitize_html(BAR_HTML.format(label="Supplier margin", fills=TWO,
                                         token=f"{{{{bar:{world['slug']}|line=supplier margin}}}}"))
    out = LiveText(db).html(html)
    assert 'style="width:9%"' in out and 'class="fill hi" style="width:5%"' in out
    assert '<span class="weight">9–14%</span>' in out


def test_a_range_that_collapses_loses_its_hi_fill(db, world):
    # Both recipes give the same weight -> one number, no gap to draw.
    html = sanitize_html(BAR_HTML.format(label="Iron", fills=TWO,
                                         token="{{bar:%s|code=%s}}" % (world["slug"], world["code"])))
    live = LiveText(db)
    live.prepare(html)
    token = next(iter(live._resolved))
    live._resolved[token].low = live._resolved[token].value = 12.0
    out = live.html(html)
    assert 'class="fill hi"' not in out and 'style="width:12%"' in out
    assert '<span class="weight">12%</span>' in out


def test_an_unknown_input_shows_the_fallback_and_keeps_the_drawing(db, world):
    html = sanitize_html(BAR_HTML.format(
        label="Packaging", fills=TWO,
        token=f"{{{{bar:{world['slug']}|line=Packaging|fallback=4–5%}}}}"))
    out = LiveText(db).html(html)
    assert TWO in out
    assert '<span class="live-value live-missing">4–5%</span>' in out
