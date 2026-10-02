"""Scrum 32 — suggest a cost structure for a product nobody has decomposed.

Three things this does NOT do, each because the alternative is worse than
having no feature:

1. It never writes a cost model. The output is a draft; only an explicit
   promote creates a FormulaVersion, and that version is tagged `ai_draft` so
   the estimate caveat survives the save.
2. It never invents an index. A suggested feed name that matches nothing we
   track is flagged `index_resolved = False` and kept — a language model
   producing a plausible-looking feed name is the expected failure here, and
   quietly dropping the line would leave a recipe that no longer sums to 100
   with nothing to explain why.
3. It never degrades to silence. In production `llm_enabled` is False and
   `ollama_generate` returns None on a cache miss, so the caller is told the
   modeler is unavailable rather than being handed an empty draft that looks
   like a considered answer.
"""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.models.index_data import CommodityIndex

SYSTEM_PROMPT = (
    "You are a procurement cost analyst. Given a chemical or industrial product, "
    "propose the cost structure a buyer should expect: the raw materials, energy, "
    "conversion and margin, each as a percentage of the delivered price. "
    "Reply with JSON only, no prose around it."
)

# The shape is spelled out in the prompt AND validated on the way back, because
# an LLM that returns almost-the-right-shape is the normal case, not the
# exception.
RESPONSE_SHAPE = """{
  "rationale": "one or two sentences on how this product is made",
  "lines": [
    {"label": "...", "weight_pct": 30.0, "type": "index"|"fixed",
     "index": "a commodity name, or null for a fixed line",
     "confidence": "high"|"medium"|"low",
     "why": "one sentence"}
  ]
}"""

MIN_LINES = 2
MAX_LINES = 20


class ModelerUnavailable(Exception):
    """The LLM could not be reached or is disabled."""


class ModelerUnparseable(Exception):
    """A reply came back but was not the agreed shape. Carries the raw text."""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


def build_prompt(product_name: str, sector: str | None, rough_price, currency, unit, region) -> str:
    price_line = (
        f"The buyer pays roughly {rough_price} {currency or ''} per {unit or 'unit'}."
        if rough_price else "The buyer has not given a price."
    )
    return (
        f"Product: {product_name}\n"
        f"Sector: {sector or 'unspecified'}\n"
        f"Region: {region or 'unspecified'}\n"
        f"{price_line}\n\n"
        "Break the delivered cost into components whose weight_pct values sum to 100. "
        "Include conversion and margin as fixed lines. Name a real, publicly quoted "
        "commodity for each index line; if no public index exists for a component, "
        "make it a fixed line rather than inventing a feed name.\n\n"
        f"Reply with exactly this JSON shape:\n{RESPONSE_SHAPE}"
    )


def parse_response(raw: str) -> dict:
    """Pull the agreed JSON out of whatever came back.

    Tolerant about the wrapper (code fences, a sentence before the object) and
    strict about the contents. Being strict here is what keeps a half-understood
    reply from becoming a confident-looking draft.
    """
    if not raw or not raw.strip():
        raise ModelerUnparseable("The model returned nothing", raw or "")

    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]

    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ModelerUnparseable(f"The model's reply was not valid JSON: {exc}", raw)

    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        raise ModelerUnparseable("The model's reply had no 'lines' list", raw)

    lines = []
    for item in data["lines"]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:64]
        try:
            weight = float(item.get("weight_pct"))
        except (TypeError, ValueError):
            continue
        # A negative or absurd weight is a misunderstanding, not a cost line.
        if not label or weight <= 0 or weight > 100:
            continue
        suggested = item.get("index")
        ctype = "fixed" if (item.get("type") == "fixed" or not suggested) else "index"
        confidence = item.get("confidence")
        lines.append({
            "label": label,
            "weight_pct": round(weight, 4),
            "component_type": ctype,
            "suggested_index": (str(suggested).strip()[:128] if suggested else None),
            "confidence": confidence if confidence in ("high", "medium", "low") else None,
            "rationale": (str(item.get("why")).strip()[:500] if item.get("why") else None),
        })

    if len(lines) < MIN_LINES:
        raise ModelerUnparseable(
            f"Only {len(lines)} usable component(s) came back — not a cost structure", raw)
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES]

    return {
        "rationale": (str(data.get("rationale")).strip()[:2000] if data.get("rationale") else None),
        "lines": lines,
    }


def resolve_indexes(db: Session, lines: list[dict]) -> list[dict]:
    """Bind each suggested feed name to a real commodity, or flag that it did not.

    Case-insensitive exact match first, then a contains match, because a model
    says "Caustic soda" where the library says "Caustic Soda (EU)". Anything
    looser would bind a line to the wrong series, which is worse than telling
    the reviewer to pick one.
    """
    wanted = {l["suggested_index"].lower() for l in lines if l.get("suggested_index")}
    if not wanted:
        for line in lines:
            line["commodity_id"] = None
            line["index_resolved"] = False
        return lines

    catalog = db.query(CommodityIndex.id, CommodityIndex.name).all()
    exact = {name.lower(): cid for cid, name in catalog}

    for line in lines:
        suggested = line.get("suggested_index")
        if not suggested or line["component_type"] == "fixed":
            line["commodity_id"] = None
            line["index_resolved"] = False
            continue
        key = suggested.lower()
        cid = exact.get(key)
        if cid is None:
            hits = [c for c, n in catalog if key in n.lower() or n.lower() in key]
            # Exactly one candidate, or none. An ambiguous name is left for the
            # reviewer rather than resolved to whichever sorted first.
            cid = hits[0] if len(hits) == 1 else None
        line["commodity_id"] = cid
        line["index_resolved"] = cid is not None
    return lines


def weight_total(lines) -> float:
    return round(sum(float(l.weight_pct if hasattr(l, "weight_pct") else l["weight_pct"])
                     for l in lines), 4)


def promotion_blockers(lines) -> list[str]:
    """Why this draft cannot become a formula yet. Empty means it can.

    Both rules exist because the failure they prevent is silent: a recipe that
    does not close at 100 prices wrong without looking wrong, and an unresolved
    index line would be saved as a broken link that rides flat.
    """
    problems = []
    total = weight_total(lines)
    if abs(total - 100.0) > 0.05:
        problems.append(f"Weights sum to {total:g}%, not 100%")
    unresolved = [
        l for l in lines
        if (l.component_type if hasattr(l, "component_type") else l["component_type"]) == "index"
        and not (l.index_resolved if hasattr(l, "index_resolved") else l["index_resolved"])
    ]
    if unresolved:
        labels = ", ".join(
            (l.label if hasattr(l, "label") else l["label"]) for l in unresolved[:4])
        problems.append(
            f"{len(unresolved)} index line(s) do not match a tracked index ({labels})"
        )
    return problems
