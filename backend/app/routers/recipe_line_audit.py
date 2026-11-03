"""Recipe-line audit API (Jira 208).

    GET /api/recipe-audit          counts and weight by status, heaviest examples
    GET /api/recipe-audit/lines    the lines themselves, filtered and paged

The acceptance criterion — every recipe line points at a real index with data
where possible — as a read anyone can re-run. Statuses and their order are in
`services/recipe_line_audit.py`.

Platform grain, like `/api/resolution` and `/api/series-health`: catalogue
recipes only, no `team_id`, authentication required. Read only.
"""
from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.recipe_line_audit import (
    AuditSummaryOut, LineAuditOut, LineAuditPageOut,
)
from app.services.recipe_line_audit import STATUSES, audit_recipe_lines, summarise

router = APIRouter()


@router.get("", response_model=AuditSummaryOut)
def get_recipe_audit(
    as_of: date | None = Query(None, description="Age the data against this date (default today)"),
    examples: int = Query(5, ge=0, le=50, description="Heaviest lines shown per status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    as_of = as_of or date.today()
    lines = audit_recipe_lines(db, as_of=as_of)
    return AuditSummaryOut(**asdict(summarise(db, lines, as_of=as_of, examples=examples)))


@router.get("/lines", response_model=LineAuditPageOut)
def list_recipe_audit_lines(
    status: str | None = Query(None, description="Only lines in this status"),
    template_code: str | None = Query(None, description="Only this recipe"),
    as_of: date | None = Query(None, description="Age the data against this date (default today)"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lines heaviest first, so the lines that matter most to a cost come first."""
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown status {status!r}; one of {list(STATUSES)}")
    as_of = as_of or date.today()
    lines = audit_recipe_lines(db, as_of=as_of)
    if status is not None:
        lines = [l for l in lines if l.status == status]
    if template_code is not None:
        lines = [l for l in lines if l.template_code == template_code]
    lines.sort(key=lambda l: (-l.weight_pct, l.template_code or "", l.region or ""))
    return LineAuditPageOut(
        as_of=as_of,
        total=len(lines),
        lines=[LineAuditOut(**asdict(l)) for l in lines[offset:offset + limit]],
    )
