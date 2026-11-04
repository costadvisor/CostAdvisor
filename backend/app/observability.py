"""Sentry wiring (soft). When `SENTRY_DSN` is set and `sentry-sdk` is
installed, errors are reported with the authenticated user attached. Both
conditions are optional — no-op otherwise, so the app runs fine in dev.

The SDK itself is installed in Phase 13 of DEPLOYMENT.md; this module is the
scaffolding the auth layer and main.py already call into.
"""
from __future__ import annotations

from app.config import get_settings

_settings = get_settings()

try:
    import sentry_sdk  # type: ignore
    from sentry_sdk.integrations.asgi import SentryAsgiMiddleware  # type: ignore
    _SDK_AVAILABLE = True
except ImportError:  # pragma: no cover — SDK not installed yet
    sentry_sdk = None
    SentryAsgiMiddleware = None
    _SDK_AVAILABLE = False


def init_sentry() -> None:
    """Initialize Sentry if configured. Safe to call when SDK not installed."""
    if not _SDK_AVAILABLE or not _settings.sentry_dsn:
        return
    if sentry_sdk.get_client().is_active():
        return  # already set up (the API imports app.tasks too)
    sentry_sdk.init(
        dsn=_settings.sentry_dsn,
        environment=_settings.environment,
        traces_sample_rate=0.1,
        send_default_pii=False,
    )


def set_user_context(user_id: str, email: str) -> None:
    """Attach user to the current Sentry scope. No-op if Sentry isn't active."""
    if not _SDK_AVAILABLE or not _settings.sentry_dsn:
        return
    sentry_sdk.set_user({"id": user_id, "email": email})


def clear_user_context() -> None:
    if not _SDK_AVAILABLE or not _settings.sentry_dsn:
        return
    sentry_sdk.set_user(None)


def capture_message(message: str, level: str = "info") -> None:
    """Send a message (not an exception) to Sentry, e.g. the daily refresh
    alerts (Jira 209). No-op if Sentry isn't active."""
    if not _SDK_AVAILABLE or not _settings.sentry_dsn:
        return
    sentry_sdk.capture_message(message, level=level)
