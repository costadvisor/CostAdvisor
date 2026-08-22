from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

broker_url = settings.redis_url
result_backend = settings.redis_url
task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]
timezone = "UTC"

# Beat schedule
beat_schedule = {
    "scrape-all-indexes-weekly": {
        "task": "app.tasks.scrape_indexes.scrape_all",
        "schedule": crontab(hour=6, minute=0, day_of_week=1),  # Monday 06:00 UTC
    },
    "scrape-team-sources-weekly": {
        "task": "app.tasks.scrape_indexes.scrape_team_sources",
        "schedule": crontab(hour=6, minute=15, day_of_week=1),  # Monday 06:15 UTC
    },
    "scrape-fx-live-daily": {
        "task": "app.tasks.scrape_indexes.scrape_fx_live",
        "schedule": crontab(hour=8, minute=0),  # Daily 08:00 UTC
    },
}
