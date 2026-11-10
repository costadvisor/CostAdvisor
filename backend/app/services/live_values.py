"""Filling live numbers into text at read time (Jira 210 / LIVE-3, S3).

One `LiveText` per request. Hand it every string of a document first
(`prepare`), so all of its tokens resolve in one batch (S2), then ask for each
string back with its tokens filled in:

    live = LiveText(db)
    live.prepare(section_htmls, panel_data)
    html = live.html(section.html, doc_as_of=report_as_of)    # report HTML
    text = live.text(lever.guidance)                          # plain text
    body = live.json(block.body_json, doc_as_of=...)          # every string inside

**HTML is changed only in its text.** The stored HTML is parsed with the
report sanitiser's own parser, tokens are replaced inside text nodes only, and
the tree is written back with the sanitiser's serialiser, which escapes the
new text. In HTML each value is a chip (S6): `<span class="live-value">`,
with `live-late`, `live-forecast` or `live-missing` added when it is not
current — classes the sanitiser allows as server-written (`LIVE_CLASSES`).
Plain text and JSON get the bare text ("(late)", "(forecast)" included). Markup — paragraphs, bold, lists, tables, the Kraljic SVG — is never
touched, and a fallback containing `<` arrives as text, not markup. The result
goes through `sanitize_html` again: cheap insurance that nothing the resolver
wrote can reach the page as markup.

**A reader never sees a token.** A token that cannot be read shows its
fallback (or "n/a"). A token broken by markup (`{{index:<b>brent</b>|last}}`
is three text nodes, so no node holds a whole token) is cut from its opening
`{{` to its closing `}}` across the nodes and shows "n/a". Every broken token
is kept in `problems` for whoever loads or checks the text.

**No token, no change.** A string without `{{` comes back as the same object,
untouched, and nothing is queried.

`used` collects every token that resolved, which is what each section,
block and lever records as its `derived_from` (S5).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Iterable

from bs4.element import Comment, NavigableString
from sqlalchemy.orm import Session

from app.services.content_drop.report_sanitize import (
    ReportRejected, parse, sanitize_html, serialize,
)
from app.services.live_resolver import resolve
from app.services.live_tokens import (
    ASOF, BAR, CURRENT, MISSING, NOT_AVAILABLE, OPENER_RE, RECIPE, TOKEN_RE, Resolved, Token,
    TokenError, fallback_of, format_display, missing_text, parse_token,
)

logger = logging.getLogger(__name__)


def _strings(value: Any) -> Iterable[str]:
    """Every string inside `value`: a string, or lists / dicts of them."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


# ── What a text depends on (S5) ──────────────────────────────────────────────
#
# Read when text is written (content load, block save), with no database: a
# text depends on the tokens it carries, whether or not they resolve today.

def _scan_string(s: str, tokens: list[Token], problems: list[str]) -> None:
    if "{{" not in s:
        return
    for m in TOKEN_RE.finditer(s):
        try:
            tokens.append(parse_token(m.group(0)))
        except TokenError as e:
            problems.append(f"{e.raw} ({e.why})")
    rest = TOKEN_RE.sub("", s)
    m = OPENER_RE.search(rest)
    if m:
        problems.append(f"broken token (unclosed or split by markup): {rest[m.start():m.start() + 60]!r}")


def scan_text(*values: Any) -> tuple[list[Token], list[str]]:
    """Tokens and broken tokens in plain text, or in every string of JSON."""
    tokens: list[Token] = []
    problems: list[str] = []
    for s in (s for v in values for s in _strings(v)):
        _scan_string(s, tokens, problems)
    return tokens, problems


def scan_html(*htmls: str | None) -> tuple[list[Token], list[str]]:
    """Tokens and broken tokens in HTML, read text node by text node — the
    same way they are filled in, so a token split by markup is broken here too."""
    tokens: list[Token] = []
    problems: list[str] = []
    for html in htmls:
        if not html or "{{" not in html:
            continue
        for node in parse(html).find_all(string=True):
            if not isinstance(node, Comment) and type(node) is NavigableString:
                _scan_string(str(node), tokens, problems)
    return tokens, problems


def derived_from(tokens: Iterable[Token]) -> dict | None:
    """The `derived_from` record of a text: the series and recipe lines it
    reads, and its tokens as written. None for a text with no token.

    Same JSONB home as CON-5's staleness descriptor and the trust review's
    `review_derived_from`; LIVE-5 reads `series` to flag text whose data moved.
    """
    tokens = list(tokens)
    if not tokens:
        return None
    return {
        "series": sorted({t.key for t in tokens if t.key}),
        "recipe_lines": sorted(
            ({"code": t.code, "region": t.region, "line": t.line} for t in tokens if t.kind == RECIPE),
            key=lambda r: (r["code"], r["region"], r["line"])),
        "bars": sorted(
            ({"report": t.report, "code": t.code, "line": t.line} for t in tokens if t.kind == BAR),
            key=lambda r: (r["report"], r["code"] or "", r["line"] or "")),
        "tokens": sorted({t.raw for t in tokens}),
    }


class LiveText:
    def __init__(self, db: Session, *, as_of: date | None = None):
        self.db = db
        self.as_of = as_of                      # judge "late" against this (default today)
        self._tokens: dict[str, Token | TokenError] = {}
        self._resolved: dict[Token, Resolved] = {}
        self.used: set[str] = set()             # tokens that resolved (S5 derived_from)
        self.problems: list[str] = []           # broken or unreadable tokens, as written

    # ── Resolution ────────────────────────────────────────────────────────

    def prepare(self, *values: Any) -> None:
        """Find every token in `values` (strings, lists, dicts) and resolve the
        new ones in one batch."""
        fresh: list[Token] = []
        for s in (s for v in values for s in _strings(v)):
            if "{{" not in s:
                continue
            for m in TOKEN_RE.finditer(s):
                raw = m.group(0)
                if raw in self._tokens:
                    continue
                try:
                    self._tokens[raw] = parse_token(raw)
                    fresh.append(self._tokens[raw])
                except TokenError as e:
                    self._tokens[raw] = e
        if fresh:
            self._resolved.update(resolve(self.db, fresh, as_of=self.as_of))

    def _render(self, raw: str, doc_as_of: date | None) -> tuple[str, str]:
        """(display text, state) for one complete token."""
        if raw not in self._tokens:
            self.prepare(raw)
        token = self._tokens[raw]
        if isinstance(token, TokenError):
            self.problems.append(f"{raw} ({token.why})")
            return missing_text(fallback_of(raw), doc_as_of), MISSING
        resolved = self._resolved.get(token) or Resolved(MISSING, reason="not resolved")
        if resolved.status != MISSING:
            self.used.add(raw)
        state = resolved.status if resolved.value is not None or token.measure == ASOF else MISSING
        return format_display(token, resolved, doc_as_of=doc_as_of), state

    def preview(self, raw: str, *, doc_as_of: date | None = None) -> dict:
        """What one token would show, for the editor's picker (S7): the text,
        its state, and why it cannot be read when it cannot."""
        raw = raw.strip()
        try:
            token = parse_token(raw)
        except TokenError as e:
            return {"token": raw, "valid": False, "error": e.why, "state": MISSING,
                    "display": missing_text(fallback_of(raw), doc_as_of), "derived_from": None}
        display, state = self._render(raw, doc_as_of)
        return {"token": raw, "valid": True, "error": None, "state": state, "display": display,
                "derived_from": derived_from([token])}

    def _display(self, raw: str, doc_as_of: date | None) -> str:
        return self._render(raw, doc_as_of)[0]

    def _pieces(self, soup, s: str, doc_as_of: date | None) -> tuple[list, bool, Resolved | None]:
        """One text node's replacement in HTML: its text with every token as a
        chip (S6) — `<span class="live-value">`, plus `live-late`,
        `live-forecast` or `live-missing` — whether a token is left open, and
        the range of a cost-bar token in it (LIVE-4), so its bar can be redrawn.
        A bar's own weight is already styled: a current one stays plain text."""
        out: list = []
        pos = 0
        bar_range: Resolved | None = None
        for m in TOKEN_RE.finditer(s):
            before, _ = self._fill(s[pos:m.start()], doc_as_of)
            if before:
                out.append(NavigableString(before))
            raw = m.group(0)
            display, state = self._render(raw, doc_as_of)
            token = self._tokens.get(raw)
            if isinstance(token, Token) and token.kind == BAR and state == CURRENT:
                out.append(NavigableString(display))
                bar_range = self._resolved.get(token)
            else:
                chip = soup.new_tag("span")
                chip["class"] = ["live-value"] + ([f"live-{state}"] if state != CURRENT else [])
                chip.string = display
                out.append(chip)
            pos = m.end()
        after, open_ = self._fill(s[pos:], doc_as_of)
        if after:
            out.append(NavigableString(after))
        return out or [NavigableString("")], open_, bar_range

    @staticmethod
    def _redraw_bar(soup, bar, low: float, high: float) -> None:
        """A cost bar's fills drawn from its range, as the reports draw them:
        `lo` is as wide as the low end, `hi` stacks the gap up to the high end
        (and is left out when the range is one number)."""
        lo = bar.find(class_="lo")
        if lo is None:
            return
        lo["style"] = f"width:{round(low):g}%"
        hi = bar.find(class_="hi")
        gap = round(high) - round(low)
        if gap > 0:
            if hi is None:
                hi = soup.new_tag("div")
                hi["class"] = ["fill", "hi"]
                lo.insert_after(hi)
            hi["style"] = f"width:{gap:g}%"
        elif hi is not None:
            hi.decompose()

    def _fill(self, s: str, doc_as_of: date | None) -> tuple[str, bool]:
        """`s` with complete tokens filled in; and whether a token opener is
        left unclosed (cut here, with "n/a" in its place)."""
        s = TOKEN_RE.sub(lambda m: self._display(m.group(0), doc_as_of), s)
        m = OPENER_RE.search(s)
        if m is None:
            return s, False
        end = s.find("}}", m.end())
        self.problems.append(f"broken token: {s[m.start():m.start() + 60]!r}")
        if end != -1:          # closed in the same string but unreadable (e.g. braces inside)
            rest, open_ = self._fill(s[end + 2:], doc_as_of)
            return s[:m.start()] + NOT_AVAILABLE + rest, open_
        return s[:m.start()] + NOT_AVAILABLE, True

    # ── Output ────────────────────────────────────────────────────────────

    def text(self, s: str | None, *, doc_as_of: date | None = None) -> str | None:
        """Plain text with its tokens filled in."""
        if not s or "{{" not in s:
            return s
        out, _ = self._fill(s, doc_as_of)
        return out

    def json(self, value: Any, *, doc_as_of: date | None = None) -> Any:
        """`value` with every string inside filled in; keys untouched."""
        if isinstance(value, str):
            return self.text(value, doc_as_of=doc_as_of)
        if isinstance(value, dict):
            return {k: self.json(v, doc_as_of=doc_as_of) for k, v in value.items()}
        if isinstance(value, list):
            return [self.json(v, doc_as_of=doc_as_of) for v in value]
        return value

    def html(self, html: str | None, *, doc_as_of: date | None = None) -> str | None:
        """Sanitised report HTML with its tokens filled in, markup untouched."""
        if not html or "{{" not in html:
            return html
        soup = parse(html)
        in_broken = False      # inside a token that markup split across nodes
        for node in list(soup.find_all(string=True)):
            if isinstance(node, Comment) or type(node) is not NavigableString:
                continue
            s = str(node)
            new = s
            if in_broken:
                end = new.find("}}")
                if end == -1:
                    node.replace_with(NavigableString(""))
                    continue
                new, in_broken = new[end + 2:], False
            if "{{" in new:
                bar = node.find_parent(class_="cost-bar")
                pieces, in_broken, bar_range = self._pieces(soup, new, doc_as_of)
                node.replace_with(*pieces)
                if bar is not None and bar_range is not None:
                    self._redraw_bar(soup, bar, bar_range.low, bar_range.value)
            elif new != s:
                node.replace_with(NavigableString(new))
        out = serialize(soup.contents)
        try:
            return sanitize_html(out)
        except ReportRejected as e:     # cannot happen from text edits; never serve it if it does
            logger.error("live values: filled HTML failed the sanitiser: %s", e)
            return TOKEN_RE.sub(NOT_AVAILABLE, html)
