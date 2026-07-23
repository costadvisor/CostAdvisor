"""Product -> catalog formula link (Scrum 58 auto-load gap).

A product can be linked to the catalog formula that prices it; cost models for
the product auto-load that template at their region (frontend). Here we cover
the association API: link on create, code/name enrichment, scope validation,
and explicit-null unlink.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.formula_template import FormulaTemplate


def _mk_template(db, name, created_by, team_id=None, code=None) -> FormulaTemplate:
    t = FormulaTemplate(team_id=team_id, created_by=created_by, name=name,
                        code=code, expression=None)
    db.add(t)
    db.commit()
    return t


def _cleanup_templates(db, ids):
    bypass_rls_var.set(True)
    for tid in ids:
        db.execute(text("DELETE FROM formula_templates WHERE id = :id"), {"id": str(tid)})
    db.commit()


def test_product_links_platform_template(db, tenant_a, client_as):
    code = f"ZZL-{uuid.uuid4().hex[:6].upper()}"
    t = _mk_template(db, "linkable", tenant_a["user_id"], code=code)
    c = client_as(tenant_a)
    try:
        r = c.post(f"/api/products?team_id={tenant_a['team_id']}", json={
            "name": "Linked product", "unit": "kg", "formula_template_id": str(t.id),
        })
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["formula_template_id"] == str(t.id)
        assert body["formula_template_code"] == code
        assert body["formula_template_name"] == "linkable"

        # The list endpoint carries the enrichment too (builder + Products page read it)
        listed = c.get("/api/products/", params={"team_id": str(tenant_a["team_id"])}).json()
        me = next(p for p in listed if p["id"] == body["id"])
        assert me["formula_template_code"] == code

        # Explicit null unlinks
        r = c.put(f"/api/products/{body['id']}", json={"formula_template_id": None})
        assert r.status_code == 200 and r.json()["formula_template_id"] is None
    finally:
        _cleanup_templates(db, [t.id])


def test_product_cannot_link_foreign_team_template(db, tenant_a, tenant_b, client_as):
    foreign = _mk_template(db, "b-private", tenant_b["user_id"], team_id=tenant_b["team_id"])
    try:
        r = client_as(tenant_a).post(f"/api/products?team_id={tenant_a['team_id']}", json={
            "name": "Sneaky", "unit": "kg", "formula_template_id": str(foreign.id),
        })
        assert r.status_code == 400
        assert "Unknown formula template" in r.json()["detail"]
    finally:
        _cleanup_templates(db, [foreign.id])
