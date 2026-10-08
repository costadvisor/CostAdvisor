"""What is left of the 2026-07 drop reader.

The July loaders and their table reader were removed with the July seed path.
Two names stay because live code and the ungated tests use them:

* `DropNotAvailable` — the error a loader raises when its drop directory is
  missing (the content-drop loaders raise it too).
* `drop_available()` — a non-raising probe for the old `sample_idea` drop. That
  folder is tracked nowhere, so the probe is False in every checkout and the
  few tests still gated on it skip.
"""
from __future__ import annotations

from pathlib import Path

# backend/app/services/drop/reader.py -> repo root is 4 levels up.
_REPO_ROOT = Path(__file__).resolve().parents[4]
DROP_DIR = _REPO_ROOT / "sample_idea" / "costadvisor-data"


class DropNotAvailable(RuntimeError):
    """The drop directory is absent. Raised rather than returning empty so a
    loader run against a missing drop fails loudly instead of reporting a
    successful no-op import."""


def drop_available() -> bool:
    """Non-raising probe — for tests and for skipping optional work."""
    return DROP_DIR.is_dir()
