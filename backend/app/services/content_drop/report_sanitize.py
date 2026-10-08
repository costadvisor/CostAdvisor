"""Allowlist sanitiser for the delivered market reports (spec D6).

The reports are HTML files written by the content programme's generator.
The app renders their seven sections with `dangerouslySetInnerHTML`, so what
reaches the database must be HTML we could have written ourselves. This
module is the gate: it checks a parsed section against a closed allowlist and
re-serialises it from the parse tree. Nothing of the source is passed through
as text.

**The allowlist is measured, not guessed.** Across the delivered files, inside the
seven `<section id>` fragments, the corpus uses exactly:

* 25 tags (`ALLOWED_TAGS`), 4 of them SVG — one Kraljic chart per file;
* 21 attribute names, allowed only on the tags that carry them
  (`ALLOWED_ATTRIBUTES`, e.g. `colspan` on `<td>` only);
* 31 class names (`ALLOWED_CLASSES`), every one defined in the report
  stylesheet (`frontend/src/styles/report.css`);
* 15 CSS properties in `style` attributes (`ALLOWED_CSS_PROPERTIES`).

There is no `href` or `src` on the list, so a section cannot link out, load a
resource or run a handler. Values are checked too: a `style` declaration
must be one of the 15 properties with a plain value (numbers, lengths, hex
colours, keywords, `var(--name)`), so no `url(` or `expression(`; SVG
geometry must be numbers; `xmlns` must be the SVG namespace.

**Anything outside the list is a violation, and a violation rejects the whole
report** (`ReportRejected`). The loader reports it and loads nothing for that
report: a sanitiser that silently strips unknown markup would hide a
regenerated report that changed shape. Measured today: zero rejections.

Three things are handled rather than rejected, because they are not content:

* **HTML comments are dropped.** Nearly every file carries one inside the Kraljic SVG
  (the author's note on the dot's position). The loader reads it into the
  Kraljic data before the section is serialised.
* **Entities are normalised.** The parser decodes them (`&mdash;` → `—`,
  `&rsquo;` → `’`, `&nbsp;` → U+00A0) and the serialiser writes characters
  back, escaping only `&`, `<`, `>` (and `"` inside attributes).
* **Unclosed elements are closed where their parent ends.** One file
  (in its section `tech`) opens a `<strong>` it never closes; the
  parser closes it at the end of its paragraph, so the stored HTML is
  balanced. `unclosed_tags` measures it on the raw source so the loader can
  say so.

The parser is BeautifulSoup's `html.parser` builder (already a dependency).
It lowercases attribute names; `viewBox` is written back in its SVG case.
"""
from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from typing import Iterable

from bs4 import BeautifulSoup
from bs4.element import Comment, NavigableString, Tag

# ── The allowlist (measured across the delivered reports) ────────────────────

HTML_TAGS = frozenset({
    "br", "code", "div", "em", "h2", "h3", "h4", "li", "p", "small", "span",
    "strong", "sub", "sup", "table", "tbody", "td", "th", "thead", "tr", "ul",
})
SVG_TAGS = frozenset({"svg", "rect", "circle", "text"})
ALLOWED_TAGS = HTML_TAGS | SVG_TAGS

# Attribute names per tag, lowercased as the parser gives them. A tag not in
# this map takes no attributes at all.
ALLOWED_ATTRIBUTES: dict[str, frozenset[str]] = {
    "div": frozenset({"class", "style"}),
    "p": frozenset({"class", "style"}),
    "span": frozenset({"class", "style"}),
    "table": frozenset({"class"}),
    "td": frozenset({"colspan", "rowspan", "style"}),
    "svg": frozenset({"style", "viewbox", "width", "xmlns"}),
    "rect": frozenset({"fill", "height", "rx", "width", "x", "y"}),
    "circle": frozenset({"cx", "cy", "fill", "r", "stroke", "stroke-width"}),
    "text": frozenset({"fill", "font-size", "font-weight", "text-anchor",
                       "transform", "x", "y"}),
}
ALL_ATTRIBUTES = frozenset().union(*ALLOWED_ATTRIBUTES.values())

ALLOWED_CLASSES = frozenset({
    "analysis", "app-card", "chain-arrow", "chain-diagram", "chain-step", "con",
    "cost-bar", "driver-card", "fill", "grid-2", "hi", "insight", "kraljic-wrap",
    "label", "lo", "pestel-table", "printable-note", "pro", "risk", "src", "stat",
    "stat-grid", "supplier-table", "supplier-tag", "tag-distributor",
    "tag-integrated", "tag-producer", "track", "value", "warning", "weight",
})

ALLOWED_CSS_PROPERTIES = frozenset({
    "background", "border", "color", "font-family", "font-size", "font-style",
    "font-weight", "grid-template-columns", "letter-spacing", "margin",
    "margin-bottom", "margin-top", "padding-top", "text-align", "width",
})

# SVG structure: the chart is an <svg> holding only shapes and labels, never
# HTML (HTML inside foreign content is how parser-differential tricks start).
SVG_CHILDREN = frozenset({"rect", "circle", "text"})
EMPTY_SVG_TAGS = frozenset({"rect", "circle"})   # written self-closed
VOID_TAGS = frozenset({"br"})
SVG_NAMESPACE = "http://www.w3.org/2000/svg"

# The parser lowercases attribute names; SVG is case-sensitive in XML.
_ATTRIBUTE_CASE = {"viewbox": "viewBox"}

# ── Value rules ──────────────────────────────────────────────────────────────

_NUMBER = r"-?(?:\d+(?:\.\d+)?|\.\d+)"
_NUMBER_RE = re.compile(rf"^{_NUMBER}$")
_COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_ATTRIBUTE_RULES: dict[str, re.Pattern] = {
    "colspan": re.compile(r"^\d{1,2}$"),
    "rowspan": re.compile(r"^\d{1,2}$"),
    "x": _NUMBER_RE, "y": _NUMBER_RE, "cx": _NUMBER_RE, "cy": _NUMBER_RE,
    "r": _NUMBER_RE, "rx": _NUMBER_RE, "width": _NUMBER_RE, "height": _NUMBER_RE,
    "stroke-width": _NUMBER_RE, "font-size": _NUMBER_RE,
    "font-weight": re.compile(r"^(?:\d{3}|normal|bold)$"),
    "fill": _COLOUR_RE, "stroke": _COLOUR_RE,
    "text-anchor": re.compile(r"^(?:start|middle|end)$"),
    "transform": re.compile(rf"^rotate\({_NUMBER}(?: {_NUMBER}){{0,2}}\)$"),
    "viewbox": re.compile(rf"^{_NUMBER}(?: {_NUMBER}){{3}}$"),
    "xmlns": re.compile(rf"^{re.escape(SVG_NAMESPACE)}$"),
}

# One CSS value token: a number with an optional unit, a hex colour, a
# keyword, or a custom-property reference. Nothing that can fetch or run.
_CSS_TOKEN = (
    rf"(?:{_NUMBER}(?:%|px|rem|em|fr)?"
    r"|#[0-9a-fA-F]{3,8}"
    r"|[a-zA-Z][a-zA-Z-]*"
    r"|var\(--[a-zA-Z0-9-]+\))"
)
_CSS_VALUE_RE = re.compile(rf"^{_CSS_TOKEN}(?:\s*,\s*{_CSS_TOKEN}|\s+{_CSS_TOKEN})*$")


class ReportRejected(ValueError):
    """A report section uses something outside the allowlist. Carries every
    violation found, not just the first, so one run lists them all."""

    def __init__(self, violations: list[str]):
        self.violations = list(violations)
        shown = "; ".join(self.violations[:5])
        more = f" (+{len(self.violations) - 5} more)" if len(self.violations) > 5 else ""
        super().__init__(f"{len(self.violations)} allowlist violation(s): {shown}{more}")


# ── Parsing ──────────────────────────────────────────────────────────────────

def parse(html: str) -> BeautifulSoup:
    """Parse a document or fragment. Entities are decoded by the parser."""
    return BeautifulSoup(html, "html.parser")


def classes_of(tag: Tag) -> list[str]:
    value = tag.get("class") or []
    return value.split() if isinstance(value, str) else list(value)


def css_declarations(style: str) -> list[tuple[str, str]]:
    out = []
    for decl in style.split(";"):
        if not decl.strip():
            continue
        prop, sep, value = decl.partition(":")
        out.append((prop.strip().lower(), value.strip() if sep else ""))
    return out


# ── Checking ─────────────────────────────────────────────────────────────────

def _check_attribute(tag: Tag, name: str, value, out: list[str]) -> None:
    where = f"<{tag.name}>"
    if name not in ALLOWED_ATTRIBUTES.get(tag.name, ()):
        out.append(f"attribute {name!r} on {where}")
        return
    if name == "class":
        for cls in classes_of(tag):
            if cls not in ALLOWED_CLASSES:
                out.append(f"class {cls!r} on {where}")
        return
    if name == "style":
        for prop, css in css_declarations(str(value)):
            if prop not in ALLOWED_CSS_PROPERTIES:
                out.append(f"CSS property {prop!r} on {where}")
            elif not _CSS_VALUE_RE.match(css):
                out.append(f"CSS value {prop}: {css!r} on {where}")
        return
    rule = _ATTRIBUTE_RULES.get(name)
    if rule is not None and not rule.match(str(value)):
        out.append(f"value {name}={str(value)!r} on {where}")


def violations(nodes: Iterable, *, in_svg: bool = False) -> list[str]:
    """Every allowlist violation in `nodes` (tags, strings and comments, as
    the parser produced them) and their descendants. Empty means clean."""
    out: list[str] = []
    for node in nodes:
        _check_node(node, in_svg, out)
    return out


def _check_node(node, in_svg: bool, out: list[str]) -> None:
    if isinstance(node, Comment):
        return                                  # dropped on output
    if isinstance(node, NavigableString):
        if type(node) is not NavigableString:   # doctype, CDATA, PI, declaration
            out.append(f"markup {type(node).__name__}")
        return
    if not isinstance(node, Tag):
        out.append(f"node {type(node).__name__}")
        return
    name = node.name
    if name not in ALLOWED_TAGS:
        out.append(f"tag <{name}>")
        return                                  # one line per foreign subtree
    if in_svg and name not in SVG_CHILDREN:
        out.append(f"tag <{name}> inside <svg>")
    if not in_svg and name in SVG_CHILDREN:
        out.append(f"tag <{name}> outside <svg>")
    for attr, value in node.attrs.items():
        _check_attribute(node, attr, value, out)
    children = list(node.children)
    if name in VOID_TAGS or name in EMPTY_SVG_TAGS:
        if any(not (isinstance(c, NavigableString) and not c.strip()) for c in children
               if not isinstance(c, Comment)):
            out.append(f"content inside <{name}>")
        return
    if name == "text":
        for child in children:
            if isinstance(child, Tag):
                out.append(f"tag <{child.name}> inside <text>")
            elif not isinstance(child, Comment) and type(child) is not NavigableString:
                out.append(f"markup {type(child).__name__} inside <text>")
        return
    for child in children:
        _check_node(child, in_svg or name == "svg", out)


# ── Serialising ──────────────────────────────────────────────────────────────

def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(value: str) -> str:
    return _escape_text(value).replace('"', "&quot;")


def _attr_value(tag: Tag, name: str, value) -> str:
    if name == "class":
        return " ".join(classes_of(tag))
    return str(value)


def _without_comments(nodes: Iterable) -> list:
    """`nodes` with comments dropped and the strings either side of a dropped
    comment joined, whitespace-only runs folded the way the parser folds them
    (a newline if there is one, else a space). So sanitising stored HTML again
    gives the same HTML back."""
    out: list = []
    for node in nodes:
        if isinstance(node, Comment):
            continue
        if isinstance(node, NavigableString) and out and isinstance(out[-1], str):
            joined = out[-1] + str(node)
            out[-1] = joined if joined.strip() else ("\n" if "\n" in joined else " ")
        elif isinstance(node, NavigableString):
            out.append(str(node))
        else:
            out.append(node)
    return out


def serialize(nodes: Iterable) -> str:
    """Write `nodes` back as HTML. Assumes `violations(nodes)` is empty: this
    writes what the parse tree holds, comments dropped, entities as
    characters, attribute values quoted and escaped."""
    parts: list[str] = []
    for node in _without_comments(nodes):
        _write(node, parts)
    return "".join(parts)


def _write(node, parts: list[str]) -> None:
    if isinstance(node, str):
        parts.append(_escape_text(node))
        return
    attrs = "".join(
        f' {_ATTRIBUTE_CASE.get(name, name)}="{_escape_attr(_attr_value(node, name, value))}"'
        for name, value in node.attrs.items()
    )
    if node.name in VOID_TAGS:
        parts.append(f"<{node.name}{attrs}>")
        return
    if node.name in EMPTY_SVG_TAGS:
        parts.append(f"<{node.name}{attrs}/>")
        return
    parts.append(f"<{node.name}{attrs}>")
    for child in _without_comments(node.children):
        _write(child, parts)
    parts.append(f"</{node.name}>")


def sanitize(nodes: Iterable) -> str:
    """Check, then serialise. Raises `ReportRejected` on any violation."""
    nodes = list(nodes)
    found = violations(nodes)
    if found:
        raise ReportRejected(found)
    return serialize(nodes)


def sanitize_html(fragment: str) -> str:
    """`sanitize` for an HTML string (tests, and any caller holding a fragment)."""
    return sanitize(parse(fragment).contents)


# ── Reading helpers ──────────────────────────────────────────────────────────

def text_of(node) -> str:
    """The visible text of a node, whitespace collapsed, comments ignored."""
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return " ".join(str(node).split())
    return " ".join(node.get_text(" ").split())


def word_count(nodes: Iterable) -> int:
    """Words of prose in `nodes`. Chart labels (SVG text) are not prose."""
    total = 0
    for node in nodes:
        if isinstance(node, Comment):
            continue
        if isinstance(node, NavigableString):
            total += len(str(node).split())
        elif isinstance(node, Tag) and node.name != "svg":
            total += word_count(node.children)
    return total


class _TagBalance(HTMLParser):
    """Start and end tags as written, before any parser repairs them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.opened: Counter = Counter()
        self.closed: Counter = Counter()

    def handle_starttag(self, tag, attrs):
        if tag not in VOID_TAGS:
            self.opened[tag] += 1

    def handle_startendtag(self, tag, attrs):
        pass                                    # <rect/> opens and closes

    def handle_endtag(self, tag):
        self.closed[tag] += 1


def unclosed_tags(raw_html: str) -> dict[str, int]:
    """Tags written open more often than closed in a raw fragment, e.g.
    `{"strong": 1}`. The parser closes them where their parent ends; this is
    how the loader knows it happened."""
    counter = _TagBalance()
    counter.feed(raw_html)
    counter.close()
    return {tag: n - counter.closed[tag] for tag, n in counter.opened.items()
            if n > counter.closed[tag]}
