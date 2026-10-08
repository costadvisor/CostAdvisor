"""Loaders for the content drop (design ARCHITECTURE_LIVE_DATA §1, §3).

One module per loader, each exposing `load(db, report)` and run in this order
by `backend/seed_content_drop.py`:

    taxonomy   families, sub-families, product lines, demand axis
    indexes    series, monthly values, type codes, cards, dossiers
    catalogue  formula templates, combos, cost lines, groups
    content    editorial blocks, dimensions, producers
    reports    market reports, sections, panels, line joins
    playbooks  playbooks, levers, objectives

The contract every loader keeps:

* **Idempotent.** A second run reports zero changes. Upsert on the natural
  key; never delete-and-reinsert rows other tables point at.
* **The CLI owns the transaction.** A loader flushes, never commits — that is
  what makes `--dry-run` a real rehearsal across all six loaders.
* **Account for every row** on the `LoadReport` it is handed
  (`app/services/drop/report.py`): created / updated / unchanged, skips with
  a reason, stale rows reported and left in place.
* **Never invent a number.** A value the drop does not carry stays NULL.

`reader` is the shared access layer: file locations (`CONTENT_DROP_DIR`),
the drop's provenance and completeness gate (`manifest`, `assert_complete`,
`check_commit`), cached parsed files, `line_key_of(pid)`, the combo-id repair,
the region map and the loader identity.
"""
from app.services.drop.report import LoadReport, TableDiff
from app.services.drop.reader import DropNotAvailable
from app.services.content_drop.reader import (
    DEFAULT_CONTENT_DROP_DIR,
    DROP_REGION_BY_APP,
    CONTENT_DROP_ENV,
    LINE_KEY_SEP,
    REGION_MAP,
    DropManifest,
    DropRefused,
    app_region,
    assert_complete,
    axis,
    check_commit,
    combo_pid,
    drop_available,
    drop_dir,
    drop_region,
    incomplete_reasons,
    indexes_file,
    industries,
    line_key_of,
    loader_user_id,
    make_line_key,
    manifest,
    pids_on_line,
    playbook_files,
    raw,
    raw_function,
    read_json,
    repair_combo_id,
    report_html,
    report_manifest,
    reset_caches,
    scope,
    split_line_key,
    tree,
)

# The run order. seed_content_drop.py resolves each to app.services.content_drop.<name>.
LOADER_ORDER = ("taxonomy", "indexes", "catalogue", "content", "reports", "playbooks")

__all__ = [
    "LOADER_ORDER", "LoadReport", "TableDiff", "DropNotAvailable", "DropRefused",
    "DropManifest", "DEFAULT_CONTENT_DROP_DIR", "DROP_REGION_BY_APP", "CONTENT_DROP_ENV",
    "LINE_KEY_SEP", "REGION_MAP", "app_region", "assert_complete", "axis", "check_commit",
    "combo_pid", "drop_available", "drop_dir", "drop_region", "incomplete_reasons",
    "indexes_file", "industries", "line_key_of", "loader_user_id", "make_line_key",
    "manifest", "pids_on_line", "playbook_files", "raw", "raw_function",
    "read_json", "repair_combo_id", "report_html", "report_manifest", "reset_caches",
    "scope", "split_line_key", "tree",
]
