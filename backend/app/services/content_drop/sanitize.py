"""The served form of the content drop's editorial blocks (design §3.2, WP-4).

Every block is written in the form the screens show. The cleaning used to
happen at read time in the product API, so the editorial API (which serves
blocks raw) and the product page could show different text. Now the content
loader applies these functions on write, and the readers apply the same ones
again (each is idempotent), so every path shows the same text.

    strip_private(value)        `_`-prefixed keys removed at any depth
    served_applications(list)   entries marked `_no_receiver` dropped, then stripped
    served_compliance(list)     a bare-string entry given the entry shape
    clean_supplier_note(text)   the data-team instruction about `share:0` removed
    clean_current_events(text)  "sibling PID(s)" read as "related product(s)"
    served_body(type, body)     the one entry point: the rules above per block type

`served_body` returns None when nothing is left to show; the loader then
writes no block.

No drop text lives in this module: the examples below are made up.
"""
from __future__ import annotations

import re
from typing import Any

# ── Private keys ─────────────────────────────────────────────────────────────


def strip_private(value: Any) -> Any:
    """`value` without any `_`-prefixed dict key, at any depth. Lists keep
    their order; other values are returned as they are."""
    if isinstance(value, dict):
        return {k: strip_private(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [strip_private(v) for v in value]
    return value


def served_applications(entries: Any) -> Any:
    """Applications without the entries the source flags `_no_receiver` (an
    industry with no buyer for this product), private keys stripped."""
    if not isinstance(entries, list):
        return strip_private(entries)
    return strip_private([a for a in entries
                          if not (isinstance(a, dict) and a.get("_no_receiver"))])


def served_compliance(entries: Any) -> Any:
    """Compliance entries with a bare-string entry given the entry shape.

    A bare string carries no flag and no type, so it gets `flag: None` and
    `bare: True` (a public key: the screens show it as a plain line with no
    badge). Every other entry is kept as authored, private keys stripped.
    """
    if not isinstance(entries, list):
        return strip_private(entries)
    out = []
    for entry in entries:
        if isinstance(entry, str):
            out.append({"flag": None, "type": "info", "name": entry, "desc": None,
                        "bare": True})
        else:
            out.append(strip_private(entry))
    return out


# ── Text clean-ups ───────────────────────────────────────────────────────────
#
# 1. supplier_note: many notes end with an instruction to the data team about
#    the structured `share` field (in the spirit of "shares are not public;
#    read share:0 as not disclosed"), in a few wordings. The screens show no
#    share at all, so the instruction is removed together with the connector
#    that introduces it ("; ", " - ", ", so ", ", and ") and the sentence is
#    closed with a full stop. A continuation after it (", and note that …")
#    becomes its own sentence. Nothing else in the note changes.
# 2. current_events: the internal phrase "sibling PID(s)" (a PID is the
#    source's product code) reads "related product(s)".
_Q = r"[\"'“”‘’]?"
_NOT_DISCLOSED = _Q + r"not\s+disclosed[.,]?" + _Q
_SHARE0_INSTRUCTION = re.compile(
    # the connector that introduces the instruction (absent at a sentence start)
    r"(?P<sep>\s*;\s*|\s+[-–—]\s+|\s*,\s*(?:so\s+|and\s+)?|\s+(?:so|and)\s+)?"
    r"(?P<lead>\s*)"
    r"(?:treat\s+share\s*:\s*0\s+as\s+" + _NOT_DISCLOSED +
    r"(?:,?\s+not\s+(?:as\s+)?" + _Q + r"zero(?:\s+volume)?[.,]?" + _Q +
    r"|\s+in\s+the\s+structured\s+field[^.]*|\s+for\s+those\s+entries|\s+throughout)?"
    r"|share\s*:\s*0\s+means\s+" + _NOT_DISCLOSED + r"(?:\s+throughout)?"
    r"|share\s*:\s*0\s*=\s*not\s+disclosed"
    r"|share\s*:\s*0\s+(?:stands\s+for\s+all\s+of\s+them|applies\s+to\s+every\s+row))"
    # a continuation (", and note …") or the sentence's own full stop
    r"(?:(?P<cont>,\s+and\s+)(?P<next>\w)|\.?)",
    re.IGNORECASE)
_SIBLING_PID = re.compile(r"\bsibling\s+PID(s?)\b")


def _drop_share0(m: re.Match) -> str:
    nxt = m.group("next")
    if m.group("sep"):                        # "…not public; read share:0 …" → "…not public."
        return "." + (f" {nxt.upper()}" if nxt else "")
    return m.group("lead") + nxt.upper() if nxt else ""   # a sentence of its own → removed


def clean_supplier_note(text: str | None) -> str | None:
    """The supplier note without the data-team instruction about `share:0`.
    None when nothing is left."""
    if not isinstance(text, str) or not text:
        return text
    out = _SHARE0_INSTRUCTION.sub(_drop_share0, text).strip()
    return out or None


def clean_current_events(text: str | None) -> str | None:
    """The current-events text with "sibling PID(s)" read as "related product(s)"."""
    if not isinstance(text, str) or not text:
        return text
    return _SIBLING_PID.sub(lambda m: f"related product{m.group(1)}", text)


# ── The one entry point ──────────────────────────────────────────────────────

def present(value: Any) -> bool:
    """Something to show: not None, not blank text, not an empty list or dict."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def served_body(block_type: str, body: Any) -> Any:
    """The body as it is stored and shown, or None when nothing is left."""
    if block_type == "applications":
        out = served_applications(body)
    elif block_type == "compliance":
        out = served_compliance(body)
    elif block_type == "supplier_note":
        out = clean_supplier_note(body) if isinstance(body, str) else strip_private(body)
    elif block_type == "current_events":
        out = clean_current_events(body) if isinstance(body, str) else strip_private(body)
    else:
        out = strip_private(body)
    return out if present(out) else None
