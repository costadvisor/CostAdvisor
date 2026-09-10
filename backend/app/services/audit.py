"""Audit trail logging service."""
import uuid
from sqlalchemy.orm import Session
from app.models.audit_log import AuditLog
from app.models.auth_event import AuthEvent
from app.database import impersonating_admin_email_var


def log_event(
    db: Session,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    event_type: str,
    entity_type: str,
    entity_id: str,
    previous_value: dict | None = None,
    new_value: dict | None = None,
):
    # Tag the entry when the action was performed during an impersonation session.
    admin_email = impersonating_admin_email_var.get()
    if admin_email:
        new_value = {**(new_value or {}), "_impersonated_by": admin_email}

    entry = AuditLog(
        team_id=team_id,
        user_id=user_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=str(entity_id),
        previous_value=previous_value,
        new_value=new_value,
    )
    db.add(entry)


def log_auth_event(
    db: Session,
    email: str,
    event_type: str,
    user_id: uuid.UUID | None = None,
    reason: str | None = None,
    request=None,
):
    """Scrum 10 — login/logout trail. Platform-level (no team_id): a login or a
    rejected signup attempt has no team yet, and `audit_logs.team_id`/`user_id`
    are NOT NULL so it can't live there. Caller is responsible for `db.commit()`."""
    ip_address = None
    user_agent = None
    if request is not None:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
    db.add(AuthEvent(
        user_id=user_id,
        email=email,
        event_type=event_type,
        reason=reason,
        ip_address=ip_address,
        user_agent=user_agent,
    ))


# A platform-grain audit row carries no team. `audit_logs.team_id` was NOT NULL
# with an FK to teams, so callers on platform data had two bad options: borrow a
# tenant, or use a nil-UUID sentinel that violates the FK and silently loses the
# row at commit. SCRUM-78 makes the column nullable and NULL means "no tenant".
PLATFORM_TEAM_SENTINEL = None


def log_platform_event(
    db: Session,
    user_id: uuid.UUID,
    event_type: str,
    entity_type: str,
    entity_id: str,
    previous_value: dict | None = None,
    new_value: dict | None = None,
) -> bool:
    """Audit an action on platform data without borrowing a tenant.

    The pattern this replaces picked *the first team the actor happens to belong
    to* — putting the event in an unrelated tenant's log — and skipped it
    entirely for an actor with no team.

    Flushed here rather than left to the caller's commit, so an audit problem
    surfaces where it can be caught: a NOT NULL or FK violation raised at commit
    would take the action it was recording down with it.

    Returns whether the row was written.
    """
    try:
        log_event(db, None, user_id, event_type, entity_type, entity_id,
                  previous_value=previous_value, new_value=new_value)
        db.flush()
        return True
    except Exception:
        db.rollback()
        return False
