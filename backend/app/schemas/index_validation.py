"""Response contracts for the index data-quality validation API (SCRUM-34)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class FindingOut(BaseModel):
    id: uuid.UUID
    # `declared` came from the drop's own `_issues.csv` and was carried through
    # verbatim; `derived` is what a run computed. The ticket asks for these to
    # stay distinguishable, so it is a field rather than a convention.
    origin: str
    check_code: str
    severity: str
    # The ticket's "naming the table, key, column..."
    subject_table: str
    subject_key: str
    subject_column: str | None = None
    # "...and the two conflicting values", labelled — which side says what is
    # the actionable half.
    left_label: str | None = None
    left_value: str | None = None
    right_label: str | None = None
    right_value: str | None = None
    summary: str
    detail: dict | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    # Set once a run stops observing it. Findings are never deleted, so this is
    # how "the fix landed" is visible at all.
    resolved_at: datetime | None = None

    class Config:
        from_attributes = True


class FindingsResponse(BaseModel):
    total: int
    findings: list[FindingOut]


class RunOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None = None
    # Per-check counts, including zeros: a check that ran and found nothing must
    # be distinguishable from a check that never ran.
    checks: dict | None = None
    n_findings: int
    n_new: int
    n_resolved: int
    note: str | None = None

    class Config:
        from_attributes = True


class RunsResponse(BaseModel):
    runs: list[RunOut]


class RunRequest(BaseModel):
    # A derived-only run deliberately does not resolve declared findings by
    # silence — it has no opinion about them.
    include_declared: bool = True
    note: str | None = None
