"""Card kind and supply status: the decision table of design §2.3.

Every catalogue card gets two separate facts:

* its **kind**: is it a product at all (`product`, `group`), or a card that
  answers with another one (`absorbed`, `pointer`, `duplicate`);
* its **supply status**: whether three makers are verified (Laurent's
  "supplier floor"), and nothing else (`live`, `supply_exception`,
  `supply_pending`, `not_audited`; NULL for a card that is not a product).

The tests run in this order and the first match wins:

    1  `_archival` starts with "POINTER to <PID>"   pointer, redirect_to = PID
    2  `_archival` starts with "DUPLICATE"          duplicate, redirect_to = the
                                                    PID after "priced as"
    3  PID in ABSORBED_FORMULA_IDS                  absorbed (the set decides,
                                                    never the text: some
                                                    maintained cards still carry
                                                    stale "ABSORBED" wording)
    4  GRP-* with an AUTO_GROUPS entry              group, not_audited (no group
                                                    card has ever been audited)
    5  GRP-* without one                            not loaded (a shell)
    6  `supply_exception` present                   product, supply_exception
    7  `supply_pending.status` is GRADE_PENDING
       or SUPPLY_PENDING                            product, supply_pending
    8  `maker_audit.outcome` is LIVE                product, live
    9  anything else                                product, not_audited

`supply_status_detail` is `{source, value, date}` only: which block decided
the status, its raw value, and that block's own `date` key. No audit field
(`batch`, `verified`, `prior`, `why`, `by`) is ever copied.

`redirects()` adds the older merge style: a card that names the PIDs merged
into it under `merged_from`. Those PIDs have no record any more, so they are
logged, not loaded.

Reads the drop through `reader`; no database.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.content_drop.reader import raw

KIND_PRODUCT = "product"
KIND_GROUP = "group"
KIND_ABSORBED = "absorbed"
KIND_POINTER = "pointer"
KIND_DUPLICATE = "duplicate"
KIND_WITHDRAWN = "withdrawn"
REDIRECT_KINDS = (KIND_POINTER, KIND_DUPLICATE)

STATUS_LIVE = "live"
STATUS_EXCEPTION = "supply_exception"
STATUS_PENDING = "supply_pending"
STATUS_NOT_AUDITED = "not_audited"

_PENDING_VALUES = ("GRADE_PENDING", "SUPPLY_PENDING")
_PID = r"[A-Z0-9]+(?:-[A-Z0-9]+)+"
_POINTER = re.compile(rf"^POINTER to ({_PID})")
_PRICED_AS = re.compile(rf"priced as\W*({_PID})")


@dataclass(frozen=True)
class CardStatus:
    kind: str
    supply_status: str | None
    detail: dict | None
    redirect_to: str | None


def _detail(source: str, block: dict, value: str | None) -> dict:
    return {"source": source, "value": value, "date": block.get("date")}


def classify(pid: str) -> CardStatus | None:
    """The decision table for one PID. None for a shell (test 5), which gets
    no template. A PID the drop does not carry at all classifies as a
    not-audited product; callers decide what to load."""
    card = raw("CURATED_CONTENT").get(pid) or {}
    archival = card.get("_archival")
    if isinstance(archival, str):
        pointer = _POINTER.match(archival)
        if pointer:
            return CardStatus(KIND_POINTER, None, None, pointer.group(1))
        if archival.startswith("DUPLICATE"):
            twin = _PRICED_AS.search(archival)
            return CardStatus(KIND_DUPLICATE, None, None, twin.group(1) if twin else None)
    if pid in _absorbed():
        return CardStatus(KIND_ABSORBED, None, None, None)
    if pid.startswith("GRP-"):
        if pid in raw("AUTO_GROUPS"):
            return CardStatus(KIND_GROUP, STATUS_NOT_AUDITED, None, None)
        return None
    exception = card.get("supply_exception")
    if exception:
        block = exception if isinstance(exception, dict) else {}
        return CardStatus(KIND_PRODUCT, STATUS_EXCEPTION,
                          _detail("supply_exception", block, block.get("status")), None)
    pending = card.get("supply_pending")
    if isinstance(pending, dict) and pending.get("status") in _PENDING_VALUES:
        return CardStatus(KIND_PRODUCT, STATUS_PENDING,
                          _detail("supply_pending", pending, pending["status"]), None)
    audit = card.get("maker_audit")
    if isinstance(audit, dict) and audit.get("outcome") == "LIVE":
        return CardStatus(KIND_PRODUCT, STATUS_LIVE, _detail("maker_audit", audit, "LIVE"), None)
    return CardStatus(KIND_PRODUCT, STATUS_NOT_AUDITED, None, None)


def _absorbed() -> frozenset[str]:
    return frozenset(raw("ABSORBED_FORMULA_IDS"))


def is_shell(pid: str) -> bool:
    """A `GRP-*` card with no AUTO_GROUPS entry: curated text only, no
    template (test 5)."""
    return pid.startswith("GRP-") and pid not in raw("AUTO_GROUPS")


def card_codes() -> list[str]:
    """Every PID that gets a platform template: the CURATED_CONTENT keys, the
    FORMULA_COMBOS records and the AUTO_GROUPS groups, minus the shells. In
    a stable order: records, then groups, then the card-only keys."""
    records, groups = raw("FORMULA_COMBOS"), raw("AUTO_GROUPS")
    out = [p for p in records if not is_shell(p)]
    seen = set(out)
    for code in groups:
        if code not in seen:
            out.append(code)
            seen.add(code)
    for code in raw("CURATED_CONTENT"):
        if code not in seen and not is_shell(code):
            out.append(code)
            seen.add(code)
    return out


def shells() -> list[str]:
    return sorted(p for p in raw("CURATED_CONTENT") if is_shell(p))


def redirects() -> dict[str, str | None]:
    """Pointer and duplicate PIDs → their target PID (None when a duplicate's
    text names no twin)."""
    out: dict[str, str | None] = {}
    for pid in card_codes():
        status = classify(pid)
        if status is not None and status.kind in REDIRECT_KINDS:
            out[pid] = status.redirect_to
    return out


def merged_from() -> dict[str, list[str]]:
    """The older merge style: target PID → the PIDs merged into it (they have
    no record any more; logged, not loaded)."""
    out: dict[str, list[str]] = {}
    for pid, card in raw("CURATED_CONTENT").items():
        entries = card.get("merged_from")
        if isinstance(entries, list):
            names = [e["pid"] for e in entries if isinstance(e, dict) and e.get("pid")]
            if names:
                out[pid] = names
    return out
