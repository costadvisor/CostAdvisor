from contextvars import ContextVar

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session as SASession

from app.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# RLS context: set by get_current_user (per-request) or by system tasks that
# need to bypass RLS (Celery scrape tasks, seed scripts, migrations). Read by
# the after_begin listener below, which issues SET LOCAL on each new
# transaction so the active Postgres row-level-security policies evaluate
# against the correct identity.
current_user_id_var: ContextVar[str | None] = ContextVar("current_user_id", default=None)
bypass_rls_var: ContextVar[bool] = ContextVar("bypass_rls", default=False)
# Set to the admin's email when a request is executing on behalf of an impersonated user.
impersonating_admin_email_var: ContextVar[str | None] = ContextVar("impersonating_admin_email", default=None)


@event.listens_for(SASession, "after_begin")
def _apply_rls_context(session, transaction, connection):
    """On every new transaction, set Postgres GUCs used by RLS policies."""
    if bypass_rls_var.get():
        connection.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
    user_id = current_user_id_var.get()
    if user_id:
        connection.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_id)},
        )


def get_db():
    """FastAPI dependency that provides a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
