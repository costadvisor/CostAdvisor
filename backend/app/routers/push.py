"""Web-push subscriptions (PWA extras).

    GET    /api/push/config             is push available, and the public key
    GET    /api/push/subscriptions      this user's devices
    POST   /api/push/subscriptions      register this device
    DELETE /api/push/subscriptions/{id} unregister one
    POST   /api/push/test               send this user a test notification

Every read and write is filtered by `current_user.id`. A subscription belongs
to a person's device, not to a team, so there is no team parameter anywhere
here and an id from someone else's account simply does not match.

The public key is served rather than baked into the frontend build: it is
public by definition, and serving it means rotating the pair does not need a
rebuild and a redeploy of the SPA.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.push import PushSubscription
from app.models.user import User
from app.routers.auth import get_current_user
from app.services.push import push_configured, send_to_user

router = APIRouter()


class SubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=1)
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)
    user_agent: str | None = Field(None, max_length=255)


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    # The endpoint is a capability URL — anyone holding it can push to that
    # browser — so it is never returned. The UI identifies a device by its
    # user agent and dates instead.
    user_agent: str | None = None
    created_at: object
    last_delivered_at: object | None = None

    class Config:
        from_attributes = True


@router.get("/config")
def config(current_user: User = Depends(get_current_user)):
    """What the browser needs before it can subscribe."""
    return {
        "enabled": push_configured(),
        "public_key": get_settings().vapid_public_key if push_configured() else None,
    }


@router.get("/subscriptions", response_model=list[SubscriptionOut])
def list_subscriptions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(PushSubscription)
        .filter(PushSubscription.user_id == current_user.id)
        .order_by(PushSubscription.created_at.desc())
        .all()
    )
    return [SubscriptionOut.model_validate(r) for r in rows]


@router.post("/subscriptions", response_model=SubscriptionOut, status_code=201)
def subscribe(
    payload: SubscriptionIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Register this browser.

    Upsert on the endpoint: re-subscribing the same browser returns the same
    endpoint, and inserting a row per visit would notify that device once per
    row. If the endpoint is already registered to a different user (a shared
    machine where someone else signed in), it moves — the browser can only
    belong to whoever is holding it now.
    """
    existing = (
        db.query(PushSubscription)
        .filter(PushSubscription.endpoint == payload.endpoint)
        .first()
    )
    if existing:
        existing.user_id = current_user.id
        existing.p256dh_key = payload.p256dh
        existing.auth_key = payload.auth
        existing.user_agent = payload.user_agent
        sub = existing
    else:
        sub = PushSubscription(
            user_id=current_user.id, endpoint=payload.endpoint,
            p256dh_key=payload.p256dh, auth_key=payload.auth,
            user_agent=payload.user_agent,
        )
        db.add(sub)
    db.flush()
    out = SubscriptionOut.model_validate(sub)
    db.commit()
    return out


@router.delete("/subscriptions/{subscription_id}", status_code=204)
def unsubscribe(
    subscription_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = (
        db.query(PushSubscription)
        .filter(
            PushSubscription.id == subscription_id,
            # Ownership is in the filter, not in a check after the fetch: a
            # row belonging to somebody else is simply not found.
            PushSubscription.user_id == current_user.id,
        )
        .first()
    )
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    db.delete(sub)
    db.commit()


class UnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=1)


@router.post("/unsubscribe", status_code=204)
def unsubscribe_by_endpoint(
    payload: UnsubscribeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove the row for a browser that has just unsubscribed itself.

    Exists because the endpoint is deliberately never returned by the list
    endpoint — it is a capability URL, and anyone holding it can push to that
    browser. The client already has its OWN endpoint, so handing it back is
    safe; matching on it saves either leaking every endpoint or leaving a
    permanent ghost row once the browser has dropped its subscription.
    """
    sub = (
        db.query(PushSubscription)
        .filter(
            PushSubscription.endpoint == payload.endpoint,
            PushSubscription.user_id == current_user.id,
        )
        .first()
    )
    if sub:
        db.delete(sub)
        db.commit()
    # 204 either way: the caller's goal is "this browser is not registered",
    # and it already is not.


@router.post("/test")
def send_test(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Prove the round trip actually reaches the device.

    Worth having as a real endpoint: subscribing can succeed while delivery
    silently never arrives — on iOS, notably, push only reaches an installed
    PWA — and a toast saying "subscribed" is not evidence of that.
    """
    if not push_configured():
        raise HTTPException(status_code=400, detail="Push is not configured on this server")
    delivered = send_to_user(
        db, current_user.id,
        title="CostAdvisor",
        body="Push notifications are working on this device.",
        url="/profile",
    )
    db.commit()
    return {"delivered": delivered}
