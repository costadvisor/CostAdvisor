"""Live-number tokens (Jira 210 S1): grammar, parser and display text.

No database: parsing and formatting only. Keys, codes and numbers are made up.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.live_tokens import (
    CHANGE_12M, CURRENT, FORECAST, INDEX, LAST, LATE, MISSING, NOT_AVAILABLE,
    RECIPE, WEIGHT, Resolved, TokenError, fallback_of, find_tokens, format_display,
    parse_token,
)

DOC = date(2026, 8, 1)


# ── Parsing ──────────────────────────────────────────────────────────────────

def test_an_index_token_parses():
    t = parse_token("{{index:brent|last}}")
    assert (t.kind, t.key, t.measure, t.fallback) == (INDEX, "brent", LAST, None)


def test_a_recipe_token_parses_with_spaces_in_the_line_name():
    t = parse_token("{{recipe:ABR-TEST-SLR|Europe|Silicon carbide grains|weight}}")
    assert (t.kind, t.code, t.region, t.line, t.measure) == (
        RECIPE, "ABR-TEST-SLR", "Europe", "Silicon carbide grains", WEIGHT)


def test_a_fallback_is_read_and_may_contain_bars_and_spaces():
    t = parse_token("{{index:ttf-eu|change12m|fallback=about +20% | rising}}")
    assert t.measure == CHANGE_12M
    assert t.fallback == "about +20% | rising"


@pytest.mark.parametrize("raw,why", [
    ("{{index:Brent|last}}", "bad series key"),              # uppercase key
    ("{{index:brent|price}}", "unknown measure"),
    ("{{index:brent}}", "{{index:KEY|MEASURE}}"),
    ("{{index:brent|last|extra}}", "{{index:KEY|MEASURE}}"),
    ("{{recipe:ABR|Europe|weight}}", "{{recipe:CODE|REGION|LINE|weight}}"),
    ("{{recipe:ABR|Europe|Line|share}}", "unknown measure"),
    ("{{recipe:AB R|Europe|Line|weight}}", "bad recipe code"),
    ("{{price:brent|last}}", "not a {{index"),
])
def test_bad_tokens_say_why(raw, why):
    with pytest.raises(TokenError) as e:
        parse_token(raw)
    assert why in e.value.why


def test_find_tokens_returns_every_complete_token_in_order():
    text = "Gas {{index:ttf-eu|last}} and oil {{index:brent|change12m}}, broken {{index:x"
    assert find_tokens(text) == ["{{index:ttf-eu|last}}", "{{index:brent|change12m}}"]


def test_a_fallback_is_found_even_in_a_broken_token():
    assert fallback_of("{{index:Bad Key|last|fallback=about 130}}") == "about 130"
    assert fallback_of("{{index:brent|last}}") is None


# ── Display ──────────────────────────────────────────────────────────────────

def _show(raw, **resolved):
    return format_display(parse_token(raw), Resolved(**resolved), doc_as_of=DOC)


def test_a_current_value_shows_the_value_and_its_month():
    assert _show("{{index:brent|last}}", status=CURRENT, value=129.9, year=2026, month=6) \
        == "129.9 · Jun 2026"


def test_a_late_value_is_marked_late():
    assert _show("{{index:brent|last}}", status=LATE, value=129.9, year=2025, month=12) \
        == "129.9 · Dec 2025 (late)"


def test_a_forecast_value_is_marked_forecast():
    assert _show("{{index:brent|last}}", status=FORECAST, value=131.24, year=2026, month=12) \
        == "131.2 · Dec 2026 (forecast)"


def test_a_change_is_signed_with_one_decimal():
    assert _show("{{index:brent|change12m}}", status=CURRENT, value=18.44, year=2026, month=6) \
        == "+18.4% · Jun 2026"
    assert _show("{{index:brent|change12m}}", status=CURRENT, value=-3.0, year=2026, month=6) \
        == "-3.0% · Jun 2026"


def test_as_of_shows_the_month_only():
    assert _show("{{index:brent|asof}}", status=CURRENT, year=2026, month=6) == "Jun 2026"


def test_a_recipe_weight_is_a_plain_percentage():
    assert _show("{{recipe:ABR|Europe|Line|weight}}", status=CURRENT, value=22.0) == "22%"
    assert _show("{{recipe:ABR|Europe|Line|weight}}", status=CURRENT, value=12.5) == "12.5%"


def test_missing_shows_the_fallback_dated_with_the_text():
    assert _show("{{index:gone|last|fallback=~€400/t}}", status=MISSING) == "~€400/t (as of Aug 2026)"


def test_missing_without_a_fallback_says_not_available():
    assert _show("{{index:gone|last}}", status=MISSING) == NOT_AVAILABLE


def test_numbers_are_sized_for_reading():
    assert _show("{{index:a|last}}", status=CURRENT, value=1234.5, year=2026, month=6).startswith("1,234 ·")
    assert _show("{{index:a|last}}", status=CURRENT, value=12.345, year=2026, month=6).startswith("12.35 ·")
