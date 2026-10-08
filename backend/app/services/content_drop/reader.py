"""Reader for the content drop (design ARCHITECTURE_LIVE_DATA §1, §3.2).

The drop is the live DB page's constants plus the authored files around it,
extracted (parsed, never executed) from Laurent's repo at one pinned commit by
`tools/drop_extract/extract_db.mjs` (routinely through `refresh.sh`) into
`docs/drop_live/`, or wherever `CONTENT_DROP_DIR` points (required outside the
main checkout):

    _manifest.json            source commit, date, branch, extractor, constants

    raw/<CONST>.json          every constant of the live page (FORMULA_COMBOS …)
    raw/_functions/*.js       the page's own helper sources (the canonicaliser)
    tree/category_tree.json   demand axis v5.1
    axis/supply_axis.json     supply axis (families › sub-families › lines)
    scope/v1_scope.json       demo-grade lines + report_map
    scope/industries_v2.json  the 50 reference buyers
    reports/*.html + MANIFEST.json
    playbooks/playbook_<slug>_appdata.json
    indexes/INDEXES.json, IDX.json, FORE.json

Six loaders read it (taxonomy, indexes, catalogue, content, reports,
playbooks). This module owns what they all need once: where the drop is, its
provenance (`manifest()`) and the completeness gate (`assert_complete()`,
`check_commit()`), the parsed files (cached — several are megabytes and every
loader wants FORMULA_COMBOS), the line key of a product, the combo-id escape
repair, the region vocabulary, and whose name platform rows are written under.

Vocabulary: the key `"subfamily"` in Laurent's records **is the product
line**, not our sub-family tier. Every read of it below says so.

Parsed JSON is cached per process and **shared**: treat it as read-only.
Call `reset_caches()` after changing `CONTENT_DROP_DIR` (tests do).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.user import User
from app.services.drop.common import REGION_MAP
from app.services.drop.normalize import MIDDLE_DOT
from app.services.drop.reader import DropNotAvailable

# backend/app/services/content_drop/reader.py -> repo root is 4 levels up
# (the app/services/drop/reader.py convention): docs/drop_live.
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONTENT_DROP_DIR = _REPO_ROOT / "docs" / "drop_live"
CONTENT_DROP_ENV = "CONTENT_DROP_DIR"

# The line key joining both axes, the reports and the catalogue.
LINE_KEY_SEP = "|||"

# Some combo ids carry the literal six characters backslash, u, 0, 0, b, 7
# instead of the real middle dot (the same trap the July drop had; see
# drop/normalize.py). Repaired on read by `repair_combo_id`.
_LITERAL_MIDDLE_DOT = "\\u00b7"

# The drop's region codes onto the app's `regions.code`, and back. Store app
# codes; APIs show the drop code (spec D2, Regions).
DROP_REGION_BY_APP = {app: drop for drop, app in REGION_MAP.items()}


# ── Where the drop is ────────────────────────────────────────────────────────

def drop_dir() -> Path:
    """The drop root, from `CONTENT_DROP_DIR` or the repo default. Raises
    `DropNotAvailable` rather than returning an empty path, so a loader run
    against a missing drop fails loudly instead of reporting a clean no-op."""
    root = Path(os.environ.get(CONTENT_DROP_ENV) or DEFAULT_CONTENT_DROP_DIR)
    if not root.is_dir():
        raise DropNotAvailable(
            f"Content drop not found at {root}. Set {CONTENT_DROP_ENV} to an extracted "
            f"drop (outside the main checkout it is required), or extract one with "
            f"`tools/drop_extract/refresh.sh`."
        )
    return root


def drop_available() -> bool:
    """Non-raising probe — for tests and optional work."""
    try:
        drop_dir()
    except DropNotAvailable:
        return False
    return True


def _path(*parts: str) -> Path:
    path = drop_dir().joinpath(*parts)
    if not path.is_file():
        raise DropNotAvailable(f"Drop file not found: {path}")
    return path


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def _cached_json(path_str: str) -> Any:
    return read_json(Path(path_str))


def _json(*parts: str) -> Any:
    return _cached_json(str(_path(*parts)))


def reset_caches() -> None:
    """Forget every parsed file and derived map (after changing the drop dir)."""
    _cached_json.cache_clear()
    _line_keys.cache_clear()
    _pids_by_line.cache_clear()


# ── Provenance and the completeness gate (design §1.2) ───────────────────────

class DropRefused(RuntimeError):
    """The drop exists but must not be loaded: its extraction is incomplete,
    it has no source commit, or it is not the commit the caller expected."""


@dataclass(frozen=True)
class DropManifest:
    """What `_manifest.json` says about where the drop came from."""
    commit: str | None
    date: datetime | None
    branch: str | None
    # The extractor's own label (`extractor` in the manifest).
    extractor_version: str | None
    # The source working tree had uncommitted changes when it was extracted.
    dirty: bool | None
    extracted_at: datetime | None
    drop_dir: Path
    raw: dict = field(repr=False, compare=False)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _manifest_json(*parts: str) -> dict:
    path = drop_dir().joinpath(*parts)
    if not path.is_file():
        raise DropRefused(
            f"{path} is missing: not a complete extract_db.mjs drop. Re-extract it "
            f"(tools/drop_extract/refresh.sh).")
    try:
        data = read_json(path)
    except (OSError, ValueError) as exc:
        raise DropRefused(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DropRefused(f"{path} is not a JSON object")
    return data


def manifest() -> DropManifest:
    """The drop's provenance from `_manifest.json`: source commit, its date,
    branch, the extractor label and the dirty flag. Read fresh on every call
    (it is small, and a refresh may swap the drop under a long process).
    Raises `DropRefused` when the file is missing or unreadable."""
    data = _manifest_json("_manifest.json")
    git = data.get("git") if isinstance(data.get("git"), dict) else {}
    commit = git.get("commit")
    return DropManifest(
        commit=commit if isinstance(commit, str) and commit else None,
        date=_parse_time(git.get("date")),
        branch=git.get("branch") if isinstance(git.get("branch"), str) else None,
        extractor_version=data.get("extractor") if isinstance(data.get("extractor"), str) else None,
        dirty=git.get("dirty") if isinstance(git.get("dirty"), bool) else None,
        extracted_at=_parse_time(data.get("extracted_at")),
        drop_dir=drop_dir(),
        raw=data,
    )


def incomplete_reasons() -> list[str]:
    """Why this drop is not a complete extraction; empty when it is.

    Reads the top manifest (`constants.failed` / `.partial`) and both page
    manifests (`raw/`, `indexes/`): a constant with `ok: false` or
    `complete: false`, or a script block that did not parse (its constants
    may be missing without a row saying so)."""
    reasons: list[str] = []
    top = _manifest_json("_manifest.json")
    constants = top.get("constants")
    if not isinstance(constants, dict):
        return ["_manifest.json has no `constants` summary (not an extract_db.mjs drop)"]
    for key in ("failed", "partial"):
        names = constants.get(key)
        if not isinstance(names, list):
            reasons.append(f"_manifest.json constants.{key} is missing")
        elif names:
            reasons.append(f"constants {key}: {', '.join(map(str, names))}")
    for page in ("raw", "indexes"):
        sub = _manifest_json(page, "_manifest.json")
        rows = sub.get("extracted")
        if not isinstance(rows, list):
            reasons.append(f"{page}/_manifest.json has no `extracted` rows")
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("ok") is False:
                reasons.append(f"{page}: {row.get('name')} failed ({row.get('reason', 'no reason')})")
            elif row.get("complete") is False:
                reasons.append(f"{page}: {row.get('name')} is partial")
        for block in sub.get("scripts") or []:
            if isinstance(block, dict) and block.get("parsed") is False:
                reasons.append(f"{page}: script block {block.get('index')} did not parse")
    return list(dict.fromkeys(reasons))


def assert_complete() -> DropManifest:
    """Refuse (`DropRefused`) a drop whose extraction failed or is partial, or
    that names no source commit (every load is pinned to one). Returns the
    manifest. A partial extraction must never reach a database (§1.2)."""
    reasons = incomplete_reasons()
    if reasons:
        shown = "; ".join(reasons[:10]) + (f"; and {len(reasons) - 10} more" if len(reasons) > 10 else "")
        raise DropRefused(
            f"the drop at {drop_dir()} is an incomplete extraction: {shown}. "
            f"Fix the extraction and re-extract (tools/drop_extract/refresh.sh); "
            f"never load a partial drop.")
    found = manifest()
    if not found.commit:
        raise DropRefused(
            f"the drop at {found.drop_dir} names no source commit (`git.commit` in "
            f"_manifest.json): extract it from a clone of Laurent's repo so the load "
            f"is pinned to a commit.")
    return found


def check_commit(expected: str | None) -> DropManifest:
    """`assert_complete()`, then refuse unless the drop is exactly commit
    `expected` (the full sha, as `_manifest.json` and `content_loads` hold it).
    `None` skips the commit comparison."""
    found = assert_complete()
    if expected is not None and found.commit.lower() != expected.strip().lower():
        raise DropRefused(
            f"the drop at {found.drop_dir} is commit {found.commit}, not the expected "
            f"{expected.strip()}. Re-extract the expected commit, or pass its full sha.")
    return found


# ── The files ────────────────────────────────────────────────────────────────

def raw(name: str) -> Any:
    """One constant of the live page, e.g. `raw("FORMULA_COMBOS")`."""
    return _json("raw", name if name.endswith(".json") else f"{name}.json")


def raw_function(name: str) -> str:
    """The source of one of the page's helper functions, e.g.
    `raw_function("CANONICALIZE_SUPPLIER")` — to port, never to execute."""
    fname = name if name.endswith(".js") else f"{name}.js"
    return _path("raw", "_functions", fname).read_text(encoding="utf-8")


def tree() -> dict:
    """Demand axis v5.1: `{_meta, shared, industries}`."""
    return _json("tree", "category_tree.json")


def axis() -> dict:
    """Supply axis: `families` › `subfamilies` › `lines`, plus `key_map`."""
    return _json("axis", "supply_axis.json")


def scope() -> dict:
    """v1 demo-grade scope: `lines`, `pids`, `report_map`, `flagship_industries`."""
    return _json("scope", "v1_scope.json")


def industries() -> list[dict]:
    """The reference buyers from `scope/industries_v2.json`."""
    return _json("scope", "industries_v2.json")["industries"]


def report_manifest() -> list[dict]:
    """The delivered reports: `{family, name, slug, delivered, data, batch}`."""
    return _json("reports", "MANIFEST.json")["reports"]


def report_html(file: str) -> str:
    """The delivered HTML of one report, by its MANIFEST `delivered` filename.
    Only a bare filename is accepted — never a path out of `reports/`."""
    if not file or Path(file).name != file:
        raise ValueError(f"report file must be a bare filename, got {file!r}")
    return _path("reports", file).read_text(encoding="utf-8")


def playbook_files() -> list[Path]:
    """The playbook files, sorted by name (a stable load order)."""
    folder = drop_dir() / "playbooks"
    if not folder.is_dir():
        raise DropNotAvailable(f"Drop folder not found: {folder}")
    return sorted(folder.glob("playbook_*_appdata.json"))


def indexes_file(name: str) -> Any:
    """`INDEXES`, `IDX` or `FORE` from `indexes/`. Series values come from
    `raw("FIDX")` / `raw("FFORE")`; the cards come from `INDEXES`."""
    return _json("indexes", name if name.endswith(".json") else f"{name}.json")


# ── Keys and codes ───────────────────────────────────────────────────────────

def make_line_key(family: str, line: str) -> str:
    return f"{family}{LINE_KEY_SEP}{line}"


def split_line_key(key: str) -> tuple[str, str]:
    family, sep, line = key.partition(LINE_KEY_SEP)
    if not sep:
        raise ValueError(f"not a line key: {key!r}")
    return family, line


def repair_combo_id(combo_id: str) -> str:
    """`FORMULA_COMBOS` combo ids are `{pid}·{region}`; some carry the
    literal `\\u00b7` escape instead of the middle dot. The literal cannot
    legitimately occur, so the substitution is unconditional."""
    return combo_id.replace(_LITERAL_MIDDLE_DOT, MIDDLE_DOT)


def combo_pid(combo_id: str) -> str:
    """The PID part of a (repaired) combo id."""
    return repair_combo_id(combo_id).split(MIDDLE_DOT, 1)[0]


@lru_cache(maxsize=None)
def _line_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    for pid, rec in raw("FORMULA_COMBOS").items():
        # The drop's `subfamily` is the product line (Laurent's key name).
        family, line = rec.get("family"), rec.get("subfamily")
        if not (family and line):
            # The record-level fields are the source; some combos lack them, so
            # the fallback for a record without them is the first combo that has
            # both (the drop's `subfamily` is again the product line).
            for combo in rec.get("combos") or []:
                if combo.get("family") and combo.get("subfamily"):
                    family, line = combo["family"], combo["subfamily"]
                    break
        if family and line:
            keys[pid] = make_line_key(family, line)
    return keys


def line_key_of(pid: str) -> str | None:
    """The record-level line key of a PID: `FORMULA_COMBOS[pid].family|||.subfamily`
    (the drop's field called `subfamily` is the product line). None for a PID
    the drop does not carry.

    This is the key as the record writes it. A few records sit on axis lines
    the drop leaves deliberately unnamed, so their key is not a current axis
    line; callers resolving it to `product_lines` (by `line_key`, then
    `former_keys`) must allow for a key with no row.
    """
    return _line_keys().get(pid)


@lru_cache(maxsize=None)
def _pids_by_line() -> dict[str, tuple[str, ...]]:
    by_line: dict[str, list[str]] = {}
    for pid, key in _line_keys().items():
        by_line.setdefault(key, []).append(pid)
    return {k: tuple(sorted(v)) for k, v in by_line.items()}


def pids_on_line(line_key: str) -> tuple[str, ...]:
    """Every PID whose record-level line is `line_key` — the expansion of a
    tree member written as `"*"`."""
    return _pids_by_line().get(line_key, ())


def app_region(drop_code: str) -> str | None:
    """The app's `regions.code` for a drop region code (`EU` → `Europe`,
    `GL` → `GLOBAL`). None for a code with no mapping — report it, never
    guess."""
    return REGION_MAP.get(drop_code)


def drop_region(app_code: str) -> str | None:
    """The drop's display code for an app region code (`Europe` → `EU`)."""
    return DROP_REGION_BY_APP.get(app_code)


# ── Loader identity ──────────────────────────────────────────────────────────

def loader_user_id(db: Session):
    """The user platform rows are attributed to: the first super admin, by
    account age, resolved at run time (never a hardcoded UUID —
    `formula_templates.created_by` is NOT NULL)."""
    uid = (
        db.query(User.id)
        .filter(User.is_super_admin.is_(True), User.deleted_at.is_(None))
        .order_by(User.created_at)
        .limit(1)
        .scalar()
    )
    if uid is None:
        raise RuntimeError(
            "No super-admin user exists to attribute platform rows to; "
            "create one before loading the drop."
        )
    return uid
