"""SCRUM-69 — scheduled seasonal-factor recompute.

The ticket asks for factors "recomputed when the series updates". The scrapers
are what update the series, so this is registered in `celeryconfig.beat_schedule`
immediately after `scrape-all-indexes-weekly` — before the projections, which
also read the series.
"""
from app.tasks import celery_app
from app.database import SessionLocal, bypass_rls_var
from app.services.index_seasonality import recompute_all


@celery_app.task(name="app.tasks.seasonality.recompute_all_seasonality")
def recompute_all_seasonality():
    # Platform-level background job — bypass RLS (same pattern as the scrapers).
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        report = recompute_all(db)
        db.commit()
        return {
            "computed": report.computed,
            "unchanged": report.unchanged,
            "insufficient": report.insufficient,
        }
    finally:
        db.close()
