"""Cross-tenant isolation tests. Every model-id-bearing route must reject
access from a user outside the owning team — 403 from the app layer or 404
from RLS + resource-not-found are both acceptable."""
from __future__ import annotations

import uuid

import pytest

from app.database import bypass_rls_var
from app.models.product import Product


ISOLATION_CODES = {403, 404}


@pytest.fixture
def a_product(tenant_a, db):
    p = Product(
        id=uuid.uuid4(),
        team_id=tenant_a["team_id"],
        created_by=tenant_a["user_id"],
        name="tenant-a-widget",
    )
    db.add(p)
    db.commit()
    return p


def test_cross_tenant_product_get(client_as, tenant_b, a_product):
    r = client_as(tenant_b).get(f"/api/products/{a_product.id}")
    assert r.status_code in ISOLATION_CODES, r.text


def test_cross_tenant_product_put(client_as, tenant_b, a_product):
    r = client_as(tenant_b).put(
        f"/api/products/{a_product.id}",
        json={"name": "hijack", "formula": None, "product_line_id": None},
    )
    assert r.status_code in ISOLATION_CODES, r.text


def test_cross_tenant_product_delete(client_as, tenant_b, a_product, db):
    r = client_as(tenant_b).delete(f"/api/products/{a_product.id}")
    assert r.status_code in ISOLATION_CODES, r.text
    bypass_rls_var.set(True)
    assert db.query(Product).filter(Product.id == a_product.id).first() is not None


def test_cross_tenant_products_list_by_team(client_as, tenant_a, tenant_b):
    r = client_as(tenant_b).get(f"/api/products?team_id={tenant_a['team_id']}")
    assert r.status_code == 403, r.text


def test_cross_tenant_index_override_bulk_delete(client_as, tenant_a, tenant_b):
    r = client_as(tenant_b).delete(
        f"/api/indexes/overrides/bulk?team_id={tenant_a['team_id']}&commodity_id=1&region=EU"
    )
    assert r.status_code == 403, r.text


def test_cross_tenant_scenarios_list(client_as, tenant_a, tenant_b):
    r = client_as(tenant_b).get(f"/api/scenarios/?team_id={tenant_a['team_id']}")
    assert r.status_code == 403, r.text


def test_cross_tenant_portfolio(client_as, tenant_a, tenant_b):
    r = client_as(tenant_b).get(f"/api/portfolio/summary?team_id={tenant_a['team_id']}")
    assert r.status_code == 403, r.text


def test_own_tenant_products_list(client_as, tenant_a):
    r = client_as(tenant_a).get(f"/api/products?team_id={tenant_a['team_id']}")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
