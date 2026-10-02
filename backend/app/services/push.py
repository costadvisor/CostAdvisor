"""Sending a web push (PWA extras).

Free by construction: the browser vendors' push relays are used by the browser
itself once `pushManager.subscribe()` runs. There is no paid service, no
Firebase project and no API key — only a VAPID key pair identifying us to the
relay.

Delivery is best-effort and never blocks the write that triggered it, exactly
like `services/email.py`. A push that fails must not roll back the alert it was
announcing.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.push import PushSubscription

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard, same shape as the Sentry one in main.py
    from pywebpush import WebPushException, webpush
    _AVAILABLE = True
except ImportError:  # pragma: no cover
    webpush = None
    WebPushException = Exception
    _AVAILABLE = False

# The relay says the subscription is gone. Not a transient failure: retrying it
# forever is how a table fills with browsers that were uninstalled months ago.
DEAD_STATUSES = {404, 410}


def push_configured() -> bool:
    return bool(_AVAILABLE and get_settings().vapid_public_key and get_settings().vapid_private_key)


def _claims() -> dict:
    # The relay requires a contact for the sender. `mailto:` is the convention.
    return {"sub": f"mailto:{get_settings().vapid_contact_email}"}


def send_to_subscription(db: Session, sub: PushSubscription, payload: dict) -> bool:
    """One device. Returns True if the relay accepted it.

    A dead subscription is deleted rather than retried, and that deletion is
    the only reason this takes a session.
    """
    if not push_configured():
        return False
    try:
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh_key, "auth": sub.auth_key},
            },
            data=json.dumps(payload),
            vapid_private_key=get_settings().vapid_private_key,
            vapid_claims=_claims(),
            timeout=10,
        )
    except WebPushException as exc:  # pragma: no cover - network path
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in DEAD_STATUSES:
            logger.info("Pruning dead push subscription %s (%s)", sub.id, status)
            db.delete(sub)
        else:
            logger.warning("Push delivery failed for %s: %s", sub.id, exc)
        return False
    except Exception as exc:  # pragma: no cover - best-effort by design
        logger.warning("Push delivery errored for %s: %s", sub.id, exc)
        return False

    sub.last_delivered_at = datetime.now(timezone.utc)
    return True


def send_to_user(db: Session, user_id, title: str, body: str, url: str | None = None) -> int:
    """Every device this user has registered. Returns how many were accepted.

    `url` rides on the payload so the service worker's notificationclick
    handler can focus-or-open the page the alert is actually about — a
    notification that opens the dashboard when it was about one product makes
    the reader do the finding.
    """
    if not push_configured():
        return 0
    subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
    payload = {"title": title, "body": body, "url": url or "/"}
    return sum(1 for s in subs if send_to_subscription(db, s, payload))
