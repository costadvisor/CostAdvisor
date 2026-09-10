"""Shared diff report for the drop loaders (Wave 3, SCRUM-74).

Both loaders — the index layer and the catalog — owe the same thing: a
per-table account of inserts, updates and skips-with-reasons, so a partial
load is explainable without re-running it and a second run can be shown to
change nothing.

`stale` is deliberately separate from `deleted`: a platform row the drop no
longer mentions is reported and left in place, never removed. The drop is
authoritative for what it covers and silent about everything else, and
deleting on silence is how a load quietly destroys data that another source
still owns.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TableDiff:
    table: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    # Rows the drop no longer mentions. Reported, not removed.
    stale: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return self.created + self.updated + self.deleted

    def line(self) -> str:
        parts = [
            f"{self.table:28s}",
            f"created={self.created:5d}",
            f"updated={self.updated:5d}",
            f"unchanged={self.unchanged:5d}",
        ]
        if self.deleted:
            parts.append(f"deleted={self.deleted:5d}")
        if self.stale:
            parts.append(f"stale={self.stale:4d}")
        if self.skipped:
            parts.append(f"skipped={len(self.skipped):4d}")
        return "  ".join(parts)


@dataclass
class LoadReport:
    title: str = "load"
    tables: list[TableDiff] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return sum(t.changed for t in self.tables)

    @property
    def skipped(self) -> list[tuple[str, str, str]]:
        return [(t.table, key, why) for t in self.tables for key, why in t.skipped]

    def table(self, name: str) -> TableDiff | None:
        return next((t for t in self.tables if t.table == name), None)

    def render(self, *, dry_run: bool = False, skip_limit: int = 40) -> str:
        head = "DRY RUN — rolled back" if dry_run else "complete"
        lines = [f"{self.title} {head}", ""]
        lines += [t.line() for t in self.tables]
        if self.skipped:
            lines += ["", f"Skipped ({len(self.skipped)}):"]
            lines += [
                f"  {tbl}: {key} — {why}" for tbl, key, why in self.skipped[:skip_limit]
            ]
            if len(self.skipped) > skip_limit:
                lines.append(f"  … and {len(self.skipped) - skip_limit} more")
        lines += ["", f"total changes: {self.changed}"]
        return "\n".join(lines)
