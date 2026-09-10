"""Quote & price-list extraction (Scrum 31b).

Deterministic regex/keyword extraction against known, closed vocabularies
(Incoterms, currencies, units) — no LLM. A core extraction path can't
silently degrade to nothing the way an Ollama call could (llm_enabled
defaults False in production), and regex heuristics are fully unit-testable
without a live model.

Two extraction paths:
- Table mode: a page's extract_tables() output with a recognizable price
  column yields one line per row — this is what makes a multi-product/
  multi-tier document produce multiple lines. Every field here is "labeled"
  (it came from an explicit column header), so confidence is uniformly high.
- Full-text fallback: no usable table anywhere -> the whole document is one
  line, fields found via regex over the page text, confidence tiered by
  whether an explicit label ("Incoterm:", "Price:") was present.

A field entirely absent from a line's `fields` dict means "not found" — it
is never defaulted or fabricated (Scrum 31b AC3). Dates are stored as ISO
strings (`value`) since JSONB can't hold Python date objects directly.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta

import pdfplumber

from app.constants.incoterms import (
    DEPRECATED_INCOTERMS,
    INCOTERMS_2020,
    is_valid as incoterm_is_valid,
    normalize as incoterm_normalize,
)
CONF_LABELED = 0.9      # an explicit table column header or "Field: value" label
CONF_CONTEXTUAL = 0.6   # a strong pattern match with supporting nearby context
CONF_WEAK = 0.35        # a bare pattern match, no label or context

CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
KNOWN_CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CNY", "CHF", "CAD", "AUD", "INR", "BRL",
    "MXN", "SGD", "HKD", "KRW", "SEK", "NOK", "DKK", "ZAR", "AED", "SAR",
}
# Canonical tokens match unit_converter.CONVERSIONS' known units (kg/t/lb).
UNIT_ALIASES = {
    "kg": "kg", "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
    "t": "t", "mt": "t", "tonne": "t", "tonnes": "t", "ton": "t", "tons": "t",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
}

# Column header keyword -> field role. Order matters: a role earlier in this
# dict wins when a header could plausibly match more than one (e.g. "Unit
# Price" contains both "price" and "unit" as substrings — "price" must be
# checked first so it isn't mis-mapped to the unit role).
HEADER_ROLE_KEYWORDS = {
    "price": ("price", "cost", "unit price", "rate"),
    "currency": ("currency", "ccy"),
    "unit": ("unit", "uom", "unit of measure"),
    "incoterm": ("incoterm", "delivery terms", "terms"),
    "volume_tier": ("moq", "qty", "quantity", "volume", "tier"),
    "product_reference": ("product", "item", "sku", "description", "material"),
    "valid_until": ("valid", "validity", "expiry", "expires"),
    "quote_date": ("date",),
}

FIELD_NAMES = (
    "product_reference", "price", "currency", "unit", "volume_tier",
    "incoterm", "named_place", "quote_date", "valid_from", "valid_until",
)


def extract_quote(content: bytes, filename: str) -> dict:
    """Raises ValueError only for structural problems (document can't be
    opened) — same contract as file_parser.py's parsers."""
    pages = _pages_from_pdf(content, filename)
    lines = _lines_from_tables(pages)
    if lines is None:
        lines = [_line_from_full_text(pages)]
    extracted_text = "\n".join(p["text"] for p in pages if p["text"])
    return {"extracted_text": extracted_text, "lines": lines}


def _pages_from_pdf(content: bytes, filename: str) -> list[dict]:
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return [
                {
                    "page": i + 1,
                    "text": page.extract_text() or "",
                    "tables": page.extract_tables() or [],
                }
                for i, page in enumerate(pdf.pages)
            ]
    except Exception as exc:
        raise ValueError(f"Could not read '{filename}' as a PDF: {exc}")


def _mk_field(value, confidence: float, page: int, snippet: str) -> dict:
    return {"value": value, "confidence": confidence, "locator": {"page": page, "snippet": snippet.strip()[:160]}}


# ── Table mode ───────────────────────────────────────────────────────────────

def _norm_header(cell) -> str:
    return re.sub(r"\s+", " ", str(cell or "")).strip().lower()


def _map_header_columns(header: list[str]) -> dict[str, int]:
    col_map: dict[str, int] = {}
    for idx, h in enumerate(header):
        for role, keywords in HEADER_ROLE_KEYWORDS.items():
            if role in col_map:
                continue
            if any(kw in h for kw in keywords):
                col_map[role] = idx
                break
    return col_map


def _parse_number(raw: str) -> float | None:
    m = re.search(r"[\d,]+(?:\.\d+)?", raw)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_currency_token(raw: str) -> str | None:
    token = raw.strip().upper()
    if token in KNOWN_CURRENCIES:
        return token
    for sym, ccy in CURRENCY_SYMBOLS.items():
        if sym in raw:
            return ccy
    return None


def _parse_unit_token(raw: str) -> str | None:
    token = re.sub(r"[^a-zA-Z]", "", raw).lower()
    return UNIT_ALIASES.get(token)


def _parse_incoterm_token(raw: str) -> str | None:
    # Uppercase-only match — see _find_incoterm's comment (deprecated codes
    # like "FOR"/"FOT" collide with common English words).
    for token in re.findall(r"[A-Z]{3,4}", raw):
        code = incoterm_normalize(token)
        if code and incoterm_is_valid(code):
            return code
    return None


def _try_parse_date(text: str) -> date | None:
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    m = re.search(r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b", text)
    if m:
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", fmt).date()
            except ValueError:
                continue
    return None


def _parse_date_token(raw: str) -> str | None:
    d = _try_parse_date(raw)
    return d.isoformat() if d else None


def _named_place_from_text(text: str, incoterm_code: str) -> str | None:
    m = re.search(re.escape(incoterm_code) + r"\s+([A-Za-z][A-Za-z\s]{1,40}?)(?:$|[,;.\n])", text, re.I)
    if not m:
        return None
    place = m.group(1).strip()
    return place or None


_TABLE_VALUE_PARSERS = {
    "price": _parse_number,
    "currency": _parse_currency_token,
    "unit": _parse_unit_token,
    "incoterm": _parse_incoterm_token,
    "volume_tier": lambda raw: raw.strip() or None,
    "product_reference": lambda raw: raw.strip() or None,
    "quote_date": _parse_date_token,
    "valid_until": _parse_date_token,
}


def _line_from_table_row(row: list, col_map: dict[str, int], page_num: int) -> dict:
    fields: dict = {}
    raw_cells: dict[str, str] = {}
    for role, idx in col_map.items():
        if idx >= len(row) or row[idx] is None:
            continue
        raw = str(row[idx]).strip()
        if not raw:
            continue
        raw_cells[role] = raw
        parser = _TABLE_VALUE_PARSERS.get(role)
        value = parser(raw) if parser else raw
        if value is None:
            continue
        fields[role] = _mk_field(value, CONF_LABELED, page_num, raw)
    if "incoterm" in fields:
        place = _named_place_from_text(raw_cells["incoterm"], fields["incoterm"]["value"])
        if place:
            fields["named_place"] = _mk_field(place, CONF_LABELED, page_num, raw_cells["incoterm"])
    return fields


def _lines_from_tables(pages: list[dict]) -> list[dict] | None:
    lines: list[dict] = []
    for page in pages:
        for table in page["tables"]:
            if not table or len(table) < 2:
                continue
            header = [_norm_header(c) for c in table[0]]
            col_map = _map_header_columns(header)
            if "price" not in col_map:
                continue  # not a price table
            for row in table[1:]:
                if not row or not any(row):
                    continue
                fields = _line_from_table_row(row, col_map, page["page"])
                if fields:
                    lines.append(fields)
    return lines or None


# ── Full-text fallback (single-product quote) ───────────────────────────────

_PRICE_RE_LABELED = re.compile(
    r"(?:price|cost|rate)\s*[:\-]?\s*([$€£¥]|\b(?:USD|EUR|GBP|JPY|CNY|CHF|CAD|AUD)\b)?\s*([\d,]+(?:\.\d+)?)",
    re.I,
)
_PRICE_RE_BARE = re.compile(r"([$€£¥]|\b(?:USD|EUR|GBP|JPY|CNY|CHF|CAD|AUD)\b)\s*([\d,]+(?:\.\d+)?)")
_QTY_UNIT = r"(?:kgs?|kilograms?|t|mt|tonnes?|tons?|lbs?|pounds?|units?|pcs?|pieces?)"
_VOLUME_RE = re.compile(
    r"(?:MOQ|minimum order)\s*[:\-]?\s*[\d,]+(?:\s*" + _QTY_UNIT + r")?"
    r"|[≥>]=?\s*[\d,]+\s*" + _QTY_UNIT + r"?"
    r"|\b\d[\d,]*\s*-\s*\d[\d,]*\s*" + _QTY_UNIT,
    re.I,
)
_PRODUCT_RE = re.compile(r"(?:product|item|sku)\s*[:\-]\s*(.+)", re.I)
_VALID_UNTIL_RE = re.compile(r"valid\s+until\s+(.+?)(?:[.,;]|$)", re.I)
_VALID_FOR_DAYS_RE = re.compile(r"valid\s+for\s+(\d+)\s+days?", re.I)
_UNIT_RE = re.compile(r"\b(" + "|".join(sorted(UNIT_ALIASES, key=len, reverse=True)) + r")\b", re.I)


def _find_incoterm(pages: list[dict]) -> dict | None:
    # Case-sensitive on purpose: several deprecated codes (e.g. "FOR", "FOT")
    # are also common lowercase English words. Real documents write Incoterm
    # codes in caps almost universally, so matching only literal uppercase
    # occurrences eliminates that false-positive class at a negligible
    # recall cost.
    codes = sorted(set(INCOTERMS_2020) | set(DEPRECATED_INCOTERMS), key=len, reverse=True)
    pattern = re.compile(r"\b(" + "|".join(codes) + r")\b")
    for page in pages:
        for line in page["text"].splitlines():
            m = pattern.search(line)
            if not m:
                continue
            code = incoterm_normalize(m.group(1))
            if not code or not incoterm_is_valid(code):
                continue
            labeled = bool(re.search(r"(?:incoterm|delivery terms|terms)\s*[:\-]", line, re.I))
            return _mk_field(code, CONF_LABELED if labeled else CONF_CONTEXTUAL, page["page"], line)
    return None


def _find_named_place(pages: list[dict], incoterm_field: dict | None) -> dict | None:
    if not incoterm_field:
        return None
    snippet = incoterm_field["locator"]["snippet"]
    place = _named_place_from_text(snippet, incoterm_field["value"])
    if not place:
        return None
    return _mk_field(place, incoterm_field["confidence"], incoterm_field["locator"]["page"], snippet)


def _find_price(pages: list[dict]) -> dict | None:
    for page in pages:
        for line in page["text"].splitlines():
            m = _PRICE_RE_LABELED.search(line)
            if m:
                try:
                    return _mk_field(float(m.group(2).replace(",", "")), CONF_LABELED, page["page"], line)
                except ValueError:
                    pass
    for page in pages:
        for line in page["text"].splitlines():
            m = _PRICE_RE_BARE.search(line)
            if m:
                try:
                    return _mk_field(float(m.group(2).replace(",", "")), CONF_CONTEXTUAL, page["page"], line)
                except ValueError:
                    pass
    return None


def _find_currency(pages: list[dict], price_field: dict | None) -> dict | None:
    if price_field:
        snippet = price_field["locator"]["snippet"]
        m = re.search(r"[$€£¥]|\b(?:USD|EUR|GBP|JPY|CNY|CHF|CAD|AUD)\b", snippet)
        if m:
            token = m.group(0)
            ccy = CURRENCY_SYMBOLS.get(token, token if token in KNOWN_CURRENCIES else None)
            if ccy:
                return _mk_field(ccy, price_field["confidence"], price_field["locator"]["page"], snippet)
    for page in pages:
        for line in page["text"].splitlines():
            m = re.search(r"\b(" + "|".join(KNOWN_CURRENCIES) + r")\b", line)
            if m:
                return _mk_field(m.group(1), CONF_WEAK, page["page"], line)
    return None


def _find_unit(pages: list[dict]) -> dict | None:
    for page in pages:
        for line in page["text"].splitlines():
            m = _UNIT_RE.search(line)
            if m:
                canonical = UNIT_ALIASES[m.group(1).lower()]
                labeled = bool(re.search(r"(?:unit|uom)\s*[:\-]", line, re.I))
                return _mk_field(canonical, CONF_LABELED if labeled else CONF_CONTEXTUAL, page["page"], line)
    return None


def _find_volume_tier(pages: list[dict]) -> dict | None:
    for page in pages:
        for line in page["text"].splitlines():
            m = _VOLUME_RE.search(line)
            if m:
                labeled = bool(re.search(r"(?:moq|tier|quantity|volume)", line, re.I))
                return _mk_field(m.group(0), CONF_LABELED if labeled else CONF_CONTEXTUAL, page["page"], line)
    return None


def _find_product_reference(pages: list[dict]) -> dict | None:
    for page in pages:
        for line in page["text"].splitlines():
            m = _PRODUCT_RE.search(line)
            if m and m.group(1).strip():
                return _mk_field(m.group(1).strip(), CONF_LABELED, page["page"], line)
    return None


def _find_quote_date(pages: list[dict]) -> dict | None:
    for page in pages:
        for line in page["text"].splitlines():
            d = _try_parse_date(line)
            if d:
                labeled = bool(re.search(r"(?:quote date|date)\s*[:\-]", line, re.I))
                return _mk_field(d.isoformat(), CONF_LABELED if labeled else CONF_WEAK, page["page"], line)
    return None


def _find_validity(pages: list[dict], quote_date_field: dict | None) -> dict | None:
    for page in pages:
        for line in page["text"].splitlines():
            m = _VALID_UNTIL_RE.search(line)
            if m:
                d = _try_parse_date(m.group(1))
                if d:
                    return _mk_field(d.isoformat(), CONF_LABELED, page["page"], line)
            m2 = _VALID_FOR_DAYS_RE.search(line)
            if m2 and quote_date_field:
                try:
                    base = date.fromisoformat(quote_date_field["value"])
                except (ValueError, TypeError):
                    continue
                d = base + timedelta(days=int(m2.group(1)))
                return _mk_field(d.isoformat(), CONF_CONTEXTUAL, page["page"], line)
    return None


def _line_from_full_text(pages: list[dict]) -> dict:
    fields: dict = {}

    incoterm = _find_incoterm(pages)
    if incoterm:
        fields["incoterm"] = incoterm
        place = _find_named_place(pages, incoterm)
        if place:
            fields["named_place"] = place

    price = _find_price(pages)
    if price:
        fields["price"] = price
    currency = _find_currency(pages, price)
    if currency:
        fields["currency"] = currency
    unit = _find_unit(pages)
    if unit:
        fields["unit"] = unit
    volume_tier = _find_volume_tier(pages)
    if volume_tier:
        fields["volume_tier"] = volume_tier
    product_reference = _find_product_reference(pages)
    if product_reference:
        fields["product_reference"] = product_reference
    quote_date = _find_quote_date(pages)
    if quote_date:
        fields["quote_date"] = quote_date
    valid_until = _find_validity(pages, quote_date)
    if valid_until:
        fields["valid_until"] = valid_until

    return fields
