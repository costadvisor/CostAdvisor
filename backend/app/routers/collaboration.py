"""Scrum 25 — intra-team collaboration: notes + negotiation flag on a cost model.

Mounted at /api/cost-models. Reads require `costing.view` (any team member),
note creation is open to any member (view), while the negotiation flag and
deleting another member's note require `costing.edit`. @mentions (`@email`) in a
note body email the mentioned teammate. All mutations are audit-logged.
"""
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.models.team import TeamMembership
from app.models.cost_model import CostModel
from app.models.collaboration import CostModelNote
from app.routers.auth import get_current_user
from app.schemas.collaboration import NoteCreate, NoteOut, FlagUpdate, FlagOut, NEGOTIATION_STATES
from app.services.audit import log_event
from app.services.email import send_mention_email
from app.services.permissions import require_permission, has_permission

router = APIRouter()
settings = get_settings()

_MENTION_RE = re.compile(r"@([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def _get_model_or_404(db: Session, cost_model_id: uuid.UUID) -> CostModel:
    cm = db.query(CostModel).filter(CostModel.id == cost_model_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Cost model not found")
    return cm


def _author_names(db: Session, notes: list[CostModelNote]) -> dict:
    ids = {n.author_user_id for n in notes}
    if not ids:
        return {}
    rows = db.query(User.id, User.display_name, User.email).filter(User.id.in_(ids)).all()
    return {r[0]: (r[1] or r[2]) for r in rows}


@router.get("/{cost_model_id}/notes", response_model=list[NoteOut])
def list_notes(
    cost_model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = _get_model_or_404(db, cost_model_id)
    require_permission(db, current_user, cm.team_id, "costing.view")
    notes = (
        db.query(CostModelNote)
        .filter(CostModelNote.cost_model_id == cost_model_id)
        .order_by(CostModelNote.created_at.asc())
        .all()
    )
    names = _author_names(db, notes)
    return [
        NoteOut(
            id=n.id, cost_model_id=n.cost_model_id, author_user_id=n.author_user_id,
            author_name=names.get(n.author_user_id), parent_note_id=n.parent_note_id,
            body=n.body, created_at=n.created_at,
        )
        for n in notes
    ]


@router.post("/{cost_model_id}/notes", response_model=NoteOut, status_code=201)
def create_note(
    cost_model_id: uuid.UUID,
    data: NoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = _get_model_or_404(db, cost_model_id)
    require_permission(db, current_user, cm.team_id, "costing.view")

    note = CostModelNote(
        team_id=cm.team_id, cost_model_id=cm.id, author_user_id=current_user.id,
        body=data.body, parent_note_id=data.parent_note_id,
    )
    db.add(note)
    db.flush()

    # Resolve @email mentions against team members (exclude the author).
    mentioned_emails = {e.lower() for e in _MENTION_RE.findall(data.body)}
    mention_targets = []
    if mentioned_emails:
        members = (
            db.query(User.email)
            .join(TeamMembership, TeamMembership.user_id == User.id)
            .filter(TeamMembership.team_id == cm.team_id, User.id != current_user.id)
            .all()
        )
        member_emails = {m[0].lower() for m in members}
        mention_targets = [e for e in mentioned_emails if e in member_emails]

    product_name = cm.product.name if cm.product else "a cost model"
    # Build the response before commit (transaction-local RLS GUCs reset on commit).
    out = NoteOut(
        id=note.id, cost_model_id=note.cost_model_id, author_user_id=note.author_user_id,
        author_name=current_user.display_name or current_user.email,
        parent_note_id=note.parent_note_id, body=note.body, created_at=note.created_at,
    )
    log_event(db, cm.team_id, current_user.id, "create", "cost_model_note", str(note.id),
              new_value={"cost_model_id": str(cm.id), "mentions": mention_targets})
    db.commit()

    # Best-effort mention emails (after commit; never block the write).
    if mention_targets:
        link = f"{settings.app_url}/portfolio/{cm.id}"
        snippet = (data.body[:160] + "…") if len(data.body) > 160 else data.body
        for email in mention_targets:
            try:
                send_mention_email(email, out.author_name, product_name, snippet, link)
            except Exception:
                pass
    return out


@router.delete("/{cost_model_id}/notes/{note_id}")
def delete_note(
    cost_model_id: uuid.UUID,
    note_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = _get_model_or_404(db, cost_model_id)
    note = db.query(CostModelNote).filter(
        CostModelNote.id == note_id, CostModelNote.cost_model_id == cost_model_id
    ).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    # Author can delete own; otherwise needs costing.edit.
    if note.author_user_id != current_user.id:
        require_permission(db, current_user, cm.team_id, "costing.edit")
    else:
        require_permission(db, current_user, cm.team_id, "costing.view")
    log_event(db, cm.team_id, current_user.id, "delete", "cost_model_note", str(note.id))
    db.delete(note)
    db.commit()
    return {"status": "deleted"}


@router.put("/{cost_model_id}/flag", response_model=FlagOut)
def set_flag(
    cost_model_id: uuid.UUID,
    data: FlagUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cm = _get_model_or_404(db, cost_model_id)
    require_permission(db, current_user, cm.team_id, "costing.edit")
    if data.negotiation_state not in NEGOTIATION_STATES:
        raise HTTPException(status_code=422, detail=f"Invalid state. Allowed: {sorted(NEGOTIATION_STATES)}")
    prev = cm.negotiation_state
    cm.negotiation_state = data.negotiation_state
    out = FlagOut(cost_model_id=cm.id, negotiation_state=data.negotiation_state)
    log_event(db, cm.team_id, current_user.id, "update", "cost_model_flag", str(cm.id),
              previous_value={"negotiation_state": prev},
              new_value={"negotiation_state": data.negotiation_state})
    db.commit()
    return out
