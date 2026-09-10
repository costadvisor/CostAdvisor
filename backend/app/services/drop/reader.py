"""Payload-agnostic reader for the 2026-07 data drop (SCRUM-74 unit 1).

Mirrors the shape of `app/services/sheet_roundtrip/`: a generic mechanism
here, per-file knowledge declared as specs in `specs.py`. Three loaders
(SCRUM-74's index layers, 76's editorial blocks, 77's dimensions) read this
drop; building the reader once is what stops three copies of the trap
handling drifting apart.

**Why stdlib `csv` and not pandas**, given the repo already depends on it:
`pd.read_csv` coerces the string "NA" to NaN by default, and `NA` is North
America in `index_commodities.source_region` (17 rows) and
`index_feeds.region` (18 rows). That is trap 1, and it is the highest-
probability silent data loss in the whole load. `na_filter=False` fixes it,
but only for as long as everyone remembers the flag. `csv.DictReader` never
coerces anything — every cell is a string, and typing is declared per column
by the spec — so the trap is structurally impossible rather than guarded.
(`file_parser.py` keeps using pandas for user uploads; different path,
different data, out of scope here.)
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.services.drop.normalize import (
    is_blank,
    normalize_cell,
    parse_bool,
    parse_int,
    parse_number,
)

# backend/app/services/drop/reader.py -> repo root is 4 levels up. Same
# convention as seed_catalog.DEFAULT_DIR (parents[1] from backend/).
_REPO_ROOT = Path(__file__).resolve().parents[4]
DROP_DIR = _REPO_ROOT / "sample_idea" / "costadvisor-data"


class DropNotAvailable(RuntimeError):
    """The drop directory is absent. Raised rather than returning empty so a
    loader run against a missing drop fails loudly instead of reporting a
    successful no-op import."""


def drop_root() -> Path:
    if not DROP_DIR.is_dir():
        raise DropNotAvailable(
            f"Data drop not found at {DROP_DIR}. Expected the costadvisor-data "
            f"folder (tables/, raw/, decisions/)."
        )
    return DROP_DIR


def drop_available() -> bool:
    """Non-raising probe — for tests and for skipping optional work."""
    return DROP_DIR.is_dir()


def _with_suffix(name: str, default: str) -> str:
    """Append the folder's usual extension only when the caller gave none —
    `tables/` holds `_manifest.json` alongside the CSVs, so a blanket
    `+ ".csv"` would mis-resolve it."""
    return name if Path(name).suffix else f"{name}{default}"


def table_path(name: str) -> Path:
    path = drop_root() / "tables" / _with_suffix(name, ".csv")
    if not path.is_file():
        raise DropNotAvailable(f"Drop table not found: {path}")
    return path


def raw_path(name: str) -> Path:
    path = drop_root() / "raw" / _with_suffix(name, ".json")
    if not path.is_file():
        raise DropNotAvailable(f"Drop raw file not found: {path}")
    return path


def decision_path(name: str) -> Path:
    path = drop_root() / "decisions" / _with_suffix(name, ".csv")
    if not path.is_file():
        raise DropNotAvailable(f"Drop decision form not found: {path}")
    return path


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Every cell as a normalised string. No type inference, no NaN, no
    blank-to-None coercion — those are the spec's job."""
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [
            {(k or ""): normalize_cell(v) for k, v in row.items()}
            for row in reader
        ]


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def read_raw(name: str) -> Any:
    """One of the raw/*.json editorial or reference files."""
    return read_json(raw_path(name))


def path_for_spec(spec) -> Path:
    """Specs resolve to tables/ or decisions/ — the caller never has to know
    which, so a decision form reads exactly like a data table."""
    from app.services.drop import specs as _specs

    if spec.name in _specs.DECISION_SPECS:
        return decision_path(spec.filename)
    return table_path(spec.filename)


def read_table(name: str, spec=None) -> list[dict[str, Any]]:
    """Rows of a drop CSV with the spec's typed columns coerced.

    Without a spec every value stays a string — which is always safe, just
    untyped. With one, the declared numeric/int/bool columns are converted
    and a bad value raises with the row number and column named, so a
    malformed cell is never silently absorbed.
    """
    from app.services.drop import specs as _specs

    if spec is None:
        spec = _specs.get_spec(name)
    rows = read_csv_rows(path_for_spec(spec) if spec else table_path(name))
    if spec is None:
        return rows

    typed: list[dict[str, Any]] = []
    for lineno, row in enumerate(rows, start=2):  # 1 is the header
        out: dict[str, Any] = dict(row)
        for column, converter in (
            [(c, parse_number) for c in spec.numeric_columns]
            + [(c, parse_int) for c in spec.int_columns]
            + [(c, parse_bool) for c in spec.bool_columns]
        ):
            if column not in out:
                continue
            try:
                out[column] = converter(out[column])
            except ValueError as exc:
                raise ValueError(
                    f"{spec.filename} row {lineno}, column {column!r}: {exc}"
                ) from exc
        typed.append(out)
    return typed


def row_key(row: dict, spec) -> tuple:
    """The spec's stable key for a row, as a tuple."""
    return tuple(row.get(c, "") for c in spec.key_columns)


def blank_key_rows(rows: list[dict], spec) -> list[int]:
    """Row numbers (1-based within the data, matching the CSV's own line
    numbering minus the header) whose key is wholly blank.

    families.csv's first data row is entirely blank — its root cause is the
    single formula carrying no taxonomy at either level, which is also why
    `combos.family`, `formulas.family` and the zero-line combo all appear in
    _issues.csv. One defect, four symptoms.
    """
    return [
        i
        for i, row in enumerate(rows, start=1)
        if all(is_blank(row.get(c)) for c in spec.key_columns)
    ]
