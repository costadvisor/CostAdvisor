"""Shared pieces kept from the 2026-07 drop machinery.

The July loaders and seeds were removed (ARCHITECTURE_LIVE_DATA §5.4). What
remains is what the content-drop loaders, a few services and the ungated tests
still import:

* `report`         — `LoadReport` / `TableDiff`, the loaders' change report.
* `normalize`      — value-level quirks (the two middle-dot encodings, the
  typographic minus, the `fixed` sentinel).
* `reader`         — `DropNotAvailable` and the `drop_available()` probe.
* `common`         — `REGION_MAP`, `BASE_PERIOD`, `_apply`, `_clean`, `_slug`.
* `dossier_loader` — index-dossier parsing and the per-series writer the
  content index loader reuses.
"""
from app.services.drop.normalize import (
    FIXED_TYPE_CODE,
    MIDDLE_DOT,
    coalesce,
    is_blank,
    is_fixed_line,
    normalize_cell,
    normalize_object_list,
    parse_bool,
    parse_int,
    parse_number,
)
from app.services.drop.reader import DROP_DIR, DropNotAvailable, drop_available
from app.services.drop.report import LoadReport, TableDiff

__all__ = [
    # normalize
    "FIXED_TYPE_CODE", "MIDDLE_DOT", "coalesce", "is_blank", "is_fixed_line",
    "normalize_cell", "normalize_object_list", "parse_bool", "parse_int",
    "parse_number",
    # reader
    "DROP_DIR", "DropNotAvailable", "drop_available",
    # report
    "LoadReport", "TableDiff",
]
