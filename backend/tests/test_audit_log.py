"""Audit log wiring tests."""
from __future__ import annotations

import uuid

from app.models.audit_log import AuditLog
from app.models.product import Product


def _last_event(db, team_id: uuid.UUID) -> AuditLog | None:
    return (
        db.query(AuditLog)
        .filter(AuditLog.team_id == team_id)
        .order_by(AuditLog.timestamp.desc())
        .first()
    )


def test_product_create_logs_event(client_as, tenant_a, db):
    r = client_as(tenant_a).post(
        f"/api/products/?team_id={tenant_a['team_id']}",
        json={"name": "audit-widget", "formula": None, "product_line_id": None},
    )
    assert r.status_code == 201, r.text
    db.expire_all()
    evt = _last_event(db, tenant_a["team_id"])
    assert evt is not None
    assert evt.event_type == "create"
    assert evt.entity_type == "product"


def test_product_delete_logs_event(client_as, tenant_a, db):
    p = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"], created_by=tenant_a["user_id"], name="to-delete")
    db.add(p)
    db.commit()
    r = client_as(tenant_a).delete(f"/api/products/{p.id}")
    assert r.status_code == 200, r.text
    db.expire_all()
    evt = _last_event(db, tenant_a["team_id"])
    assert evt is not None
    assert evt.event_type == "delete"
    assert evt.entity_type == "product"
