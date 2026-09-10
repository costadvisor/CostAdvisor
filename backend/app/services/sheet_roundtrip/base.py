"""Sheet round-trip payload interface (Scrum 27b).

A payload spec is the only thing that knows about a specific table's shape
(FormulaRegionCoverage today). The generic export/diff/apply mechanism
(excel_io.py, diff.py, routers/sheets.py) never branches on payload_key —
adding a second payload is a new spec + one registry entry, not a change to
the mechanism.

Column type-conversion and business validation (currency must be 3 letters,
a price must be non-negative, ...) live on the column, owned by the spec —
not in the generic mechanism — for the same reason ProviderAdapter/
BaseScraper push provider-specific logic into the adapter rather than the
registry/orchestration layer.
"""
from abc import ABC, abstractmethod
from typing import Any, Callable, Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session


class SheetColumnSpec:
    """One column of a payload's sheet.

    `kind`:
      - "key": identifies the row (e.g. formula code, region). Locked in the
        export; editing it never re-keys a row — it just makes that row
        unmatched (reported, not silently absorbed).
      - "editable": human-owned; a genuine change here is what an import
        call reports as `kind="change"` and what `apply` writes back.
      - "readonly": system-owned reference/context. Shown for the human's
        benefit but locked; an edit here is reported as
        `kind="rejected_readonly_edit"`, never applied.
    """

    def __init__(
        self,
        name: str,
        kind: Literal["key", "editable", "readonly"],
        header_label: str,
        parse: Callable[[str], Any],
        to_string: Callable[[Any], str] = lambda v: "" if v is None else str(v),
    ):
        self.name = name
        self.kind = kind
        self.header_label = header_label
        self.parse = parse
        self.to_string = to_string


class SheetPayloadSpec(ABC):
    key: str
    sheet_name: str
    columns: list[SheetColumnSpec]
    permission_key: str
    filter_schema: type[BaseModel]

    @property
    def key_columns(self) -> list[SheetColumnSpec]:
        return [c for c in self.columns if c.kind == "key"]

    @property
    def editable_columns(self) -> list[SheetColumnSpec]:
        return [c for c in self.columns if c.kind == "editable"]

    @property
    def readonly_columns(self) -> list[SheetColumnSpec]:
        return [c for c in self.columns if c.kind == "readonly"]

    def column(self, name: str) -> SheetColumnSpec | None:
        return next((c for c in self.columns if c.name == name), None)

    @abstractmethod
    def query_rows(self, db: Session, filter_spec: BaseModel) -> list[dict]:
        """Current live rows for the given filter, as flat dicts keyed by
        column name — must include every key/editable/readonly column."""
        ...

    @abstractmethod
    def apply_change(self, db: Session, row_key: dict, column: str, value: Any) -> None:
        """Write one field back via the ORM. Does not commit."""
        ...

    @abstractmethod
    def get_current_value(self, db: Session, row_key: dict, column: str) -> Any:
        """Fetch the live value for one (row_key, column) — used at apply
        time to detect a concurrent edit since the diff was computed."""
        ...
