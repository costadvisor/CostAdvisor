import logging

from app.tasks import celery_app
from app.database import SessionLocal, bypass_rls_var
from app.observability import capture_message
from app.services.refresh_log import finish_run, start_run
from app.services.staleness import check

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.staleness_check.check_staleness")
def check_staleness():
    """Daily: mark report sections, panels, levers and blocks that are out of
    date, and say why (LIVE-5).

    Recorded as a `staleness_check` run beside the refresh runs (Jira 209), so
    `/api/refresh/runs?task=staleness_check` shows when it last ran and what it
    found; newly stale text is reported to the worker log and, when
    configured, Sentry. No email for now.
    """
    bypass_rls_var.set(True)  # System task — no user context
    db = SessionLocal()
    try:
        run = start_run(db, "staleness_check")
        report = check(db)
        summary = report.summary()
        db.commit()
        if report.newly_stale:
            logger.warning("staleness: %s", summary)
            capture_message(f"LIVE-5 staleness: {summary}", level="warning")
        else:
            logger.info("staleness: %s", summary)
        run.note = summary
        finish_run(db, run)
        return {"stale": sum(report.stale.values()), "newly_stale": report.newly_stale,
                "summary": summary}
    finally:
        db.close()
