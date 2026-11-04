from celery import Celery

from app.config import get_settings
from app.observability import init_sentry

settings = get_settings()

# The worker is its own process, so main.py's init_sentry() never ran here and
# task errors went unreported (Jira 209). sentry-sdk enables its Celery
# integration on its own once initialised. No-op without SENTRY_DSN.
init_sentry()

celery = Celery("costadvisor")
celery.config_from_object("celeryconfig")
celery.autodiscover_tasks(["app.tasks"])

# Alias used by task modules
celery_app = celery
