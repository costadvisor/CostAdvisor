"""Every number typed into the reports and blocks, classified (LIVE-4 / SCRUM-211, S1).

LIVE-4 converts the typed numbers that come from data we hold into live
tokens (Jira 210). Before anything is converted, this lists them all, so the
size of the job and the drift are measured, not guessed.

Kinds:

    cost_bar        a bar of the process section's cost structure:
                    `<div class="cost-bar">` with a label ("Iron (FE-MB)")
                    and a weight range ("30–49%"). Read from the structure,
                    so its numbers are not counted again as percentages.
    pct_move        "rose 12%", "down 8.5%" — a move in a sentence
    price           "$450/t", "€1,200/t", "820 USD/t"
    date            "June 2026", "Q2 2026", "H1 2026"; `as_of_cue` marks the
                    ones that say how fresh data is ("as of", "data to", ...)
    percent         any other percentage (shares, yields, purities...)

Each item carries its sentence and the content series that sentence names
(208's mention vocabulary), which S2 uses to match it to our data.

Read only. Reports are read from `market_report_sections` (the panels are the
same text cut differently), blocks from platform blocks' current versions.
The items quote licensed text: anything written out goes to the git-ignored
`docs/` folder only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.models.editorial import EditorialBlock, EditorialBlockVersion
from app.models.market_report import MarketReportSection
from app.services.mention_audit import _matcher, build_vocabulary

COST_BAR = "cost_bar"
PCT_MOVE = "pct_move"
PRICE = "price"
DATE = "date"
PERCENT = "percent"
KINDS = (COST_BAR, PCT_MOVE, PRICE, DATE, PERCENT)

_NUM = r"\d{1,3}(?:[.,]\d+)?"
_MOVE_RE = re.compile(
    rf"\b(rose|rising|rise|up|increased?|increasing|gained?|climbed|higher|jumped|surged|"
    rf"fell|falling|fall|down|decreased?|decreasing|declined?|dropped|lower|slipped|eased)\b"
    rf"[^.;%]{{0,40}}?(?<![\d.])([+-]?{_NUM})\s?%", re.IGNORECASE)
_PRICE_RE = re.compile(
    r"(?:(?:US)?[$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|bn))?(?:\s?[–-]\s?[$€£¥]?\d[\d,]*(?:\.\d+)?)?"
    r"(?:\s?/\s?(?:t|mt|kg|lb|bbl|MWh|MMBtu|tonne|ton))?)"
    r"|(?:\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|CNY|INR)\s?/\s?(?:t|mt|kg|lb|bbl|MWh|MMBtu|tonne|ton))",
    re.IGNORECASE)
_MONTHS = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_DATE_RE = re.compile(rf"\b{_MONTHS}\.?\s+20\d\d\b|\b[QH][1-4]\s?20\d\d\b")
_PERCENT_RE = re.compile(rf"(?<![\d.]){_NUM}(?:\s?[–-]\s?{_NUM})?\s?%")
_AS_OF_CUE = re.compile(r"\b(as of|as at|data (?:to|through|until)|latest|to date|through|until|"
                        r"most recent|last (?:reading|print|data))\b", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"(])")
_CODE_RE = re.compile(r"\(([A-Z0-9][A-Z0-9-]{1,30})\)")


@dataclass
class NumberItem:
    source: str                     # report | block
    doc: str                        # report slug, or "subject_code/block_type"
    part: str                       # section id, or "body"
    kind: str
    text: str                       # the number as typed
    context: str                    # its sentence (for a cost bar: "label · weight")
    series_ids: frozenset[int] = frozenset()   # content series the sentence names
    extra: dict = field(default_factory=dict)  # cost bar: label, code, lo, hi; date: as_of_cue


def _bar_range(weight: str) -> tuple[float | None, float | None]:
    nums = [float(n.replace(",", ".")) for n in re.findall(_NUM, weight)]
    if not nums:
        return None, None
    return min(nums), max(nums)


def _sentences(text: str) -> list[str]:
    text = " ".join(text.split())
    return [s for s in _SENTENCE_RE.split(text) if s]


def _classify(sentence: str, base: dict, matcher, vocab) -> list[NumberItem]:
    series = frozenset()
    if matcher is not None:
        ids: set[int] = set()
        for m in matcher.finditer(sentence):
            ids |= vocab[m.group(1).lower()]
        series = frozenset(ids)
    items: list[NumberItem] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    def add(kind: str, m: re.Match, text: str | None = None, extra: dict | None = None) -> None:
        taken.append(m.span())
        items.append(NumberItem(kind=kind, text=(text or m.group(0)).strip(), context=sentence,
                                series_ids=series, extra=extra or {}, **base))

    for m in _MOVE_RE.finditer(sentence):
        pct = re.search(rf"[+-]?{_NUM}\s?%$", m.group(0))
        add(PCT_MOVE, m, text=pct.group(0) if pct else m.group(0),
            extra={"verb": m.group(1).lower()})
    for m in _PRICE_RE.finditer(sentence):
        if free(*m.span()):
            add(PRICE, m)
    cue = bool(_AS_OF_CUE.search(sentence))
    for m in _DATE_RE.finditer(sentence):
        if free(*m.span()):
            add(DATE, m, extra={"as_of_cue": cue})
    for m in _PERCENT_RE.finditer(sentence):
        if free(*m.span()):
            add(PERCENT, m)
    return items


def scan_report_section(slug: str, section_id: str, html: str, matcher, vocab) -> list[NumberItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[NumberItem] = []
    base = {"source": "report", "doc": slug, "part": section_id}
    for bar in soup.find_all(class_="cost-bar"):
        label_el, weight_el = bar.find(class_="label"), bar.find(class_="weight")
        label = label_el.get_text(" ", strip=True) if label_el else ""
        weight = weight_el.get_text(" ", strip=True) if weight_el else ""
        code = _CODE_RE.search(label)
        lo, hi = _bar_range(weight)
        items.append(NumberItem(kind=COST_BAR, text=weight, context=f"{label} · {weight}",
                                extra={"label": label, "code": code.group(1) if code else None,
                                       "lo": lo, "hi": hi}, **base))
        bar.decompose()            # its numbers are not counted again below
    for sentence in _sentences(soup.get_text(" ")):
        items += _classify(sentence, base, matcher, vocab)
    return items


def _strings(value) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def inventory(db: Session, *, include_blocks: bool = True) -> list[NumberItem]:
    """Every typed number in the reports (and platform blocks), classified."""
    vocab = build_vocabulary(db)
    matcher = _matcher(vocab)
    items: list[NumberItem] = []
    for slug, section_id, html in (
        db.query(MarketReportSection.slug, MarketReportSection.section_id, MarketReportSection.html)
        .order_by(MarketReportSection.slug, MarketReportSection.ordinal)
    ):
        items += scan_report_section(slug, section_id, html, matcher, vocab)
    if include_blocks:
        for code, btype, fmt, body_text, body_json in (
            db.query(EditorialBlock.subject_code, EditorialBlock.block_type,
                     EditorialBlockVersion.body_format, EditorialBlockVersion.body_text,
                     EditorialBlockVersion.body_json)
            .join(EditorialBlockVersion, EditorialBlockVersion.id == EditorialBlock.current_version_id)
            .filter(EditorialBlock.team_id.is_(None))
        ):
            base = {"source": "block", "doc": f"{code}/{btype}", "part": "body"}
            for s in _strings(body_json if fmt == "json" else body_text):
                for sentence in _sentences(s):
                    items += _classify(sentence, base, matcher, vocab)
    return items
