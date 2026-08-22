import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.invite import TeamInvite
from app.models.team import Team, TeamMembership
from app.routers.auth import get_current_user
from app.schemas.invite import PendingInviteOut
from app.services.audit import log_event

router = APIRouter()


def _invite_to_pending_out(invite: TeamInvite) -> PendingInviteOut:
    inviter = invite.invited_by
    return PendingInviteOut(
        id=invite.id,
        token=invite.token,
        team_id=invite.team_id,
        team_name=invite.team.name if invite.team else str(invite.team_id),
        role=invite.role,
        invited_by_name=inviter.display_name if inviter else None,
        invited_by_email=inviter.email if inviter else "",
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        status=invite.status,
        accepted_at=invite.accepted_at,
    )


@router.get("/pending", response_model=list[PendingInviteOut])
def list_pending_invites(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    invites = (
        db.query(TeamInvite)
        .filter(
            TeamInvite.invited_email == current_user.email,
            TeamInvite.status == "pending",
            TeamInvite.expires_at > now,
        )
        .all()
    )
    return [_invite_to_pending_out(i) for i in invites]


@router.get("/history", response_model=list[PendingInviteOut])
def list_invite_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the last 30 non-pending invites (accepted, declined, revoked, expired) for the current user."""
    now = datetime.now(timezone.utc)
    invites = (
        db.query(TeamInvite)
        .filter(TeamInvite.invited_email == current_user.email)
        .filter(
            (TeamInvite.status != "pending") |
            (TeamInvite.expires_at <= now)
        )
        .order_by(TeamInvite.created_at.desc())
        .limit(30)
        .all()
    )
    return [_invite_to_pending_out(i) for i in invites]


@router.post("/{token}/accept")
def accept_invite(
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invite = db.query(TeamInvite).filter(TeamInvite.token == token).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.status != "pending":
        raise HTTPException(status_code=400, detail="Invite is no longer valid")
    if invite.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invite has expired")
    if invite.invited_email.lower() != current_user.email.lower():
        raise HTTPException(status_code=403, detail="This invite was sent to a different email address")

    existing = db.query(TeamMembership).filter(
        TeamMembership.user_id == current_user.id,
        TeamMembership.team_id == invite.team_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="You are already a member of this team")

    membership = TeamMembership(user_id=current_user.id, team_id=invite.team_id, role=invite.role)
    db.add(membership)

    invite.status = "accepted"
    invite.accepted_at = datetime.now(timezone.utc)

    log_event(db, invite.team_id, current_user.id, "invite_accepted", "team_member",
              str(current_user.id),
              new_value={"email": current_user.email, "role": invite.role})
    db.commit()

    team = db.query(Team).filter(Team.id == invite.team_id).first()
    return {"team_id": str(invite.team_id), "team_name": team.name if team else "", "role": invite.role}


@router.post("/{token}/decline")
def decline_invite(
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invite = db.query(TeamInvite).filter(TeamInvite.token == token).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.status != "pending":
        raise HTTPException(status_code=400, detail="Invite is no longer valid")
    if invite.invited_email.lower() != current_user.email.lower():
        raise HTTPException(status_code=403, detail="This invite was sent to a different email address")

    invite.status = "declined"
    db.commit()
    return {"status": "declined"}
