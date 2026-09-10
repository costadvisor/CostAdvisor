"""Support system: threads (person/team scope), staff replies, canned
responses, FAQ."""
import pytest


@pytest.fixture
def support_agent(user_factory, db):
    """A non-super-admin user granted the platform Support Agent role."""
    from app.models.rbac import Role, UserPlatformRole

    u = user_factory()
    role = db.query(Role).filter(Role.team_id.is_(None), Role.name == "Support Agent").first()
    db.add(UserPlatformRole(user_id=u["user_id"], role_id=role.id))
    db.commit()
    return u


def test_create_thread_and_reply(client_as, tenant_a):
    c = client_as(tenant_a)
    r = c.post(f"/api/support/threads?team_id={tenant_a['team_id']}",
               json={"subject": "How do I add a supplier?", "body": "Can't find the button."})
    assert r.status_code == 200, r.text
    thread = r.json()
    assert thread["status"] == "open"
    assert thread["message_count"] == 1

    got = c.get(f"/api/support/threads/{thread['id']}")
    assert got.status_code == 200
    assert len(got.json()["messages"]) == 1


def _add_teammate(db, team_id, email):
    """A second member of an existing team, authenticated the same way
    user_factory's fixture users are (client_as needs a `token` key)."""
    import uuid as _uuid
    from app.models.team import TeamMembership
    from app.models.user import User
    from tests.conftest import _make_jwt

    uid = _uuid.uuid4()
    db.add(User(id=uid, google_id=email, email=email, display_name=email))
    db.flush()
    db.add(TeamMembership(user_id=uid, team_id=team_id, role="member"))
    db.commit()
    return {"user_id": uid, "team_id": team_id, "token": _make_jwt(uid)}


def _delete_teammate(db, uid):
    from sqlalchemy import text
    db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(uid)})
    db.commit()


def test_person_scope_is_private_from_teammates(client_as, tenant_a, db):
    """A second member of the SAME team must not see a 'person'-scope thread
    that isn't theirs, even though team RLS would let the row through."""
    teammate = _add_teammate(db, tenant_a["team_id"], "t2@test.local")
    thread = client_as(tenant_a).post(
        f"/api/support/threads?team_id={tenant_a['team_id']}",
        json={"subject": "Private question", "body": "...", "scope": "person"},
    ).json()

    denied = client_as(teammate).get(f"/api/support/threads/{thread['id']}")
    assert denied.status_code == 403
    _delete_teammate(db, teammate["user_id"])


def test_team_scope_is_visible_to_teammates(client_as, tenant_a, db):
    teammate = _add_teammate(db, tenant_a["team_id"], "t3@test.local")
    thread = client_as(tenant_a).post(
        f"/api/support/threads?team_id={tenant_a['team_id']}",
        json={"subject": "Team question", "body": "...", "scope": "team"},
    ).json()

    ok = client_as(teammate).get(f"/api/support/threads/{thread['id']}")
    assert ok.status_code == 200
    _delete_teammate(db, teammate["user_id"])


def test_staff_can_reply_and_change_status(client_as, tenant_a, support_agent):
    user_c = client_as(tenant_a)
    thread = user_c.post(f"/api/support/threads?team_id={tenant_a['team_id']}",
                          json={"subject": "Help", "body": "?"}).json()

    staff_c = client_as(support_agent)
    reply = staff_c.post(f"/api/support/threads/{thread['id']}/messages",
                          json={"body": "Here's how..."})
    assert reply.status_code == 200
    assert reply.json()["is_staff"] is True

    st = staff_c.put(f"/api/support/threads/{thread['id']}/status", json={"status": "resolved"})
    assert st.status_code == 200
    assert st.json()["status"] == "resolved"


def test_non_staff_cannot_reply_to_others_thread_or_manage_content(client_as, tenant_a, tenant_b):
    thread = client_as(tenant_a).post(f"/api/support/threads?team_id={tenant_a['team_id']}",
                                       json={"subject": "Q", "body": "?"}).json()
    other = client_as(tenant_b)
    assert other.get(f"/api/support/threads/{thread['id']}").status_code == 403
    assert other.post("/api/support/canned-responses",
                       json={"title": "Hi", "body": "Hello there"}).status_code == 403
    assert other.post("/api/support/faq",
                       json={"category": "onboarding", "question": "Q?", "answer": "A."}).status_code == 403


def test_canned_response_crud_and_faq_public_read(client_as, support_agent, tenant_a):
    staff = client_as(support_agent)
    created = staff.post("/api/support/canned-responses",
                          json={"title": "Welcome", "body": "Thanks for reaching out!", "category": "general"})
    assert created.status_code == 200
    cr_id = created.json()["id"]
    assert staff.put(f"/api/support/canned-responses/{cr_id}",
                      json={"title": "Welcome!", "body": "Updated", "category": "general"}).status_code == 200

    faq = staff.post("/api/support/faq",
                      json={"category": "onboarding", "question": "How do I create a team?",
                            "answer": "Go to Team > Create."})
    assert faq.status_code == 200

    # A regular, non-staff user can read published FAQ but not the admin listing.
    user_c = client_as(tenant_a)
    pub = user_c.get("/api/support/faq")
    assert pub.status_code == 200
    assert any(f["question"].startswith("How do I create a team") for f in pub.json())
    assert user_c.get("/api/support/faq/admin").status_code == 403

    assert staff.delete(f"/api/support/canned-responses/{cr_id}").status_code == 200
