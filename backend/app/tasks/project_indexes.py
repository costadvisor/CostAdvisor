from app.tasks import celery_app
from app.database import SessionLocal, bypass_rls_var
from app.services.index_projection import project_all_series, DEFAULT_HORIZON_QUARTERS


@celery_app.task(name="app.tasks.project_indexes.project_all")
def project_all(horizon_quarters: int = DEFAULT_HORIZON_QUARTERS):
    """Refresh the projection vintage for every (commodity, region) pair that
    has at least one IndexValue row. Scheduled after the weekly scrape jobs
    so each fit uses that week's freshest actuals."""
    bypass_rls_var.set(True)  # System task — no user context
    db = SessionLocal()
    try:
        return project_all_series(db, horizon_quarters=horizon_quarters)
    finally:
        db.close()
