"""Scrum 25 — intra-team collaboration (notes + negotiation flag)."""
import uuid

import pytest

from app.models.product import Product
from app.models.cost_model import CostModel
from app.models.team import TeamMembership


@pytest.fixture
def cost_model(tenant_a, db):
    product = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"],
                      created_by=tenant_a["user_id"], name="Caustic Soda", unit="mt")
    db.add(product)
    db.flush()
    cm = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=product.id,
                   created_by=tenant_a["user_id"], region="Europe", currency="USD")
    db.add(cm)
    db.commit()
    return cm


def test_create_and_list_notes(client_as, tenant_a, cost_model):
    c = client_as(tenant_a)
    r = c.post(f"/api/cost-models/{cost_model.id}/notes", json={"body": "First look — supplier padding ~8%."})
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["author_name"]
    # threaded reply
    r2 = c.post(f"/api/cost-models/{cost_model.id}/notes",
                json={"body": "Agreed, let's push back.", "parent_note_id": note["id"]})
    assert r2.status_code == 201
    assert r2.json()["parent_note_id"] == note["id"]

    lst = c.get(f"/api/cost-models/{cost_model.id}/notes")
    assert lst.status_code == 200
    assert len(lst.json()) == 2


def test_delete_own_note(client_as, tenant_a, cost_model):
    c = client_as(tenant_a)
    nid = c.post(f"/api/cost-models/{cost_model.id}/notes", json={"body": "temp"}).json()["id"]
    d = c.delete(f"/api/cost-models/{cost_model.id}/notes/{nid}")
    assert d.status_code == 200
    assert c.get(f"/api/cost-models/{cost_model.id}/notes").json() == []


def test_set_flag(client_as, tenant_a, cost_model):
    c = client_as(tenant_a)
    r = c.put(f"/api/cost-models/{cost_model.id}/flag", json={"negotiation_state": "in_negotiation"})
    assert r.status_code == 200
    assert r.json()["negotiation_state"] == "in_negotiation"
    # invalid state rejected
    bad = c.put(f"/api/cost-models/{cost_model.id}/flag", json={"negotiation_state": "bogus"})
    assert bad.status_code == 422
    # flag surfaces on the cost-model read
    cm = c.get(f"/api/cost-models/{cost_model.id}").json()
    assert cm["negotiation_state"] == "in_negotiation"


def test_member_can_note_but_not_flag(client_as, tenant_a, cost_model, user_factory, db):
    member = user_factory()
    db.add(TeamMembership(user_id=member["user_id"], team_id=tenant_a["team_id"], role="member"))
    db.commit()
    c = client_as(member)
    # member (view/export) can leave a note
    assert c.post(f"/api/cost-models/{cost_model.id}/notes", json={"body": "hi"}).status_code == 201
    # but cannot change the negotiation flag (needs edit)
    assert c.put(f"/api/cost-models/{cost_model.id}/flag", json={"negotiation_state": "agreed"}).status_code == 403


def test_non_member_forbidden(client_as, tenant_a, tenant_b, cost_model):
    c = client_as(tenant_b)
    assert c.get(f"/api/cost-models/{cost_model.id}/notes").status_code in (403, 404)
    assert c.post(f"/api/cost-models/{cost_model.id}/notes", json={"body": "x"}).status_code in (403, 404)
