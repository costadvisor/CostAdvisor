"""How a live number looks (Jira 210 S6).

* in report HTML each value is a chip, marked late / forecast / missing;
* a rebased index level reads "(index)", so it is never taken for a price —
  and only on `last` (a % change or a month is not a level);
* the sanitiser accepts the chip classes the server writes, while the
  measured allowlist of authored classes stays exactly as it was;
* plain text (blocks, levers) keeps the words, no markup.

Series come from the resolver tests' fixtures; numbers are made up.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.content_drop import report_sanitize as rs
from app.services.live_tokens import CURRENT, Resolved, format_display, parse_token
from app.services.live_values import LiveText
from tests.test_live_resolver import _series, made  # noqa: F401 — fixture


def _html(db, raw, as_of):
    live = LiveText(db, as_of=as_of)
    return live.html(f"<p>{raw}</p>")


def test_a_late_value_is_an_amber_chip(db, made):
    s = _series(db, made, [(2026, 6, 100, "actual")])
    out = _html(db, f"{{{{index:{s.commodity_key}|last}}}}", date(2027, 1, 5))
    assert out == '<p><span class="live-value live-late">100.0 · Jun 2026 (late)</span></p>'


def test_a_forecast_value_is_a_forecast_chip(db, made):
    s = _series(db, made, [(2026, 9, 104, "forecast")])
    out = _html(db, f"{{{{index:{s.commodity_key}|last}}}}", date(2026, 11, 5))
    assert out == '<p><span class="live-value live-forecast">104.0 · Sep 2026 (forecast)</span></p>'


def test_a_rebased_index_says_index_on_its_level_only(db, made):
    s = _series(db, made, [(2025, 6, 100, "actual"), (2026, 6, 129.9, "actual")])
    s.value_kind = "index_level"
    db.commit()
    live = LiveText(db, as_of=date(2026, 8, 3))
    key = s.commodity_key
    assert live.text(f"{{{{index:{key}|last}}}}") == "129.9 (index) · Jun 2026"
    assert live.text(f"{{{{index:{key}|change12m}}}}") == "+29.9% · Jun 2026"
    assert live.text(f"{{{{index:{key}|asof}}}}") == "Jun 2026"


def test_plain_text_keeps_the_words_and_no_markup(db, made):
    s = _series(db, made, [(2026, 6, 100, "actual")])
    out = LiveText(db, as_of=date(2027, 1, 5)).text(f"Oil {{{{index:{s.commodity_key}|last}}}}")
    assert out == "Oil 100.0 · Jun 2026 (late)"


def test_the_formatter_labels_levels_on_last_only():
    level = Resolved(CURRENT, value=129.9, year=2026, month=6, index_level=True)
    assert format_display(parse_token("{{index:a|last}}"), level) == "129.9 (index) · Jun 2026"
    change = Resolved(CURRENT, value=4.2, year=2026, month=6, index_level=True)
    assert format_display(parse_token("{{index:a|change12m}}"), change) == "+4.2% · Jun 2026"


def test_the_sanitiser_takes_the_chip_and_the_measured_list_is_unchanged():
    chip = '<p><span class="live-value live-late">1 · Jun 2026 (late)</span></p>'
    assert rs.sanitize_html(chip) == chip
    assert len(rs.ALLOWED_CLASSES) == 31 and not (rs.LIVE_CLASSES & rs.ALLOWED_CLASSES)
    with pytest.raises(rs.ReportRejected):
        rs.sanitize_html('<p><span class="live-anything">x</span></p>')
