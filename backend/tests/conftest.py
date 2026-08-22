"""Shared fixtures. Tests run against the local dev Postgres — no separate
test DB. Each test gets fresh users and teams with random UUIDs and tears
them down (team CASCADE wipes products, cost_models, overrides, etc.).

RLS is live — fixtures use `bypass_rls_var.set(True)` during setup/teardown.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal, bypass_rls_var
from app.main import app
from app.models.team import Team, TeamMembership
from app.models.user import User

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
