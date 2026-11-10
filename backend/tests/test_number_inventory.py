"""LIVE-4 S1 + S2: every typed number classified, matched to our data, drift measured.

S1 (no database): cost bars read from their structure (and not counted again
as percentages); moves, prices, dates (with or without an as-of cue) and other
percentages from each sentence, with the series the sentence names.

S2: cost bars against the report's recipe lines (by type code, else line
name); moves, as-of dates and prices against the series a sentence names;
an index level is never offered as a price; events and shares stay text.

Text, codes and numbers are made up.
"""
from __future__ import annotations

from datetime import date

from app.services.number_inventory import (
    COST_BAR, DATE, PCT_MOVE, PERCENT, PRICE, NumberItem, scan_report_section,
)
from app.services.number_matching import (
    AMBIGUOUS, DRIFTED, MANUAL, MATCH, NOT_CONVERTIBLE, NOT_OURS, _cost_bar, _typed_month,
    match_items,
)
from tests.test_live_resolver import _series, made  # noqa: F401 — fixture

AS_OF = date(2026, 8, 3)

BAR = ('<div class="cost-bar"><span class="label">{label}</span><div class="track">'
       '<div class="fill lo" style="width:30%"></div><div class="fill hi" style="width:49%"></div>'
       '</div><span class="weight">{weight}</span></div>')


# ── S1: inventory ────────────────────────────────────────────────────────────

def _scan(html, vocab=None):
    import re
    vocab = vocab or {}
    matcher = (re.compile(r"\b(" + "|".join(map(re.escape, vocab)) + r")\b", re.I)
               if vocab else None)
    return scan_report_section("rep", "process", html, matcher, vocab)


def test_cost_bars_are_read_from_their_structure_and_not_counted_twice():
    items = _scan(BAR.format(label="Iron (FE-MB)", weight="30–49%")
                  + BAR.format(label="Supplier margin", weight="12%"))
    bars = [i for i in items if i.kind == COST_BAR]
    assert [(b.extra["code"], b.extra["lo"], b.extra["hi"]) for b in bars] == [
        ("FE-MB", 30, 49), (None, 12, 12)]
    assert not [i for i in items if i.kind == PERCENT]


def test_sentences_are_classified_with_the_series_they_name():
    items = _scan(
        "<p>Brent rose 12% over the year. Prices reached $450/t in Europe. "
        "Data as of June 2026. The plant opened in March 2024. Purity is 99.5%.</p>",
        vocab={"brent": {7}})
    kinds = [(i.kind, i.text) for i in items]
    assert (PCT_MOVE, "12%") in kinds
    assert (PRICE, "$450/t") in kinds
    assert (DATE, "June 2026") in kinds and (DATE, "March 2024") in kinds
    assert (PERCENT, "99.5%") in kinds
    move = next(i for i in items if i.kind == PCT_MOVE)
    assert move.series_ids == frozenset({7}) and move.extra["verb"] == "rose"
    dates = {i.text: i.extra["as_of_cue"] for i in items if i.kind == DATE}
    assert dates == {"June 2026": True, "March 2024": False}


# ── S2: cost bars ────────────────────────────────────────────────────────────

def _bar(label, lo, hi, code=None):
    return NumberItem(source="report", doc="rep", part="process", kind=COST_BAR,
                      text=f"{lo}–{hi}%", context=label,
                      extra={"label": label, "code": code, "lo": lo, "hi": hi})


def test_a_cost_bar_matches_by_code_then_by_line_name_and_measures_drift():
    by_code = {"rep": {"FE-MB": [30.0, 41.0, 49.0]}}
    by_name = {"rep": {"supplier margin": [9.0, 14.0], "x": [1.0]}}
    same = _cost_bar(_bar("Iron (FE-MB)", 30, 49, "FE-MB"), by_code, by_name)
    assert (same.status, same.typed, same.today) == (MATCH, "30–49%", "30–49%")
    drift = _cost_bar(_bar("Supplier margin", 10, 14), by_code, by_name)
    assert (drift.status, drift.today) == (DRIFTED, "9–14%")
    lost = _cost_bar(_bar("Other inputs", 5, 8), by_code, by_name)
    assert lost.status == MANUAL and "no recipe line" in lost.why


def test_quarters_and_halves_are_read_as_their_last_month():
    assert _typed_month("Q2 2026") == (2026, 6)
    assert _typed_month("H2 2026") == (2026, 12)
    assert _typed_month("September 2026") == (2026, 9)


# ── S2: series-based numbers ─────────────────────────────────────────────────

def _item(kind, text, context, sid, **extra):
    return NumberItem(source="report", doc="rep", part="supply", kind=kind, text=text,
                      context=context, series_ids=frozenset(sid), extra=extra)


def test_moves_dates_and_prices_against_the_series_named(db, made):
    s = _series(db, made, [(2025, 6, 100, "actual"), (2026, 6, 112, "actual")])
    level = _series(db, made, [(2026, 6, 130, "actual")])
    level.value_kind = "index_level"
    db.commit()
    items = [
        _item(PCT_MOVE, "12%", "It rose 12% year-on-year.", {s.id}, verb="rose"),
        _item(PCT_MOVE, "5%", "It fell 5% since spring.", {s.id}, verb="fell"),
        _item(DATE, "June 2026", "Data as of June 2026.", {s.id}, as_of_cue=True),
        _item(DATE, "March 2024", "The plant opened in March 2024.", {s.id}, as_of_cue=False),
        _item(PRICE, "$450/t", "It costs $450/t.", {level.id}),
        _item(PRICE, "$450/t", "Both cost $450/t.", {s.id, level.id}),
        _item(PERCENT, "99.5%", "Purity is 99.5%.", set()),
    ]
    m = match_items(db, items, as_of=AS_OF)
    yoy, down, asof, event, level_price, two, share = m
    assert (yoy.status, yoy.typed, yoy.today, yoy.confidence) == (MATCH, "+12.0%", "+12.0%", "high")
    assert yoy.token == f"{{{{index:{s.commodity_key}|change12m}}}}"
    assert (down.status, down.typed, down.confidence) == (DRIFTED, "-5.0%", "low")
    assert (asof.status, asof.today) == (MATCH, "Jun 2026")
    assert event.status == NOT_OURS
    assert level_price.status == NOT_CONVERTIBLE and "index level" in level_price.why
    assert two.status == AMBIGUOUS
    assert share.status == NOT_OURS
