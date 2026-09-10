"""In-app support system.

Mounted at /api/support. Any team member can open a thread against their own
team ("person" scope by default — only they + staff see it; "team" scope
makes it visible to the whole team) and post replies to their own thread.
Staff (super_admin or the platform "Support Agent" role, via `support.reply`)
see and reply to every team's threads and manage the canned-response library
+ FAQ. RLS enforces the team boundary; "person" scope is an app-layer filter
on top of that, following the AlertSubscription precedent (see the migration
docstring) — there is no per-user RLS anywhere in this codebase.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.team import Team, TeamMembership
from app.models.rbac import Role, RolePermission, UserPlatformRole
from app.models.support import SupportThread, SupportMessage, SupportCannedResponse, SupportFAQ
from app.routers.auth import get_current_user
from app.schemas.support import (
    ThreadCreate, MessageCreate, ThreadStatusUpdate, ThreadOut, ThreadDetailOut, MessageOut,
    CannedResponseCreate, CannedResponseOut, FAQCreate, FAQOut,
)
from app.services.audit import log_event
from app.services.email import send_support_message_email, send_support_reply_email
from app.services.permissions import has_platform_permission, require_platform_permission

router = APIRouter()

VALID_STATUSES = ("open", "pending", "resolved", "closed")


def _is_staff(db: Session, user: User) -> bool:
    return user.is_super_admin or has_platform_permission(db, user, "support.reply")


def _require_staff(db: Session, user: User) -> None:
    if user.is_super_admin:
        return
    require_platform_permission(db, user, "support.reply")


def _require_member(db: Session, user: User, team_id: uuid.UUID) -> None:
    if user.is_super_admin:
        return
    row = db.query(TeamMembership).filter(
        TeamMembership.team_id == team_id, TeamMembership.user_id == user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this team")


def _get_thread_or_404(db: Session, thread_id: uuid.UUID) -> SupportThread:
    t = db.query(SupportThread).filter(SupportThread.id == thread_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Thread not found")
    return t


def _authorize_thread_access(db: Session, user: User, thread: SupportThread) -> None:
    """Team RLS already guarantees the caller is on the thread's team (or is
    staff, who bypass RLS via the same `is_super_admin` short-circuit every
    other admin read uses). This is the 'person' scope narrowing on top."""
    if _is_staff(db, user):
        return
    if thread.user_id == user.id:
        return
    if thread.scope == "team":
        _require_member(db, user, thread.team_id)
        return
    raise HTTPException(status_code=403, detail="This thread is private to its creator and support staff")


def _staff_emails(db: Session) -> list[str]:
    """Everyone who can act on a thread: super admins + the Support Agent
    platform role. Best-effort notification target, not an access check."""
    admins = db.query(User.email).filter(User.is_super_admin.is_(True)).all()
    agents = (
        db.query(User.email)
        .join(UserPlatformRole, UserPlatformRole.user_id == User.id)
        .join(Role, Role.id == UserPlatformRole.role_id)
        .filter(Role.team_id.is_(None), Role.name == "Support Agent")
        .all()
    )
    return sorted({e for (e,) in admins + agents})


def _user_names(db: Session, ids: set) -> dict:
    if not ids:
        return {}
    rows = db.query(User.id, User.display_name, User.email).filter(User.id.in_(ids)).all()
    return {r[0]: (r[1] or r[2]) for r in rows}


def _thread_out(db: Session, t: SupportThread, names: dict, team_names: dict, count: int) -> ThreadOut:
    return ThreadOut(
        id=t.id, team_id=t.team_id, team_name=team_names.get(t.team_id),
        user_id=t.user_id, user_name=names.get(t.user_id), scope=t.scope, subject=t.subject,
        status=t.status, assigned_admin_id=t.assigned_admin_id,
        created_at=t.created_at, updated_at=t.updated_at, message_count=count,
    )


@router.get("/is-staff")
def is_staff(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """So the frontend can show the staff-only Support console entry point —
    there's no platform-roles list on /auth/me's user payload to check client-side."""
    return {"is_staff": _is_staff(db, current_user)}


@router.get("/threads", response_model=list[ThreadOut])
def list_threads(
    team_id: uuid.UUID | None = None,
    mine: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Staff with no team_id given see every team's threads. A regular user
    (or `mine=true`) always sees only threads they can access: their own,
    plus any 'team'-scope thread on a team they belong to."""
    staff = _is_staff(db, current_user)
    q = db.query(SupportThread)
    if team_id is not None:
        _require_member(db, current_user, team_id)
        q = q.filter(SupportThread.team_id == team_id)
    elif not staff or mine:
        my_team_ids = [m.team_id for m in db.query(TeamMembership.team_id).filter(
            TeamMembership.user_id == current_user.id).all()]
        q = q.filter(SupportThread.team_id.in_(my_team_ids))

    threads = q.order_by(SupportThread.updated_at.desc()).all()
    if not staff or mine:
        threads = [t for t in threads if t.user_id == current_user.id or t.scope == "team"]

    counts = {}
    if threads:
        rows = (
            db.query(SupportMessage.thread_id, SupportMessage.id)
            .filter(SupportMessage.thread_id.in_([t.id for t in threads])).all()
        )
        for tid, _ in rows:
            counts[tid] = counts.get(tid, 0) + 1

    names = _user_names(db, {t.user_id for t in threads})
    team_rows = db.query(Team.id, Team.name).filter(Team.id.in_({t.team_id for t in threads})).all()
    team_names = dict(team_rows)
    return [_thread_out(db, t, names, team_names, counts.get(t.id, 0)) for t in threads]


@router.post("/threads", response_model=ThreadDetailOut)
def create_thread(
    body: ThreadCreate,
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_member(db, current_user, team_id)
    if body.scope not in ("person", "team"):
        raise HTTPException(status_code=422, detail="scope must be 'person' or 'team'")

    thread = SupportThread(team_id=team_id, user_id=current_user.id, scope=body.scope, subject=body.subject)
    db.add(thread)
    db.flush()
    msg = SupportMessage(team_id=team_id, thread_id=thread.id, author_id=current_user.id,
                          is_staff=False, body=body.body)
    db.add(msg)
    log_event(db, team_id, current_user.id, "support_thread_created", "support_thread", str(thread.id),
              new_value={"subject": body.subject, "scope": body.scope})
    db.commit()
    db.refresh(thread)
    db.refresh(msg)

    for staff_email in _staff_emails(db):
        try:
            send_support_message_email(staff_email, current_user.display_name or current_user.email,
                                        body.subject, body.body)
        except Exception:
            pass

    author_name = current_user.display_name or current_user.email
    return ThreadDetailOut(
        id=thread.id, team_id=thread.team_id, team_name=None, user_id=thread.user_id, user_name=author_name,
        scope=thread.scope, subject=thread.subject, status=thread.status,
        assigned_admin_id=thread.assigned_admin_id, created_at=thread.created_at, updated_at=thread.updated_at,
        message_count=1,
        messages=[MessageOut(id=msg.id, author_id=msg.author_id, author_name=author_name, is_staff=False,
                              body=msg.body, canned_response_id=None, created_at=msg.created_at)],
    )


@router.get("/threads/{thread_id}", response_model=ThreadDetailOut)
def get_thread(
    thread_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thread = _get_thread_or_404(db, thread_id)
    _authorize_thread_access(db, current_user, thread)
    messages = (
        db.query(SupportMessage).filter(SupportMessage.thread_id == thread_id)
        .order_by(SupportMessage.created_at.asc()).all()
    )
    names = _user_names(db, {thread.user_id} | {m.author_id for m in messages})
    team = db.query(Team.name).filter(Team.id == thread.team_id).scalar()
    out = _thread_out(db, thread, names, {thread.team_id: team}, len(messages))
    return ThreadDetailOut(
        **out.model_dump(),
        messages=[
            MessageOut(id=m.id, author_id=m.author_id, author_name=names.get(m.author_id), is_staff=m.is_staff,
                       body=m.body, canned_response_id=m.canned_response_id, created_at=m.created_at)
            for m in messages
        ],
    )


@router.post("/threads/{thread_id}/messages", response_model=MessageOut)
def post_message(
    thread_id: uuid.UUID,
    body: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thread = _get_thread_or_404(db, thread_id)
    _authorize_thread_access(db, current_user, thread)
    staff = _is_staff(db, current_user)

    msg = SupportMessage(
        team_id=thread.team_id, thread_id=thread.id, author_id=current_user.id, is_staff=staff,
        body=body.body, canned_response_id=body.canned_response_id,
    )
    db.add(msg)
    # A staff reply reopens a resolved/closed thread's attention; a user
    # reply on a thread staff marked "pending" (waiting on the user) puts it
    # back in the staff queue as "open".
    thread.status = "pending" if staff else "open"
    if staff and thread.assigned_admin_id is None:
        thread.assigned_admin_id = current_user.id
    log_event(db, thread.team_id, current_user.id, "support_message_posted", "support_thread", str(thread.id),
              new_value={"is_staff": staff})
    db.commit()
    db.refresh(msg)

    if staff:
        requester = db.query(User).filter(User.id == thread.user_id).first()
        if requester:
            try:
                send_support_reply_email(requester.email, thread.subject, body.body)
            except Exception:
                pass
    else:
        for staff_email in _staff_emails(db):
            try:
                send_support_message_email(staff_email, current_user.display_name or current_user.email,
                                            thread.subject, body.body)
            except Exception:
                pass

    return MessageOut(id=msg.id, author_id=msg.author_id, author_name=current_user.display_name or current_user.email,
                       is_staff=staff, body=msg.body, canned_response_id=msg.canned_response_id,
                       created_at=msg.created_at)


@router.put("/threads/{thread_id}/status", response_model=ThreadOut)
def update_status(
    thread_id: uuid.UUID,
    body: ThreadStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_staff(db, current_user)
    thread = _get_thread_or_404(db, thread_id)
    if body.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {VALID_STATUSES}")
    thread.status = body.status
    log_event(db, thread.team_id, current_user.id, "support_thread_status_changed", "support_thread",
              str(thread.id), new_value={"status": body.status})
    db.commit()
    names = _user_names(db, {thread.user_id})
    team = db.query(Team.name).filter(Team.id == thread.team_id).scalar()
    return _thread_out(db, thread, names, {thread.team_id: team}, 0)


# ── Canned responses (staff-managed, platform-wide) ─────────────────────────

@router.get("/canned-responses", response_model=list[CannedResponseOut])
def list_canned_responses(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_staff(db, current_user)
    return db.query(SupportCannedResponse).order_by(SupportCannedResponse.category, SupportCannedResponse.title).all()


@router.post("/canned-responses", response_model=CannedResponseOut)
def create_canned_response(
    body: CannedResponseCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    _require_staff(db, current_user)
    row = SupportCannedResponse(title=body.title, body=body.body, category=body.category,
                                 created_by=current_user.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/canned-responses/{response_id}", response_model=CannedResponseOut)
def update_canned_response(
    response_id: uuid.UUID, body: CannedResponseCreate,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    _require_staff(db, current_user)
    row = db.query(SupportCannedResponse).filter(SupportCannedResponse.id == response_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Canned response not found")
    row.title, row.body, row.category = body.title, body.body, body.category
    db.commit()
    db.refresh(row)
    return row


@router.delete("/canned-responses/{response_id}")
def delete_canned_response(
    response_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    _require_staff(db, current_user)
    row = db.query(SupportCannedResponse).filter(SupportCannedResponse.id == response_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Canned response not found")
    db.delete(row)
    db.commit()
    return {"status": "deleted"}


# ── FAQ / knowledge base (staff-managed, read by everyone) ──────────────────

@router.get("/faq", response_model=list[FAQOut])
def list_faq(
    category: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(SupportFAQ).filter(SupportFAQ.published.is_(True))
    if category:
        q = q.filter(SupportFAQ.category == category)
    return q.order_by(SupportFAQ.category, SupportFAQ.sort_order).all()


@router.get("/faq/admin", response_model=list[FAQOut])
def list_faq_admin(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Unpublished entries included — staff-only management view."""
    _require_staff(db, current_user)
    return db.query(SupportFAQ).order_by(SupportFAQ.category, SupportFAQ.sort_order).all()


@router.post("/faq", response_model=FAQOut)
def create_faq(body: FAQCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_staff(db, current_user)
    row = SupportFAQ(**body.model_dump(), created_by=current_user.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/faq/{faq_id}", response_model=FAQOut)
def update_faq(
    faq_id: uuid.UUID, body: FAQCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    _require_staff(db, current_user)
    row = db.query(SupportFAQ).filter(SupportFAQ.id == faq_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="FAQ entry not found")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/faq/{faq_id}")
def delete_faq(faq_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_staff(db, current_user)
    row = db.query(SupportFAQ).filter(SupportFAQ.id == faq_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="FAQ entry not found")
    db.delete(row)
    db.commit()
    return {"status": "deleted"}
