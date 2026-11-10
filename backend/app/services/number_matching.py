"""Which typed numbers come from our data, what they would become, and how far
they have drifted (LIVE-4 / SCRUM-211, S2).

Takes S1's inventory and gives every item one status:

    match            from our data, and the typed number still equals today's
    drifted          from our data, and today's value differs — the text is wrong
    no_data          from our data, but the series / recipe has no value now
    ambiguous        the sentence names several series: a person picks one
    manual           looks convertible but nothing ties it to one source
    not_convertible  tied to a series, but our data cannot say it (an index
                     level is not a price)
    not_ours         a number we do not hold — it stays written text

Rules, by kind (all PROPOSED, for review with Alexis and Laurent):

* **cost_bar** — matched to the recipe lines of the report's own product lines,
  by the type code in its label ("Iron (FE-MB)"), else by the exact line name
  ("Supplier margin"). Today's range is the min–max weight of those lines; it
  matches when both ends round to the typed ones. (S3 decides what the range
  should mean; this measures the gap with the plainest reading.)
* **pct_move** — a sentence naming exactly one series: `{{index:KEY|change12m}}`.
  "fell / down / lower" makes the typed number negative. Matches within one
  percentage point. Confidence is high only when the sentence says the
  period is a year ("y/y", "year-on-year", "12 months", "over the year").
* **date** with an as-of cue ("as of", "data to", "latest") and one series:
  `{{index:KEY|asof}}`; matches when it is the same month. Other dates are
  events: not ours.
* **price** with one series: a rebased index (`value_kind = index_level`,
  Jan 2023 = 100) cannot give a price, so `not_convertible`; otherwise
  `{{index:KEY|last}}`.
* **percent** — shares, yields, purities: not ours.
"""
from __future__ import annotations

import calendar
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.models.index_data import CommodityIndex
from app.services.live_resolver import report_recipe_weights, resolve
from app.services.live_tokens import MISSING, parse_token
from app.services.number_inventory import (
    COST_BAR, DATE, PCT_MOVE, PERCENT, PRICE, NumberItem,
)

MATCH = "match"
DRIFTED = "drifted"
NO_DATA = "no_data"
AMBIGUOUS = "ambiguous"
MANUAL = "manual"
NOT_CONVERTIBLE = "not_convertible"
NOT_OURS = "not_ours"
STATUSES = (DRIFTED, MATCH, NO_DATA, AMBIGUOUS, MANUAL, NOT_CONVERTIBLE, NOT_OURS)

# PROPOSED: how close a typed % move must be to today's to count as a match.
MOVE_TOLERANCE_PP = 1.0

_DOWN = {"fell", "falling", "fall", "down", "decrease", "decreased", "decreasing", "decline",
         "declined", "dropped", "lower", "slipped", "eased"}
_YEARLY = re.compile(r"\b(y/y|yoy|year[- ]on[- ]year|12[- ]month|twelve months|over the (?:past )?year|"
                     r"annual(?:ly)?|from a year (?:ago|earlier))\b", re.IGNORECASE)
_STRICT_AS_OF = re.compile(r"\b(as of|as at|data (?:to|through|until)|latest (?:data|reading|print|value))\b",
                           re.IGNORECASE)
_MONTH_NUM = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


@dataclass
class Match:
    item: NumberItem
    status: str
    why: str
    token: str | None = None        # the proposed token (cost bars: a description until S3)
    typed: str | None = None        # the typed value, normalised
    today: str | None = None        # today's value from our data
    confidence: str | None = None   # high | low, where it applies


def _num(text: str) -> float | None:
    m = re.search(r"\d{1,3}(?:[.,]\d+)?", text)
    return float(m.group(0).replace(",", ".")) if m else None


def _norm_label(label: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*", " ", label).strip().lower()


def _typed_month(text: str) -> tuple[int, int] | None:
    """The month a typed date stands for; a quarter or half is its last month
    ("Q2 2026" → June 2026), the month its data runs to."""
    q = re.match(r"([QH])([1-4])\s?(20\d\d)", text)
    if q:
        n = int(q.group(2))
        month = n * 3 if q.group(1) == "Q" else n * 6
        return (int(q.group(3)), month) if month <= 12 else None
    m = re.match(r"([A-Za-z]{3})[a-z]*\.?\s+(20\d\d)", text)
    if not m or m.group(1).lower() not in _MONTH_NUM:
        return None
    return int(m.group(2)), _MONTH_NUM[m.group(1).lower()]


def _recipe_weights(db: Session) -> tuple[dict, dict]:
    """The same per-report recipe weights the live bar reads (one rule)."""
    return report_recipe_weights(db)


def _range_text(lo: float, hi: float) -> str:
    return f"{lo:g}%" if round(lo) == round(hi) else f"{lo:g}–{hi:g}%"


def _cost_bar(item: NumberItem, by_code: dict, by_name: dict) -> Match:
    code, label = item.extra.get("code"), item.extra.get("label", "")
    lo, hi = item.extra.get("lo"), item.extra.get("hi")
    weights, how = None, None
    if code and by_code.get(item.doc, {}).get(code):
        weights, how = by_code[item.doc][code], f"type code {code}"
    elif by_name.get(item.doc, {}).get(_norm_label(label)):
        weights, how = by_name[item.doc][_norm_label(label)], "line name"
    if weights is None:
        why = ("the report's product lines have no platform recipes" if not by_name.get(item.doc)
               else "no recipe line with this code or name")
        return Match(item, MANUAL, why, typed=item.text)
    now_lo, now_hi = min(weights), max(weights)
    token = f"live cost bar · {code or _norm_label(label)}"
    if lo is None:
        return Match(item, MANUAL, "typed weight has no number", token=token, typed=item.text)
    same = round(lo) == round(now_lo) and round(hi) == round(now_hi)
    return Match(item, MATCH if same else DRIFTED,
                 f"{len(weights)} recipe line(s) by {how}", token=token,
                 typed=_range_text(lo, hi), today=_range_text(now_lo, now_hi), confidence="high")


def match_items(db: Session, items: list[NumberItem], *, as_of: date | None = None) -> list[Match]:
    """A status for every item, with today's value where it comes from our data."""
    by_code, by_name = _recipe_weights(db)
    keys = dict(db.query(CommodityIndex.id, CommodityIndex.commodity_key)
                .filter(CommodityIndex.commodity_key.isnot(None)))
    levels = {cid for (cid,) in db.query(CommodityIndex.id)
              .filter(CommodityIndex.value_kind == "index_level")}

    # Index tokens to resolve in one batch.
    wanted = set()
    for it in items:
        if len(it.series_ids) == 1:
            key = keys[next(iter(it.series_ids))]
            if it.kind == PCT_MOVE:
                wanted.add(f"{{{{index:{key}|change12m}}}}")
            elif it.kind == DATE:
                wanted.add(f"{{{{index:{key}|asof}}}}")
            elif it.kind == PRICE:
                wanted.add(f"{{{{index:{key}|last}}}}")
    parsed = {raw: parse_token(raw) for raw in wanted}
    resolved = resolve(db, parsed.values(), as_of=as_of) if parsed else {}

    out: list[Match] = []
    for it in items:
        if it.kind == COST_BAR:
            out.append(_cost_bar(it, by_code, by_name))
            continue
        if it.kind == PERCENT:
            out.append(Match(it, NOT_OURS, "a share, yield or other figure we do not hold"))
            continue
        if it.kind == DATE and not _STRICT_AS_OF.search(it.context):
            out.append(Match(it, NOT_OURS, "a date of an event, not of our data"))
            continue
        if not it.series_ids:
            out.append(Match(it, MANUAL if it.kind != DATE else NOT_OURS,
                             "the sentence names no series we hold"))
            continue
        if len(it.series_ids) > 1:
            out.append(Match(it, AMBIGUOUS, f"the sentence names {len(it.series_ids)} series"))
            continue

        cid = next(iter(it.series_ids))
        key = keys[cid]
        if it.kind == PRICE and cid in levels:
            out.append(Match(it, NOT_CONVERTIBLE,
                             f"{key} is an index level (Jan 2023 = 100), not a price", typed=it.text))
            continue

        measure = {PCT_MOVE: "change12m", DATE: "asof", PRICE: "last"}[it.kind]
        token = f"{{{{index:{key}|{measure}}}}}"
        r = resolved.get(parsed[token])
        if r is None or r.status == MISSING:
            out.append(Match(it, NO_DATA, r.reason if r else "not resolved", token=token, typed=it.text))
            continue

        if it.kind == PCT_MOVE:
            typed = _num(it.text)
            if typed is None:
                out.append(Match(it, MANUAL, "no number in the move", token=token))
                continue
            sign = -1 if it.extra.get("verb") in _DOWN or it.text.startswith("-") else 1
            typed = sign * abs(typed)
            same = abs(typed - r.value) <= MOVE_TOLERANCE_PP
            out.append(Match(it, MATCH if same else DRIFTED,
                             "one series named; % change vs a year earlier", token=token,
                             typed=f"{typed:+.1f}%", today=f"{r.value:+.1f}%",
                             confidence="high" if _YEARLY.search(it.context) else "low"))
        elif it.kind == DATE:
            typed = _typed_month(it.text)
            today = (r.year, r.month)
            out.append(Match(it, MATCH if typed == today else DRIFTED,
                             "as-of date next to one series", token=token, typed=it.text,
                             today=f"{calendar.month_abbr[r.month]} {r.year}",
                             confidence="high" if typed else "low"))
        else:
            out.append(Match(it, DRIFTED, "price of a series holding real prices", token=token,
                             typed=it.text, today=f"{r.value:g}", confidence="low"))
    return out


def summarise(matches: list[Match]) -> dict[str, Counter]:
    """kind -> status -> count."""
    out: dict[str, Counter] = defaultdict(Counter)
    for m in matches:
        out[f"{m.item.source}:{m.item.kind}"][m.status] += 1
    return out
