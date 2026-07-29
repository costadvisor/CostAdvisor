"""Scrum 24 — scheduled alert evaluation.

`evaluate_all_alerts` walks every team with alerts and fires new ones. Wire it
into the Celery beat schedule (celeryconfig) to run e.g. daily; it is also
invocable on demand via POST /api/alerts/evaluate."""
from app.tasks import celery_app
from app.database import SessionLocal, bypass_rls_var
from app.models.team import Team
from app.services.alerts import evaluate_team_alerts
# Side-effect import: region auto-register listener (some triggers touch index data).
from app.services import regions as _region_events  # noqa: F401


@celery_app.task(name="app.tasks.alerts.evaluate_all_alerts")
def evaluate_all_alerts():
    # Platform-level background job — bypass RLS (same pattern as the scrapers).
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        team_ids = [t[0] for t in db.query(Team.id).all()]
        total = 0
        for tid in team_ids:
            total += len(evaluate_team_alerts(db, tid))
        return {"teams": len(team_ids), "alerts_created": total}
    finally:
        db.close()
