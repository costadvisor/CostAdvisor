"""Value normalisation for the 2026-07 data drop (Wave 3, SCRUM-74 unit 1).

Every quirk handled here is a real, verified defect in
`sample_idea/costadvisor-data/` — not defensive coding. Each carries the
count that was measured, because the natural instinct on reading this file
is to "simplify" one away, and each one silently corrupts a load.

The drop's own README is explicit that row counts will move but the shape
will not, so nothing here keys on a count; the counts are documentation of
why the branch exists.
"""
from __future__ import annotations

# ── Trap 2: the middle dot arrives in two encodings ──────────────────────────
# combo_id is `{formula_id}·{region}`. 74 combo_ids and 412 combo_lines rows
# carry the literal six-character ASCII sequence · instead of the real
# U+00B7, and 36 formula_ids appear in BOTH styles across their own combos.
# Any grouping or dedupe on the raw string therefore splits those formulas in
# two. Normalising on read makes the two encodings one value; verified that
# after this substitution combos↔combo_lines join with zero orphans.
MIDDLE_DOT = "·"
_LITERAL_MIDDLE_DOT = "\\u00b7"

# ── Trap 8: U+2212 MINUS SIGN, not a hyphen ──────────────────────────────────
# 13 index_feeds.change_pct values use the typographic minus. float() raises
# on it, and it also blows up cp1252 log output on Windows.
_MINUS_SIGN = "−"

# ── Trap 5: `fixed` is a sentinel, not a foreign key ─────────────────────────
# 1,317 combo_lines rows carry type_code='fixed', which is deliberately absent
# from type_codes.csv — these are the margin and fixed-cost lines. A naive FK
# check reports 1,317 broken references.
FIXED_TYPE_CODE = "fixed"


def normalize_cell(raw: str | None) -> str:
    """Every CSV cell passes through here. Trims, and repairs the literal
    middle-dot escape (trap 2).

    The literal sequence `\\u00b7` cannot legitimately occur in this data, so
    the substitution is unconditional and safe.
    """
    if raw is None:
        return ""
    return raw.replace(_LITERAL_MIDDLE_DOT, MIDDLE_DOT).strip()


def is_blank(raw) -> bool:
    """Blank means blank. Deliberately does NOT treat "NA" as missing —
    that is trap 1: `NA` is North America in `index_commodities.source_region`
    (17 rows) and `index_feeds.region` (18 rows). Reading this drop with a
    NaN-coercing parser silently deletes North America.
    """
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def parse_number(raw) -> float | None:
    """Float, or None for blank. Handles the drop's numeric quirks: the
    typographic minus (trap 8), a trailing %, a leading +, and thousands
    separators. Raises ValueError on genuinely unparseable input rather than
    returning None, so a malformed value is never mistaken for a missing one.
    """
    if is_blank(raw):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(_MINUS_SIGN, "-").replace(",", "")
    s = s.rstrip("%").lstrip("+").strip()
    if s == "":
        return None
    return float(s)


def parse_int(raw) -> int | None:
    """Int, or None for blank. Goes through parse_number so the same quirks
    are handled; rejects a non-integral value rather than truncating it."""
    value = parse_number(raw)
    if value is None:
        return None
    if value != int(value):
        raise ValueError(f"expected an integer, got {raw!r}")
    return int(value)


_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}


def parse_bool(raw) -> bool | None:
    if is_blank(raw):
        return None
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise ValueError(f"expected a boolean, got {raw!r}")


def is_fixed_line(type_code: str | None) -> bool:
    """True for the `fixed` sentinel (trap 5). Call this before resolving a
    type_code as a foreign key."""
    return (type_code or "").strip().lower() == FIXED_TYPE_CODE


# ── Trap 19: polymorphic arrays in the raw JSON ──────────────────────────────
# CURATED_CONTENT.json mixes shapes inside what should be uniform lists:
#   compliance[]        367 dicts + 31 bare strings
#   applications[]      965 dicts + 53 nulls
#   applications[].spec 118 strings + 1 dict
# A typed loader throws partway through the file. Normalising to a uniform
# list of dicts up front keeps that out of every consumer.

def normalize_object_list(
    value,
    *,
    text_key: str,
    drop_nulls: bool = True,
) -> list[dict]:
    """Coerce a mixed list of dicts / bare strings / nulls into a uniform
    list of dicts. A bare string becomes `{text_key: <string>}` so the value
    survives instead of being dropped or crashing the parse.

    `drop_nulls` removes null entries (the 53 nulls in applications[]); they
    carry no information and there is nothing to preserve.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return [{text_key: value}]

    out: list[dict] = []
    for item in value:
        if item is None:
            if not drop_nulls:
                out.append({})
            continue
        if isinstance(item, dict):
            out.append(item)
        else:
            out.append({text_key: item})
    return out


def coalesce(entry: dict, *keys: str):
    """First present, non-blank value among `keys`.

    Exists because the drop states the same field under different names:
    `substitution[]` carries its body under `body` (392 entries) or `desc`
    (26). Picking one name silently loses the other set.
    """
    for key in keys:
        if key in entry and not is_blank(entry.get(key)):
            return entry[key]
    return None
