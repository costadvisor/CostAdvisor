from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db, bypass_rls_var
from app.models.access_request import PlatformAccessRequest
from app.models.user import User
from app.schemas.access_request import AccessRequestCreate

router = APIRouter()


@router.post("")
def submit_access_request(
    payload: AccessRequestCreate,
    db: Session = Depends(get_db),
):
    """Public endpoint — no auth required. Records a platform access request."""
    email = payload.email.lower().strip()

    bypass_rls_var.set(True)
    try:
        # User with this email already exists — no need to request access
        existing_user = db.query(User).filter(
            User.email == email,
            User.deleted_at == None,  # noqa: E711
        ).first()
        if existing_user:
            return {"status": "exists", "message": "Account already exists. Sign in to continue."}

        # Accepted request already exists
        accepted = db.query(PlatformAccessRequest).filter(
            PlatformAccessRequest.email == email,
            PlatformAccessRequest.status == "accepted",
        ).first()
        if accepted:
            return {"status": "accepted", "message": "Access already granted. Sign in to continue."}

        # Pending request already exists
        pending = db.query(PlatformAccessRequest).filter(
            PlatformAccessRequest.email == email,
            PlatformAccessRequest.status == "pending",
        ).first()
        if pending:
            return {"status": "pending", "message": "Request already submitted. We'll be in touch."}

        req = PlatformAccessRequest(
            email=email,
            name=payload.name,
            company=payload.company,
        )
        db.add(req)
        db.commit()
    finally:
        bypass_rls_var.set(False)

    return {"status": "submitted"}
