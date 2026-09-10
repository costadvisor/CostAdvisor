"""Shared reader for the 2026-07 data drop (Wave 3, SCRUM-74 unit 1).

`sample_idea/costadvisor-data/` is consumed by three separate loaders —
SCRUM-74's index layers, SCRUM-76's editorial blocks, SCRUM-77's dimensions.
Each has to survive the same set of verified defects in the source. This
package owns them once:

* `normalize` — the value-level quirks (the two middle-dot encodings, the
  typographic minus, "NA" meaning North America, the `fixed` sentinel, the
  polymorphic JSON arrays).
* `reader`    — the payload-agnostic mechanism, stdlib-csv based so no
  parser can coerce a value behind your back.
* `specs`     — per-file registry; adding a table is one entry.
* `issues`    — `_issues.csv` carried through, plus a round-trip check
  against the manifest's own count of it.
* `authority` — the "which column wins" rules, decided once with the
  evidence recorded.
"""
from app.services.drop.authority import (
    MarginResolution,
    ProxyStatusPair,
    find_margin_line,
    is_margin_line,
    is_priceable,
    proxy_status_pair,
    resolve_margin,
    unpriceable_lines,
)
from app.services.drop.issues import (
    DropIssue,
    declared_summary,
    issues_by_table,
    issues_for,
    load_issues,
    observed_summary,
    verify_issue_summary,
)
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
from app.services.drop.reader import (
    DROP_DIR,
    DropNotAvailable,
    decision_path,
    drop_available,
    drop_root,
    raw_path,
    read_csv_rows,
    read_json,
    read_raw,
    read_table,
    table_path,
)
from app.services.drop.specs import DropTableSpec, all_specs, get_spec, table_specs

__all__ = [
    # normalize
    "FIXED_TYPE_CODE", "MIDDLE_DOT", "coalesce", "is_blank", "is_fixed_line",
    "normalize_cell", "normalize_object_list", "parse_bool", "parse_int",
    "parse_number",
    # reader
    "DROP_DIR", "DropNotAvailable", "decision_path", "drop_available",
    "drop_root", "raw_path", "read_csv_rows", "read_json", "read_raw",
    "read_table", "table_path",
    # specs
    "DropTableSpec", "all_specs", "get_spec", "table_specs",
    # issues
    "DropIssue", "declared_summary", "issues_by_table", "issues_for",
    "load_issues", "observed_summary", "verify_issue_summary",
    # authority
    "MarginResolution", "ProxyStatusPair", "find_margin_line", "is_margin_line",
    "is_priceable", "proxy_status_pair", "resolve_margin", "unpriceable_lines",
]
