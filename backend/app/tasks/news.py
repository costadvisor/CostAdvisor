"""SCRUM-214 — the monthly news sweep, and the "Run now" button behind it.

Same shape as the radar task: a platform-level background job, so it bypasses
RLS and opens its own session. The schedule (celeryconfig) fires on the 1st of
each month, but the task does nothing unless NEWS_SWEEP_ENABLED is on, so an
environment never spends money on news it did not ask for.
"""
import logging
import uuid

from app.tasks import celery_app
from app.config import get_settings
from app.database import SessionLocal, bypass_rls_var
from app.models.news import NewsRun
from app.services.news_sweep import SweepAlreadyRunning, execute_run, run_sweep

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.news.monthly_news_sweep")
def monthly_news_sweep():
    if not get_settings().news_sweep_enabled:
        return {"skipped": "NEWS_SWEEP_ENABLED is off"}
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        run = run_sweep(db, trigger="scheduled")
        return {"run_id": str(run.id), "status": run.status, "new_items": run.items_new,
                "cost_usd": run.cost_usd}
    except SweepAlreadyRunning as e:
        logger.warning("monthly news sweep skipped: %s", e)
        return {"skipped": str(e)}
    finally:
        db.close()


@celery_app.task(name="app.tasks.news.execute_news_run")
def execute_news_run(run_id: str):
    """Carry out a run the API already created (POST /api/news/runs)."""
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        run = db.get(NewsRun, uuid.UUID(run_id))
        if run is None:
            return {"error": "run not found"}
        run = execute_run(db, run)
        return {"run_id": run_id, "status": run.status}
    finally:
        db.close()
