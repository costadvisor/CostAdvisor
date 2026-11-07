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


def test_cost_model_update_logs_only_changed_fields(client_as, tenant_a, db):
    """The edit form submits every field; the update row named each one as
    {old, new} even when it had not changed."""
    from app.models.cost_model import CostModel
    p = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"], created_by=tenant_a["user_id"],
                name="audit-model-product")
    db.add(p)
    db.flush()
    cm = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=p.id,
                   created_by=tenant_a["user_id"], region="Europe", currency="EUR")
    db.add(cm)
    db.commit()

    def updates():
        db.expire_all()
        return (db.query(AuditLog)
                .filter(AuditLog.team_id == tenant_a["team_id"], AuditLog.event_type == "update",
                        AuditLog.entity_type == "cost_model", AuditLog.entity_id == str(cm.id))
                .all())

    c = client_as(tenant_a)
    r = c.put(f"/api/cost-models/{cm.id}", json={"region": "Europe", "currency": "EUR"})
    assert r.status_code == 200, r.text
    assert updates() == []                       # nothing changed, nothing logged

    r = c.put(f"/api/cost-models/{cm.id}", json={"region": "Europe", "currency": "USD"})
    assert r.status_code == 200, r.text
    [evt] = updates()
    assert evt.new_value == {"currency": {"old": "EUR", "new": "USD"}}
