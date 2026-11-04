import logging

from app.tasks import celery_app
from app.database import SessionLocal, bypass_rls_var
from app.observability import capture_message
from app.services.refresh_log import finish_run, start_run
from app.services.refresh_overview import alert_summary, build_alerts, refresh_overview

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.refresh_check.check_refresh_health")
def check_refresh_health():
    """Daily: report refresh failures and late data (Jira 209 S5).

    Runs after the day's scrapes. The alerts are the same ones the overview
    endpoint shows live; this puts them where someone is looking — the worker
    log and, when configured, Sentry (one warning per day while anything is
    wrong). No email for now.

    The check itself is recorded as a `refresh_check` run whose note carries
    the summary, so "when was this last checked, and what did it find" is
    answerable from `/api/refresh/runs?task=refresh_check`.
    """
    bypass_rls_var.set(True)  # System task — no user context
    db = SessionLocal()
    try:
        run = start_run(db, "refresh_check")
        alerts = build_alerts(refresh_overview(db))
        summary = alert_summary(alerts)
        for a in alerts:
            logger.warning("refresh alert [%s] %s", a.kind, a.message)
        if alerts:
            capture_message(summary + "\n" + "\n".join(a.message for a in alerts), level="warning")
        run.note = summary
        finish_run(db, run)
        return {"alerts": len(alerts), "summary": summary}
    finally:
        db.close()
