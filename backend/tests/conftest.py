"""Shared fixtures. Each test gets fresh users and teams with random UUIDs and
tears them down (team CASCADE wipes products, cost_models, overrides, etc.).

RLS is live — fixtures use `bypass_rls_var.set(True)` during setup/teardown.

**The suite runs against its own database**, defaulting to the app database's
name with `_test` appended. It used to share the app's database, and because the
cleanup helpers here `commit()` rather than roll back, that repeatedly damaged
real data: a platform market signal and a platform dimension alias leaked across
tenants, a `kind`-scoped repair deleted 141 live industry assertions, and
`test_index_dossier` left the *active* volatility calibration pointing at a
ladder fitted to synthetic fixtures — so every percentile the app served came
from test data.

Redirect happens before any app import, because `app.database` builds its engine
at import time from `get_settings()`. The test database is then brought up to
head automatically — it was created as a template copy, so it does not otherwise
move when a migration lands, and the resulting failures look like broken tests
rather than a stale schema.
"""
from __future__ import annotations

import os
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine, text


def _point_at_the_test_database() -> str:
    """Redirect `DATABASE_URL` at the test database, or fail loudly.

    There is deliberately **no silent fallback to the app database**. Falling
    back would restore the exact behaviour this exists to prevent, and it would
    do so invisibly — the suite would pass while quietly writing to real data,
    which is how the damage above went unnoticed for so long. A missing test
    database is a hard stop with instructions.
    """
    from app.config import get_settings as _get_settings

    if os.environ.get("COSTADVISOR_TEST_USE_APP_DB") == "1":
        # Explicit, never a default. Printed every run so it cannot be forgotten.
        print("\n*** COSTADVISOR_TEST_USE_APP_DB=1 — the suite is writing to the "
              "APP database. Cleanup helpers here commit; expect real data to "
              "change. ***\n")
        return _get_settings().database_url

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        app_url = _get_settings().database_url
        base, _, name = app_url.rpartition("/")
        name, sep, query = name.partition("?")
        if not name:
            raise RuntimeError(f"Cannot derive a test database name from {app_url!r}")
        url = f"{base}/{name if name.endswith('_test') else name + '_test'}{sep}{query}"

    probe = create_engine(url, pool_pre_ping=True)
    try:
        with probe.connect():
            pass
    except Exception as exc:
        raise RuntimeError(
            f"\n\nThe test database is not reachable: {url}\n  {type(exc).__name__}: {exc}\n\n"
            "Create it as a copy of the app database (no active connections needed on it):\n"
            "    createdb -U costadvisor -h 127.0.0.1 -T costadvisor costadvisor_test\n\n"
            "Or point somewhere else with TEST_DATABASE_URL. The suite will not fall "
            "back to the app database: its cleanup helpers commit, so a fallback would "
            "silently write to real data.\n"
        ) from exc
    finally:
        probe.dispose()

    os.environ["DATABASE_URL"] = url
    _get_settings.cache_clear()   # the engine is built from this on the next import
    return url


def _migrate_the_test_database(url: str) -> None:
    """Bring the test database up to head before the suite touches it.

    The test database was created with `createdb -T costadvisor`, a template
    copy — which snapshots the schema at one moment and then never moves. Every
    migration after that has to reach it separately, and the failure mode is
    nasty: the suite fails with `relation "..." does not exist` on the new
    table, which reads as a broken test rather than a stale database, and the
    fix (`DATABASE_URL=...-test alembic upgrade head`) is nowhere near the
    error. SCRUM-34 hit exactly this.

    Cheap when there is nothing to do: the revision is compared against the
    script directory's head first, so the usual run costs one query rather than
    Alembic's full machinery.

    **Never migrates the app database.** Under `COSTADVISOR_TEST_USE_APP_DB=1`
    the URL is real data, and changing its schema as a side effect of running
    tests is not a thing this should ever do — that mode is already opting into
    enough. Set `COSTADVISOR_TEST_SKIP_MIGRATE=1` to manage the test database by
    hand.
    """
    if os.environ.get("COSTADVISOR_TEST_USE_APP_DB") == "1":
        return
    if os.environ.get("COSTADVISOR_TEST_SKIP_MIGRATE") == "1":
        return

    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    backend_root = pathlib.Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    # Resolved from this file rather than the working directory: pytest can be
    # invoked from anywhere, and `alembic.ini`'s own `script_location` is
    # relative.
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)

    heads = set(ScriptDirectory.from_config(cfg).get_heads())
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            current = set(MigrationContext.configure(conn).get_current_heads())
    finally:
        engine.dispose()
    if current == heads:
        return

    print(f"\n*** Test database is at {current or '(empty)'}, head is {heads} "
          f"— upgrading {url.rsplit('/', 1)[-1]}. ***")
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        raise RuntimeError(
            f"\n\nCould not migrate the test database to head: "
            f"{type(exc).__name__}: {exc}\n\n"
            "Run it by hand to see the full error:\n"
            f"    DATABASE_URL={url} alembic upgrade head\n\n"
            "Or set COSTADVISOR_TEST_SKIP_MIGRATE=1 to manage it yourself. The "
            "suite will not run against a stale schema: the failures that "
            "produces look like broken tests, not a stale database.\n"
        ) from exc


TEST_DATABASE_URL = _point_at_the_test_database()
_migrate_the_test_database(TEST_DATABASE_URL)

from app.config import get_settings  # noqa: E402  — must follow the redirect
from app.database import SessionLocal, bypass_rls_var  # noqa: E402
from app.main import app  # noqa: E402
from app.models.team import Team, TeamMembership  # noqa: E402
from app.models.user import User  # noqa: E402

settings = get_settings()


def _make_jwt(user_id: uuid.UUID) -> str:
    return jwt.encode(
        {
            "sub": str(user_id),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "iat": datetime.now(timezone.utc),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


@pytest.fixture
def db():
    """Raw session with RLS bypassed — for test setup/teardown/assertions."""
    bypass_rls_var.set(True)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
        bypass_rls_var.set(False)


@pytest.fixture
def client():
    """Unauthenticated TestClient."""
    return TestClient(app)


@pytest.fixture
def client_as():
    """Factory for an authenticated TestClient bound to a user fixture dict
    (one of `tenant_a`, `tenant_b`, or a user_factory() result)."""
    def _as(user: dict) -> TestClient:
        c = TestClient(app)
        c.cookies.set("ca_token", user["token"])
        return c
    return _as


@pytest.fixture
def user_factory(db):
    """Returns a callable that creates a user + a personal team, returning a
    dict with `user_id`, `team_id`, `token`, and `cookies` ready for
    TestClient."""
    created: list[tuple[uuid.UUID, uuid.UUID]] = []

    def _create(is_super_admin: bool = False) -> dict:
        uid = uuid.uuid4()
        tid = uuid.uuid4()
        u = User(
            id=uid,
            google_id=f"test-{uid}",
            email=f"test-{uid}@test.local",
            display_name=f"Test-{uid.hex[:6]}",
            is_super_admin=is_super_admin,
        )
        db.add(u)
        db.flush()
        db.add(Team(id=tid, name=f"Team-{uid.hex[:6]}", created_by=uid))
        db.flush()
        db.add(TeamMembership(user_id=uid, team_id=tid, role="owner"))
        db.commit()
        created.append((uid, tid))
        token = _make_jwt(uid)
        return {
            "user_id": uid,
            "team_id": tid,
            "token": token,
            # httpx TestClient: explicit Cookie header is the most robust way
            # to attach auth across both sync and async paths.
            "headers": {"Cookie": f"ca_token={token}"},
        }

    yield _create

    # Teardown: raw SQL so Postgres FK CASCADEs handle the graph — the ORM
    # otherwise tries to null out PK columns on related rows.
    bypass_rls_var.set(True)
    for uid, tid in created:
        # Clear audit rows this user authored first — impersonation writes a row
        # with user_id=admin into the *target's* team, so it isn't covered by the
        # team CASCADE and would otherwise block the users DELETE on its FK.
        db.execute(text("DELETE FROM audit_logs WHERE user_id = :uid"), {"uid": str(uid)})
        db.execute(text("DELETE FROM teams WHERE id = :tid"), {"tid": str(tid)})
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(uid)})
    db.commit()


@pytest.fixture
def tenant_a(user_factory):
    return user_factory()


@pytest.fixture
def tenant_b(user_factory):
    return user_factory()
