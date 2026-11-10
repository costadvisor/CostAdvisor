"""Live-number tokens: the grammar, the parser and the display text (Jira 210 /
LIVE-3, S1).

Where the text quotes a number the app holds, it stores a token instead of
the typed number, and the server fills it in when someone reads it (S2 resolves,
S3 substitutes). This module knows no database: it parses a token and turns a
resolved value into the words a reader sees.

    {{index:KEY|last}}                      latest actual value of a series
    {{index:KEY|change12m}}                 % change of the latest actual vs 12 months before
    {{index:KEY|asof}}                      month of the latest actual
    {{recipe:CODE|REGION|LINE|weight}}      weight of one line of a platform recipe
    {{bar:REPORT|code=TYPECODE}}            a cost-structure bar (LIVE-4): the min–max
    {{bar:REPORT|line=LINE NAME}}           weight of that input across the platform
                                            recipes of the report's product lines
    ...|fallback=TEXT                       optional, last: shown when there is no data

`KEY` is the series key (`commodity_index.commodity_key`, e.g. `brent`).
`CODE` is the platform recipe code (`formula_templates.code`), `REGION` the
line's region (`Europe`, `NA`, ...) and `LINE` the line's name, as stored.

PROPOSED (Jira 210): the ticket leaves the syntax open. Tokens are plain
characters, so the report sanitiser lets them through unchanged. The grammar
lives here only: changing it later means changing this module.

Display (`format_display`):

    current    129.9 · Jun 2026      (`last` on a rebased index: 129.9 (index) · Jun 2026)
    late       129.9 · Jun 2026 (late)
    forecast   131.2 · Dec 2026 (forecast)
    missing    the fallback + " (as of Aug 2026)" (the text's own date), or
               "n/a (no current data)" when the token gives no fallback
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

INDEX = "index"
RECIPE = "recipe"
BAR = "bar"

LAST = "last"
CHANGE_12M = "change12m"
ASOF = "asof"
WEIGHT = "weight"
RANGE = "range"

INDEX_MEASURES = (LAST, CHANGE_12M, ASOF)
RECIPE_MEASURES = (WEIGHT,)

# Resolution states (S2 decides; this module only displays them).
CURRENT = "current"
LATE = "late"
FORECAST = "forecast"
MISSING = "missing"

NOT_AVAILABLE = "n/a (no current data)"

# A complete token. The body cannot hold braces, so a token never spans
# another one and a fallback cannot smuggle a token in.
TOKEN_RE = re.compile(r"\{\{(index|recipe|bar):([^{}]*)\}\}")
# The start of a token — what is left in text after every complete token is
# replaced means a broken one (unclosed, or split by markup).
OPENER_RE = re.compile(r"\{\{\s*(?:index|recipe|bar)\s*:")

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_CODE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_FALLBACK = "fallback="


class TokenError(ValueError):
    """A token that cannot be read. Carries the raw text and why."""

    def __init__(self, raw: str, why: str):
        super().__init__(f"{raw}: {why}")
        self.raw = raw
        self.why = why


@dataclass(frozen=True)
class Token:
    raw: str                        # exactly as written, the substitution key
    kind: str                       # index | recipe
    measure: str
    key: str | None = None          # index: the series key
    code: str | None = None         # recipe: the template code
    region: str | None = None       # recipe: the line's region
    line: str | None = None         # recipe / bar: the line's name
    report: str | None = None       # bar: the report slug
    fallback: str | None = None


@dataclass
class Resolved:
    status: str                     # current | late | forecast | missing
    value: float | None = None
    year: int | None = None         # the month the value is from
    month: int | None = None
    reason: str | None = None       # why missing (for logs and tests)
    low: float | None = None        # bar: the low end of the range (`value` is the high end)
    index_level: bool = False       # a rebased index (e.g. Jan 2023 = 100), not a price


def find_tokens(text: str) -> list[str]:
    """Every complete token in `text`, as written, in order."""
    return [m.group(0) for m in TOKEN_RE.finditer(text or "")]


def fallback_of(raw: str) -> str | None:
    """The fallback of a token, read even when the rest of it is broken, so a
    bad token still shows what its author meant as the safe text."""
    body = raw[2:-2] if raw.startswith("{{") and raw.endswith("}}") else raw
    at = body.find("|" + _FALLBACK)
    if at == -1:
        return None
    text = body[at + 1 + len(_FALLBACK):].strip()
    return text or None


def parse_token(raw: str) -> Token:
    """The token in `raw` (one complete `{{...}}`), or `TokenError`."""
    m = TOKEN_RE.fullmatch(raw.strip())
    if not m:
        raise TokenError(raw, "not a {{index:...}} or {{recipe:...}} token")
    kind, body = m.group(1), m.group(2)

    fallback = None
    at = body.find("|" + _FALLBACK)
    if at != -1:
        fallback = body[at + 1 + len(_FALLBACK):].strip() or None
        body = body[:at]
    parts = [p.strip() for p in body.split("|")]

    if kind == INDEX:
        if len(parts) != 2:
            raise TokenError(raw, "an index token is {{index:KEY|MEASURE}}")
        key, measure = parts
        if not _KEY_RE.match(key):
            raise TokenError(raw, f"bad series key {key!r} (lowercase letters, digits, hyphens)")
        if measure not in INDEX_MEASURES:
            raise TokenError(raw, f"unknown measure {measure!r}; one of {', '.join(INDEX_MEASURES)}")
        return Token(raw=raw, kind=kind, measure=measure, key=key, fallback=fallback)

    if kind == BAR:
        if len(parts) != 2:
            raise TokenError(raw, "a bar token is {{bar:REPORT|code=TYPECODE}} or {{bar:REPORT|line=NAME}}")
        report, selector = parts
        if not _SLUG_RE.match(report):
            raise TokenError(raw, f"bad report slug {report!r}")
        name, _, value = selector.partition("=")
        value = value.strip()
        if name.strip() == "code" and _CODE_RE.match(value):
            return Token(raw=raw, kind=kind, measure=RANGE, report=report, code=value, fallback=fallback)
        if name.strip() == "line" and value:
            return Token(raw=raw, kind=kind, measure=RANGE, report=report, line=value, fallback=fallback)
        raise TokenError(raw, "a bar names its input as code=TYPECODE or line=NAME")

    if len(parts) != 4:
        raise TokenError(raw, "a recipe token is {{recipe:CODE|REGION|LINE|weight}}")
    code, region, line, measure = parts
    if not _CODE_RE.match(code):
        raise TokenError(raw, f"bad recipe code {code!r}")
    if not region or not line:
        raise TokenError(raw, "a recipe token needs a region and a line name")
    if measure not in RECIPE_MEASURES:
        raise TokenError(raw, f"unknown measure {measure!r}; one of {', '.join(RECIPE_MEASURES)}")
    return Token(raw=raw, kind=kind, measure=measure, code=code, region=region, line=line,
                 fallback=fallback)


# ── Display ──────────────────────────────────────────────────────────────────

def month_label(year: int, month: int) -> str:
    return f"{calendar.month_abbr[month]} {year}"


def _number(value: float) -> str:
    size = abs(value)
    if size >= 1000:
        return f"{value:,.0f}"
    if size >= 100:
        return f"{value:,.1f}"
    return f"{value:,.2f}"


def _percent(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def range_text(low: float, high: float) -> str:
    """A cost bar's weight as the reports write it: "30–49%", or "12%"."""
    if round(low) == round(high):
        return f"{round(high):g}%"
    return f"{round(low):g}–{round(high):g}%"


def missing_text(fallback: str | None, doc_as_of: date | None) -> str:
    """What a reader sees when there is no data: the author's fallback, dated
    with the text's own date (the fallback is as old as the text), or n/a."""
    if not fallback:
        return NOT_AVAILABLE
    if doc_as_of is None:
        return fallback
    return f"{fallback} (as of {month_label(doc_as_of.year, doc_as_of.month)})"


def format_display(token: Token, resolved: Resolved, *, doc_as_of: date | None = None) -> str:
    """The text a reader sees in place of `token`."""
    if resolved.status == MISSING or (resolved.value is None and token.measure != ASOF):
        return missing_text(token.fallback, doc_as_of)

    if token.measure == WEIGHT:
        return _percent(resolved.value)
    if token.measure == RANGE:
        return range_text(resolved.low if resolved.low is not None else resolved.value, resolved.value)

    if resolved.year is None or resolved.month is None:
        return missing_text(token.fallback, doc_as_of)
    when = month_label(resolved.year, resolved.month)
    mark = {LATE: " (late)", FORECAST: " (forecast)"}.get(resolved.status, "")

    if token.measure == ASOF:
        return f"{when}{mark}"
    if token.measure == CHANGE_12M:
        return f"{resolved.value:+.1f}% · {when}{mark}"
    # A rebased index level is not a price: say so, so "129.9" is never read
    # as $/t. (97 of the 99 content series are levels, Jan 2023 = 100.)
    level = " (index)" if resolved.index_level else ""
    return f"{_number(resolved.value)}{level} · {when}{mark}"
