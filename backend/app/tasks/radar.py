"""SCRUM-79 — scheduled trigger-radar runs.

Registered in `celeryconfig.beat_schedule` *before* `evaluate_all_alerts`:
alert delivery reads the windows this produces, so a radar that ran after
delivery would always be one day behind.
"""
from app.tasks import celery_app
from app.database import SessionLocal, bypass_rls_var
from app.models.team import Team
from app.services.trigger_radar import run_radar
# Side-effect import: region auto-register listener (market signals carry a region).
from app.services import regions as _region_events  # noqa: F401


@celery_app.task(name="app.tasks.radar.run_all_radars")
def run_all_radars():
    # Platform-level background job — bypass RLS (same pattern as the scrapers).
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        team_ids = [t[0] for t in db.query(Team.id).all()]
        totals = {"opened": 0, "refreshed": 0, "closed": 0}
        for tid in team_ids:
            summary = run_radar(db, tid).summary
            for k in totals:
                totals[k] += summary[k]
        return {"teams": len(team_ids), **totals}
    finally:
        db.close()
