"""Staff sign-in: a verified Google Workspace account on one of
`settings.staff_email_domains` may sign up without a team invite or an approved
access request. Everyone else still meets the invite-only gate.

The Google token exchange and userinfo call are replaced by a fake client, so
these tests drive the real `/auth/callback` code path end to end."""
import uuid

import pytest

from app.models.auth_event import AuthEvent
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.routers import auth as auth_router
from app.routers.auth import is_staff_account


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _fake_oauth_client(userinfo):
    class _Client:
        def __init__(self, *args, **kwargs):
            self.token = None

        async def fetch_token(self, *args, **kwargs):
            return {"access_token": "fake-access-token"}

        async def get(self, url):
            return _FakeResponse(userinfo)

    return _Client


def _userinfo(email, *, hd=None, verified=True):
    info = {"sub": "test-sub-" + uuid.uuid4().hex, "email": email,
            "email_verified": verified, "name": "Test Person"}
    if hd is not None:
        info["hd"] = hd
    return info


@pytest.fixture
def sign_in(client, db, monkeypatch):
    """Run the OAuth callback for a given Google userinfo; clean up only the
    rows created for the emails this test used."""
    emails = []

    def _run(userinfo):
        emails.append(userinfo["email"])
        monkeypatch.setattr(auth_router, "AsyncOAuth2Client", _fake_oauth_client(userinfo))
        client.cookies.set("oauth_state", "st:verifier")
        return client.get("/auth/callback", params={"code": "c", "state": "st"},
                          follow_redirects=False)

    yield _run

    db.rollback()
    users = db.query(User).filter(User.email.in_(emails)).all()
    ids = [u.id for u in users]
    if ids:
        db.query(RefreshToken).filter(RefreshToken.user_id.in_(ids)).delete(synchronize_session=False)
    db.query(AuthEvent).filter(AuthEvent.email.in_(emails)).delete(synchronize_session=False)
    for u in users:
        db.delete(u)
    db.commit()


def _new_email(domain):
    return f"staff-test-{uuid.uuid4().hex[:10]}@{domain}"


def test_workspace_staff_account_signs_up_without_invite(sign_in, db):
    email = _new_email("staminachem.com")
    r = sign_in(_userinfo(email, hd="staminachem.com"))
    assert r.status_code == 302
    assert "login_error" not in r.headers["location"]
    user = db.query(User).filter(User.email == email).first()
    assert user is not None
    event = db.query(AuthEvent).filter(AuthEvent.email == email,
                                       AuthEvent.event_type == "login_success").first()
    assert event is not None and event.reason == "staff_domain"


def test_personal_google_account_on_the_staff_address_is_still_gated(sign_in, db):
    # A personal (non-Workspace) Google account can carry a verified
    # @staminachem.com address; it has no `hd` claim and must not pass.
    email = _new_email("staminachem.com")
    r = sign_in(_userinfo(email, hd=None))
    assert "login_error=access_needed" in r.headers["location"]
    assert db.query(User).filter(User.email == email).first() is None


def test_unverified_email_is_still_gated(sign_in, db):
    email = _new_email("staminachem.com")
    r = sign_in(_userinfo(email, hd="staminachem.com", verified=False))
    assert "login_error=access_needed" in r.headers["location"]
    assert db.query(User).filter(User.email == email).first() is None


def test_other_workspace_domain_is_still_gated(sign_in, db):
    email = _new_email("example.com")
    r = sign_in(_userinfo(email, hd="example.com"))
    assert "login_error=access_needed" in r.headers["location"]
    assert db.query(User).filter(User.email == email).first() is None


def test_empty_setting_turns_the_bypass_off(sign_in, db, monkeypatch):
    monkeypatch.setattr(auth_router.settings, "staff_email_domains", "")
    email = _new_email("staminachem.com")
    r = sign_in(_userinfo(email, hd="staminachem.com"))
    assert "login_error=access_needed" in r.headers["location"]
    assert db.query(User).filter(User.email == email).first() is None


@pytest.mark.parametrize("info, expected", [
    ({"email": "a@staminachem.com", "hd": "staminachem.com", "email_verified": True}, True),
    ({"email": "A@StaminaChem.com", "hd": "StaminaChem.com", "email_verified": True}, True),
    ({"email": "a@staminachem.com", "email_verified": True}, False),
    ({"email": "a@staminachem.com", "hd": "staminachem.com", "email_verified": False}, False),
    ({"email": "a@staminachem.com", "hd": "staminachem.com", "email_verified": "true"}, False),
    ({"email": "a@other.com", "hd": "staminachem.com", "email_verified": True}, False),
    ({"email": "a@evil-staminachem.com", "hd": "evil-staminachem.com", "email_verified": True}, False),
    ({"email": "a@sub.staminachem.com", "hd": "staminachem.com", "email_verified": True}, False),
])
def test_is_staff_account(info, expected):
    assert is_staff_account(info) is expected
