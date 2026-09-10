import uuid
from datetime import datetime

from pydantic import BaseModel


class SheetImportRowDiffOut(BaseModel):
    id: int
    row_key: dict
    column: str
    old_value: str | None = None
    new_value: str | None = None
    kind: str  # change | rejected_readonly_edit | unmatched_key | invalid_value
    applied: bool

    model_config = {"from_attributes": True}


class SheetImportRunOut(BaseModel):
    id: uuid.UUID
    payload_key: str
    filter_spec: dict
    status: str  # empty | diffed | applied
    row_count: int
    imported_by: uuid.UUID
    created_at: datetime
    applied_by: uuid.UUID | None = None
    applied_at: datetime | None = None
    diffs: list[SheetImportRowDiffOut] = []

    model_config = {"from_attributes": True}


class SheetApplyResult(BaseModel):
    run: SheetImportRunOut
    applied: list[SheetImportRowDiffOut]
    skipped_stale: list[SheetImportRowDiffOut]
