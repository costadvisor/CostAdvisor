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
