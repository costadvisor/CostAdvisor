"""Payload-agnostic diff computation for the sheet round-trip mechanism
(Scrum 27b).

Always diffs against a FRESH query_rows() call — live DB state at import
time, never a snapshot cached from export time. This is what makes
concurrent edits "normal, not an edge case": two overlapping exports each
diff correctly against whatever is actually in the database when they're
reimported, independent of each other and of what the export looked like
when it was generated.
"""
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.sheet_roundtrip.base import SheetPayloadSpec

# Sentinel column name for a diff entry that couldn't be matched to any live
# row at all — the row's own key, not a single field, is what's wrong.
UNMATCHED_KEY_COLUMN = "__key__"


def _values_equal(a, b) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    numeric = (int, float, Decimal)
    if isinstance(a, numeric) and isinstance(b, numeric):
        return abs(float(a) - float(b)) < 1e-6
    return str(a) == str(b)


def compute_diff(db: Session, spec: SheetPayloadSpec, filter_spec, uploaded_rows: list[dict]) -> list[dict]:
    """Returns a list of dicts shaped exactly like SheetImportRowDiff's
    insertable columns (row_key, column, old_value, new_value, kind)."""
    live_rows = spec.query_rows(db, filter_spec)
    live_map = {
        tuple(row.get(c.name) for c in spec.key_columns): row
        for row in live_rows
    }

    entries: list[dict] = []
    for uploaded in uploaded_rows:
        row_key = {c.name: uploaded.get(c.name) for c in spec.key_columns}
        key_tuple = tuple(row_key[c.name] for c in spec.key_columns)
        live_row = live_map.get(key_tuple)

        if live_row is None:
            entries.append({
                "row_key": row_key, "column": UNMATCHED_KEY_COLUMN,
                "old_value": None, "new_value": None, "kind": "unmatched_key",
            })
            continue

        for col in spec.columns:
            if col.kind == "key" or col.name not in uploaded:
                continue
            raw = uploaded[col.name]
            live_value = live_row.get(col.name)

            try:
                parsed = col.parse(raw) if raw is not None else None
            except (ValueError, TypeError):
                entries.append({
                    "row_key": row_key, "column": col.name,
                    "old_value": col.to_string(live_value), "new_value": col.to_string(raw),
                    "kind": "invalid_value",
                })
                continue

            if _values_equal(parsed, live_value):
                continue  # unmodified — no entry, which is what makes AC2 true

            kind = "change" if col.kind == "editable" else "rejected_readonly_edit"
            entries.append({
                "row_key": row_key, "column": col.name,
                "old_value": col.to_string(live_value), "new_value": col.to_string(parsed),
                "kind": kind,
            })

    return entries
