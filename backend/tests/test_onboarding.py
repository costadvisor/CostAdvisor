"""Scrum 16 — self-serve onboarding: load-example-data (for ANY team, not just the
hardcoded staminachem one) + the onboarding-status checklist signals."""
import pytest


def test_load_example_data_works_for_a_new_team(client_as, tenant_a, db):
    c = client_as(tenant_a)
    r = c.post(f"/api/teams/{tenant_a['team_id']}/load-example-data")
    assert r.status_code == 200, r.text

    from app.models.product import Product
    from app.models.cost_model import CostModel
    n_products = db.query(Product).filter(Product.team_id == tenant_a["team_id"]).count()
    n_models = db.query(CostModel).filter(CostModel.team_id == tenant_a["team_id"]).count()
    assert n_products == 5
    assert n_models == 10


def test_load_example_data_is_idempotent(client_as, tenant_a, db):
    c = client_as(tenant_a)
    assert c.post(f"/api/teams/{tenant_a['team_id']}/load-example-data").status_code == 200
    assert c.post(f"/api/teams/{tenant_a['team_id']}/load-example-data").status_code == 200

    from app.models.product import Product
    n_products = db.query(Product).filter(Product.team_id == tenant_a["team_id"]).count()
    assert n_products == 5  # not 10 — re-run creates no duplicates


def test_load_example_data_requires_edit_permission(client_as, tenant_a, user_factory, db):
    from app.models.team import TeamMembership
    member = user_factory()
    db.add(TeamMembership(user_id=member["user_id"], team_id=tenant_a["team_id"], role="member"))
    db.commit()
    r = client_as(member).post(f"/api/teams/{tenant_a['team_id']}/load-example-data")
    assert r.status_code == 403


def test_onboarding_status_reflects_real_progress(client_as, tenant_a, db):
    c = client_as(tenant_a)
    status = c.get(f"/api/teams/{tenant_a['team_id']}/onboarding-status").json()
    assert status == {
        "has_product": False, "has_priced_model": False,
        "has_actual_price": False, "has_brief": False,
    }

    c.post(f"/api/teams/{tenant_a['team_id']}/load-example-data")
    status = c.get(f"/api/teams/{tenant_a['team_id']}/onboarding-status").json()
    assert status["has_product"] is True
    assert status["has_priced_model"] is True
    assert status["has_actual_price"] is True
    assert status["has_brief"] is False  # no brief has been generated yet


def test_onboarding_status_non_member_forbidden(client_as, tenant_a, tenant_b):
    r = client_as(tenant_b).get(f"/api/teams/{tenant_a['team_id']}/onboarding-status")
    assert r.status_code == 403
