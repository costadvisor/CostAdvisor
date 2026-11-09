"""Filling live numbers into text (Jira 210 S3).

* several tokens in one text, all filled, one batch;
* HTML formatting kept exactly: only text changes;
* a token split by markup, or unreadable, never reaches the reader;
* what the resolver writes is escaped (a fallback with `<` stays text);
* no token: the same string back, and no database touched;
* plain text and JSON bodies filled the same way;
* `used` and `problems` say what the text depends on and what is broken.

Series come from the resolver tests' fixtures; text and numbers are made up.
"""
from __future__ import annotations

from datetime import date

from app.services.content_drop.report_sanitize import sanitize_html
from app.services.live_tokens import NOT_AVAILABLE
from app.services.live_values import LiveText
from tests.test_live_resolver import _series, made  # noqa: F401 — fixture

AS_OF = date(2026, 8, 3)
DOC = date(2026, 8, 1)


def _two(db, made):
    a = _series(db, made, [(2025, 6, 100, "actual"), (2026, 6, 129.9, "actual")])
    b = _series(db, made, [(2026, 6, 41.25, "actual")])
    return a.commodity_key, b.commodity_key


# ── HTML ─────────────────────────────────────────────────────────────────────

def test_every_token_in_a_section_is_filled_and_markup_is_kept(db, made):
    a, b = _two(db, made)
    html = sanitize_html(
        f"<h2>Cost drivers</h2>"
        f"<p>Crude stood at <strong>{{{{index:{a}|last}}}}</strong>, up "
        f"<em>{{{{index:{a}|change12m}}}}</em> on the year.</p>"
        f"<ul><li>Gas: {{{{index:{b}|last}}}}</li><li>Data as of {{{{index:{b}|asof}}}}</li></ul>"
        f"<table><tbody><tr><td colspan=\"2\">{{{{index:{b}|last}}}}</td></tr></tbody></table>"
    )
    live = LiveText(db, as_of=AS_OF)
    live.prepare(html)
    out = live.html(html, doc_as_of=DOC)
    assert out == (
        "<h2>Cost drivers</h2>"
        "<p>Crude stood at <strong>129.9 · Jun 2026</strong>, up "
        "<em>+29.9% · Jun 2026</em> on the year.</p>"
        "<ul><li>Gas: 41.25 · Jun 2026</li><li>Data as of Jun 2026</li></ul>"
        '<table><tbody><tr><td colspan="2">41.25 · Jun 2026</td></tr></tbody></table>'
    )
    assert "{{" not in out
    assert len(live.used) == 4 and live.problems == []


def test_a_token_split_by_markup_never_reaches_the_reader(db, made):
    a, _ = _two(db, made)
    html = sanitize_html(f"<p>Price {{{{index:<strong>{a}</strong>|last}}}} today.</p>")
    live = LiveText(db, as_of=AS_OF)
    out = live.html(html, doc_as_of=DOC)
    assert "{{" not in out and "}}" not in out and a not in out
    assert out == f"<p>Price {NOT_AVAILABLE}<strong></strong> today.</p>"
    assert live.problems and "broken token" in live.problems[0]


def test_an_unreadable_token_shows_its_fallback_with_the_text_date(db, made):
    live = LiveText(db, as_of=AS_OF)
    out = live.html("<p>{{index:Bad Key|last|fallback=about 130}}</p>", doc_as_of=DOC)
    assert out == "<p>about 130 (as of Aug 2026)</p>"
    assert live.problems and "bad series key" in live.problems[0]


def test_a_missing_series_shows_the_fallback_dated_with_the_text(db, made):
    live = LiveText(db, as_of=AS_OF)
    out = live.html("<p>{{index:no-such-series|last|fallback=~€400/t}}</p>", doc_as_of=DOC)
    assert out == "<p>~€400/t (as of Aug 2026)</p>"
    assert live.used == set()


def test_what_the_resolver_writes_is_escaped(db, made):
    # Stored HTML holds a `<` typed in a fallback as `&lt;`; the parser makes it
    # text again, and the serialiser must escape it on the way out.
    live = LiveText(db, as_of=AS_OF)
    out = live.html('<p>{{index:no-such-series|last|fallback=&lt;b onclick="x"&gt;40&lt;/b&gt;}}</p>',
                    doc_as_of=None)
    assert out == '<p>&lt;b onclick="x"&gt;40&lt;/b&gt;</p>'
    assert sanitize_html(out) == out


def test_markup_inside_a_token_is_never_served(db, made):
    # Cannot come from a stored report (the loader would have rejected the
    # <b>); if it ever did, the sanitiser re-check catches it and the reader
    # gets "n/a", never the markup.
    live = LiveText(db, as_of=AS_OF)
    out = live.html('<p>{{index:no-such-series|last|fallback=<b onclick="x">40</b>}}</p>')
    assert out == f"<p>{NOT_AVAILABLE}</p>"


def test_text_without_tokens_comes_back_untouched_and_queries_nothing():
    live = LiveText(db=None)   # no database at all: any query would fail
    html = '<p>Plain <strong>text</strong>, capacity 400 kt.</p><svg viewBox="0 0 10 10"></svg>'
    assert live.html(html) is html
    assert live.text("no tokens here") == "no tokens here"
    assert live.json({"a": ["b", 1]}) == {"a": ["b", 1]}


# ── Plain text and JSON ──────────────────────────────────────────────────────

def test_plain_text_is_filled_and_not_escaped(db, made):
    a, _ = _two(db, made)
    live = LiveText(db, as_of=AS_OF)
    assert live.text(f"Crude < {{{{index:{a}|last}}}} & rising") == "Crude < 129.9 · Jun 2026 & rising"


def test_every_string_inside_a_json_body_is_filled(db, made):
    a, b = _two(db, made)
    body = {"title": "Drivers", "rows": [{"text": f"oil {{{{index:{a}|change12m}}}}"},
                                         {"text": f"gas {{{{index:{b}|asof}}}}", "n": 3}]}
    live = LiveText(db, as_of=AS_OF)
    live.prepare(body)
    assert live.json(body) == {"title": "Drivers", "rows": [{"text": "oil +29.9% · Jun 2026"},
                                                           {"text": "gas Jun 2026", "n": 3}]}


def test_an_unclosed_token_in_plain_text_is_cut(db, made):
    live = LiveText(db, as_of=AS_OF)
    assert live.text("Price {{index:brent|last and more") == f"Price {NOT_AVAILABLE}"
