"""Mention audit: do reports and intelligence point at indexes? (Jira 208)

Reports (`market_report_sections`) and intelligence blocks (`editorial_blocks`)
are stored as prose with no index column, so nothing in the schema says which
series a sentence is about. This reads that prose and reports, per section or
block:

    commodity mentions   a series' own name or one of its card names, matched
                         as a whole word, with the series it could mean
    product codes        a catalogue recipe code (e.g. F27-PEA-EU); a recipe's
                         lines point at series, so this is an INDIRECT link
    typed figures        percentages and prices written into the text
    anchored figures     sentences holding both a commodity mention and a
                         typed figure: the numbers that go stale when the
                         series moves, and the candidates for live values
                         (Jira 210 / LIVE-4)

Matching is deliberately literal. The vocabulary comes only from the data
(series names and index-card names, parentheticals dropped, split on " / "),
never from a hand-written synonym list, so every match can be traced to a
name the library already uses. A term naming several series (one per region)
is reported with all of them: a mention never auto-picks a region.

Read only. It turns nothing into a link; it measures how far there is to go.
"""
from __future__ import annotations

import html as html_lib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.editorial import EditorialBlock, EditorialBlockVersion
from app.models.formula_template import FormulaTemplate
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexCard
from app.models.market_report import MarketReportSection

# Shorter terms match inside too many unrelated words to mean anything.
MIN_TERM_LENGTH = 4

# Series names that are also everyday English words, so a literal match says
# nothing ("lead times", "to lead"). Left out of the vocabulary rather than
# guessed at, and reported as excluded so the gap stays visible.
EXCLUDED_TERMS = frozenset({"lead"})

_TAG = re.compile(r"<[^>]+>")
_PARENS = re.compile(r"\s*\([^)]*\)")
_SENTENCE = re.compile(r"(?<=[.!?;])\s+")
_PERCENT = re.compile(r"[-+]?\d+(?:[.,]\d+)?\s?%")
_PRICE = re.compile(
    r"(?:(?:USD|EUR|GBP|US\$|[$€£])\s?\d[\d,.]*)|(?:\d[\d,.]*\s?(?:USD|EUR|GBP|[$€£])(?:\s?/\s?\w+)?)",
    re.IGNORECASE,
)
_CODE = re.compile(r"\b[A-Z0-9]{2,6}(?:-[A-Z0-9]{2,10}){1,3}\b")


@dataclass
class TextAudit:
    source: str                    # report | editorial
    ref: str                       # report slug + section, or block type + subject
    commodity_terms: list[str] = field(default_factory=list)
    series_ids: list[int] = field(default_factory=list)
    product_codes: list[str] = field(default_factory=list)
    typed_figures: int = 0
    anchored_figures: int = 0      # sentences with a commodity term AND a typed figure
    already_linked: bool = False   # editorial block that already carries a commodity_id


def _plain(text: str | None) -> str:
    return html_lib.unescape(_TAG.sub(" ", text or ""))


def _clean_name(name: str) -> str:
    return _PARENS.sub("", name).split(" — ")[0].strip()


def build_vocabulary(db: Session) -> dict[str, set[int]]:
    """term (lower case) -> the series it can mean. Content series only
    (`commodity_key` set): those are the ones recipes and reports belong to."""
    vocab: dict[str, set[int]] = defaultdict(set)
    content = {s.id: s for s in db.query(CommodityIndex).filter(CommodityIndex.commodity_key.isnot(None))}
    names = [(s.id, s.name) for s in content.values()]
    names += [
        (cid, name) for cid, name in db.query(IndexCard.commodity_id, IndexCard.name)
        if cid in content and name
    ]
    for cid, name in names:
        for part in _clean_name(name).split(" / "):
            term = part.strip().lower()
            if (len(term) >= MIN_TERM_LENGTH and not term.endswith(" ppi")
                    and term not in EXCLUDED_TERMS):
                vocab[term].add(cid)
    return dict(vocab)


def _matcher(vocab: dict[str, set[int]]) -> re.Pattern | None:
    if not vocab:
        return None
    # Longest first, so "caustic soda" wins over a shorter overlapping term.
    terms = sorted(vocab, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b", re.IGNORECASE)


def audit_text(text: str, *, source: str, ref: str, vocab: dict[str, set[int]],
               matcher: re.Pattern | None, codes: set[str]) -> TextAudit:
    plain = _plain(text)
    out = TextAudit(source=source, ref=ref)
    found_terms: set[str] = set()
    for sentence in _SENTENCE.split(plain):
        terms = {m.group(1).lower() for m in matcher.finditer(sentence)} if matcher else set()
        figures = len(_PERCENT.findall(sentence)) + len(_PRICE.findall(sentence))
        found_terms |= terms
        out.typed_figures += figures
        if terms and figures:
            out.anchored_figures += 1
    out.commodity_terms = sorted(found_terms)
    out.series_ids = sorted({cid for t in found_terms for cid in vocab.get(t, ())})
    out.product_codes = sorted({c for c in _CODE.findall(plain) if c in codes})
    return out


@dataclass
class MentionSummary:
    vocabulary_terms: int
    reports: dict
    editorial: dict
    terms: list[dict]              # per term: mentions, the series it can mean


def _roll_up(rows: list[TextAudit]) -> dict:
    return {
        "units": len(rows),
        "with_commodity_mention": sum(1 for r in rows if r.commodity_terms),
        "with_product_code": sum(1 for r in rows if r.product_codes),
        "with_neither": sum(1 for r in rows if not r.commodity_terms and not r.product_codes),
        "with_typed_figures": sum(1 for r in rows if r.typed_figures),
        "anchored_figures": sum(r.anchored_figures for r in rows),
        "already_linked": sum(1 for r in rows if r.already_linked),
    }


def audit_mentions(db: Session) -> tuple[list[TextAudit], MentionSummary]:
    vocab = build_vocabulary(db)
    matcher = _matcher(vocab)
    codes = {c for (c,) in db.query(FormulaTemplate.code).filter(
        FormulaTemplate.team_id.is_(None), FormulaTemplate.code.isnot(None))}

    rows: list[TextAudit] = []
    for slug, heading, body in db.query(
        MarketReportSection.slug, MarketReportSection.heading, MarketReportSection.html,
    ).order_by(MarketReportSection.slug, MarketReportSection.ordinal):
        rows.append(audit_text(body, source="report", ref=f"{slug} · {heading or ''}".strip(),
                               vocab=vocab, matcher=matcher, codes=codes))

    blocks = (
        db.query(EditorialBlock, EditorialBlockVersion.body_text)
        .join(EditorialBlockVersion, EditorialBlockVersion.id == EditorialBlock.current_version_id)
        .filter(EditorialBlock.team_id.is_(None))
    )
    for block, body in blocks:
        r = audit_text(body, source="editorial",
                       ref=f"{block.block_type} · {block.subject_type}:{block.subject_code}",
                       vocab=vocab, matcher=matcher, codes=codes)
        r.already_linked = block.commodity_id is not None
        rows.append(r)

    mentions = Counter(t for r in rows for t in r.commodity_terms)
    keys = {s.id: s.commodity_key for s in db.query(CommodityIndex).filter(
        CommodityIndex.commodity_key.isnot(None))}
    terms = [
        {"term": t, "units": n, "series": sorted(keys[c] for c in vocab[t] if c in keys),
         "single_series": len(vocab[t]) == 1}
        for t, n in mentions.most_common()
    ]
    return rows, MentionSummary(
        vocabulary_terms=len(vocab),
        reports=_roll_up([r for r in rows if r.source == "report"]),
        editorial=_roll_up([r for r in rows if r.source == "editorial"]),
        terms=terms,
    )
