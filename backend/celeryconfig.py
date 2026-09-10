from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

broker_url = settings.redis_url
result_backend = settings.redis_url
task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]
timezone = "UTC"

# Task modules the worker must import.
#
# `autodiscover_tasks(["app.tasks"])` in app/tasks/__init__.py looks for an
# `app.tasks.tasks` submodule, which does not exist — so nothing was ever
# autodiscovered and every task was registered only as a side effect of some
# request path importing its module. A beat entry for a task the worker never
# imported fails at dispatch, so this is listed explicitly alongside adding the
# alert entry below.
imports = (
    "app.tasks.scrape_indexes",
    "app.tasks.project_indexes",
    "app.tasks.alerts",
    "app.tasks.radar",
    "app.tasks.seasonality",
)

# Beat schedule
beat_schedule = {
    "scrape-all-indexes-weekly": {
        "task": "app.tasks.scrape_indexes.scrape_all",
        "schedule": crontab(hour=6, minute=0, day_of_week=1),  # Monday 06:00 UTC
    },
    # SCRUM-69 — factors are recomputed *when the series updates*, and the
    # scrapes are what update it. Runs before the projections, which read the
    # same series.
    "recompute-seasonality-weekly": {
        "task": "app.tasks.seasonality.recompute_all_seasonality",
        "schedule": crontab(hour=6, minute=45, day_of_week=1),  # Monday 06:45 UTC
    },
    "scrape-team-sources-weekly": {
        "task": "app.tasks.scrape_indexes.scrape_team_sources",
        "schedule": crontab(hour=6, minute=15, day_of_week=1),  # Monday 06:15 UTC
    },
    "fetch-provider-sources-weekly": {
        "task": "app.tasks.scrape_indexes.fetch_provider_sources",
        "schedule": crontab(hour=6, minute=30, day_of_week=1),  # Monday 06:30 UTC
    },
    "project-all-indexes-weekly": {
        "task": "app.tasks.project_indexes.project_all",
        "schedule": crontab(hour=7, minute=0, day_of_week=1),  # Monday 07:00 UTC — after the scrapes, before FX
    },
    # SCRUM-79 — `evaluate_all_alerts` shipped with Scrum 24 and was never
    # registered here, so alerts have only ever fired when somebody POSTed the
    # on-demand endpoint. The radar runs first; alert delivery reads the windows
    # it produced, so the order matters.
    "run-trigger-radar-daily": {
        "task": "app.tasks.radar.run_all_radars",
        "schedule": crontab(hour=7, minute=30),  # Daily 07:30 UTC — after the projections
    },
    "evaluate-alerts-daily": {
        "task": "app.tasks.alerts.evaluate_all_alerts",
        "schedule": crontab(hour=7, minute=45),  # Daily 07:45 UTC — after the radar
    },
    "scrape-fx-live-daily": {
        "task": "app.tasks.scrape_indexes.scrape_fx_live",
        "schedule": crontab(hour=8, minute=0),  # Daily 08:00 UTC
    },
}
