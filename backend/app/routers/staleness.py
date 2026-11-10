"""Stale-text worklist (LIVE-5 S5).

    GET  /api/staleness/texts     stale texts with their reasons, most serious first
    POST /api/staleness/review    "read it again": re-pin a text's baseline, clear its flag

For content approvers: the platform `content.approve` permission (super
admins pass), the same gate as approving a block. Readers never see reasons,
only the one-line note the readers' APIs carry.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.routers.auth import get_current_user
from app.services.permissions import has_platform_permission
from app.services.staleness import (
    KINDS, MODELS, OLD_TEXT, REASONS, mark_reviewed, reader_note,
)

router = APIRouter()


class StaleTextOut(BaseModel):
    kind: str
    id: str
    where: str
    reasons: list[dict]
    reader_note: str | None = None
    checked_at: datetime | None = None


class StaleListOut(BaseModel):
    counts: dict[str, int]          # kind -> stale texts, every kind
    by_reason: dict[str, int]       # reason -> stale texts
    age_only: int                   # stale only by age: one fix (re-load the content drop)
    items: list[StaleTextOut]


class ReviewIn(BaseModel):
    kind: str
    id: str


def _require_approver(db: Session, user: User) -> None:
    if not has_platform_permission(db, user, "content.approve"):
        raise HTTPException(status_code=403, detail="Platform permission required: content.approve")


def _where(kind: str, row) -> str:
    if kind == "section":
        return f"{row.slug} · {row.section_id}"
    if kind == "panel":
        return f"{row.slug} · panel {row.panel}"
    if kind == "lever":
        return f"lever {row.lever_code}"
    return f"{row.subject_code} · {row.block_type}" + (f" · {row.region}" if row.region else "")


def _severity(reasons: list[dict]) -> int:
    codes = [r["code"] for r in reasons]
    return min((REASONS.index(c) for c in codes if c in REASONS), default=len(REASONS))


@router.get("/texts", response_model=StaleListOut)
def stale_texts(kind: str | None = Query(None), reason: str | None = Query(None),
                include_age_only: bool = Query(False, description="also list texts stale only by age"),
                limit: int = Query(200, ge=1, le=2000),
                db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_approver(db, current_user)
    if kind is not None and kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {list(KINDS)}")
    counts, by_reason, age_only, items = {}, {}, 0, []
    for k in KINDS:
        model = MODELS[k]
        rows = db.query(model).filter(model.is_stale).all()
        counts[k] = len(rows)
        for row in rows:
            reasons = row.stale_reasons or []
            codes = [r["code"] for r in reasons]
            for c in codes:
                by_reason[c] = by_reason.get(c, 0) + 1
            if codes == [OLD_TEXT]:
                age_only += 1
                if not include_age_only:
                    continue
            if (kind and k != kind) or (reason and reason not in codes):
                continue
            items.append(StaleTextOut(kind=k, id=str(row.id), where=_where(k, row), reasons=reasons,
                                      reader_note=reader_note(reasons), checked_at=row.stale_checked_at))
    items.sort(key=lambda i: (_severity(i.reasons), i.kind, i.where))
    return StaleListOut(counts=counts, by_reason=by_reason, age_only=age_only, items=items[:limit])


@router.post("/review", response_model=StaleTextOut)
def review(data: ReviewIn, db: Session = Depends(get_db),
           current_user: User = Depends(get_current_user)):
    _require_approver(db, current_user)
    if data.kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {list(KINDS)}")
    try:
        row_id = data.id if data.kind == "block" else int(data.id)
        row = mark_reviewed(db, data.kind, row_id, by=current_user.email)
    except (LookupError, ValueError):
        raise HTTPException(404, f"No {data.kind} {data.id}")
    db.commit()
    return StaleTextOut(kind=data.kind, id=str(row.id), where=_where(data.kind, row), reasons=[],
                        reader_note=None, checked_at=row.stale_checked_at)
