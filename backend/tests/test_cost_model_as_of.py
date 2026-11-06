"""GET /api/cost-models/{id} names the quarter "should-cost today" is priced at
(audit C-13, C-27).

Portfolio, Monitor and the brief price "today" at the model's latest quarter
with data (costing_engine.latest_data_quarter). The product page priced it at
the calendar quarter, the last month carried forward, so it read €292 "as of
Q4 2026" beside their €291 as of Q2-26. The single-model read now carries
that quarter so the page can use it.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexMonthlyValue
from app.models.product import Product


def test_single_read_names_the_latest_quarter_with_data(client_as, tenant_a, db):
    ci = CommodityIndex(name=f"AsOf-{uuid.uuid4().hex[:8]}", currency="EUR", unit="t")
    db.add(ci)
    db.flush()
    db.add_all([IndexMonthlyValue(commodity_id=ci.id, year=2025, month=m, value=100 + m, kind="actual")
                for m in (1, 2, 3, 4)])
    p = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"], created_by=tenant_a["user_id"], name="As-of P", unit="t")
    db.add(p)
    db.flush()
    cm = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=p.id,
                   created_by=tenant_a["user_id"], region="Europe", currency="EUR")
    bare = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=p.id,
                     created_by=tenant_a["user_id"], region="Europe", currency="EUR")
    db.add_all([cm, bare])
    db.flush()
    fv = FormulaVersion(cost_model_id=cm.id, base_price=100, base_year=2025, base_quarter=1,
                        formula_type="simple", margin_type="pct", margin_value=0)
    db.add(fv)
    db.flush()
    db.add(FormulaComponent(formula_version_id=fv.id, label="Feed", commodity_id=ci.id, weight=1))
    db.commit()
    try:
        c = client_as(tenant_a)
        body = c.get(f"/api/cost-models/{cm.id}").json()
        assert (body["as_of_year"], body["as_of_quarter"]) == (2025, 2)   # April: Q2-25
        # No formula yet: nothing to price, so no as-of quarter.
        body = c.get(f"/api/cost-models/{bare.id}").json()
        assert body["as_of_year"] is None and body["as_of_quarter"] is None
        # The list adds it only when asked (the Portfolio grid).
        rows = {r["id"]: r for r in c.get(f"/api/cost-models?team_id={tenant_a['team_id']}&as_of=true").json()}
        assert (rows[str(cm.id)]["as_of_year"], rows[str(cm.id)]["as_of_quarter"]) == (2025, 2)
        rows = {r["id"]: r for r in c.get(f"/api/cost-models?team_id={tenant_a['team_id']}").json()}
        assert rows[str(cm.id)]["as_of_year"] is None
    finally:
        db.execute(text("DELETE FROM cost_models WHERE id IN (:a, :b)"), {"a": str(cm.id), "b": str(bare.id)})
        db.query(IndexMonthlyValue).filter(IndexMonthlyValue.commodity_id == ci.id).delete(synchronize_session=False)
        db.query(CommodityIndex).filter(CommodityIndex.id == ci.id).delete(synchronize_session=False)
        db.commit()
