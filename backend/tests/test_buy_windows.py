"""Scrum 22 — opportunistic buy windows endpoint.

Focus: auth/permission gate + response shape. The signal math (current
should-cost vs trailing-4Q average from calculate_evolution) reuses the costing
engine, already covered by the engine determinism tests.

Scrum 70 (Part 2) additions cover the forward lock/hold verdict below — in
particular the regression proving forecast storage (Part 1) never perturbs
this backward signal, which is the load-bearing guarantee the whole Part 2
design leans on.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.index_data import CommodityIndex, IndexValue
from app.models.index_projection import IndexProjectionRun, IndexProjectionPoint
from app.models.product import Product

REGION = "Europe"
BASE_Y, BASE_Q = 2024, 1


def _insert_run(db, commodity_id, region, vintage_at, points, status="fitted",
                 method="ols_linear_trend", horizon_quarters=4):
    """Directly construct a projection vintage at an exact (year, quarter) —
    bypasses run_projection's own horizon-from-last-actual-data computation so
    the verdict tests can target an exact future period regardless of what
    "today" happens to be when the suite runs."""
    run = IndexProjectionRun(
        commodity_id=commodity_id, region=region, vintage_at=vintage_at,
        status=status, method=method, horizon_quarters=horizon_quarters,
        history_points_used=4,
    )
    db.add(run)
    db.flush()
    for y, q, v, lo, hi in points:
        db.add(IndexProjectionPoint(run_id=run.id, year=y, quarter=q, value=v, ci_lo=lo, ci_hi=hi))
    db.commit()
    return run


@pytest.fixture
def forward_model(tenant_a, db):
    """A single-component cost model (margin 0%, base 2024-Q1) with a short
    real IndexValue history — enough for the backward buy-window signal to
    produce a real (non-insufficient) reading, and a base-period value for the
    forward path's reference ratio. No forecast is created here; individual
    tests add projection runs as needed."""
    suffix = uuid.uuid4().hex[:8]
    commodity = CommodityIndex(name=f"Forward-{suffix}", currency="USD", unit="t")
    db.add(commodity)
    db.flush()

    db.add_all([
        IndexValue(commodity_id=commodity.id, region=REGION, year=2024, quarter=1, value=100),
        IndexValue(commodity_id=commodity.id, region=REGION, year=2024, quarter=2, value=102),
        IndexValue(commodity_id=commodity.id, region=REGION, year=2024, quarter=3, value=105),
        IndexValue(commodity_id=commodity.id, region=REGION, year=2024, quarter=4, value=103),
    ])

    product = Product(
        id=uuid.uuid4(), team_id=tenant_a["team_id"], created_by=tenant_a["user_id"],
        name="Forward Test Product", unit="kg",
    )
    db.add(product)
    db.flush()

    cm = CostModel(
        id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=product.id,
        created_by=tenant_a["user_id"], region=REGION, currency="USD",
    )
    db.add(cm)
    db.flush()

    fv = FormulaVersion(
        cost_model_id=cm.id, base_price=100, base_year=BASE_Y, base_quarter=BASE_Q,
        formula_type="simple", margin_type="pct", margin_value=0,
    )
    db.add(fv)
    db.flush()
    db.add(FormulaComponent(formula_version_id=fv.id, label="Feedstock", commodity_id=commodity.id, weight=1.0))
    db.commit()

    yield cm, commodity.id

    from sqlalchemy import text
    run_ids = [r.id for r in db.query(IndexProjectionRun).filter(IndexProjectionRun.commodity_id == commodity.id).all()]
    if run_ids:
        db.query(IndexProjectionPoint).filter(IndexProjectionPoint.run_id.in_(run_ids)).delete(synchronize_session=False)
        db.query(IndexProjectionRun).filter(IndexProjectionRun.id.in_(run_ids)).delete(synchronize_session=False)
    db.execute(text("DELETE FROM cost_models WHERE id = :id"), {"id": str(cm.id)})
    db.query(IndexValue).filter(IndexValue.commodity_id == commodity.id).delete(synchronize_session=False)
    db.query(CommodityIndex).filter(CommodityIndex.id == commodity.id).delete(synchronize_session=False)
    db.commit()


def test_buy_windows_owner_ok(client_as, tenant_a):
    c = client_as(tenant_a)
    r = c.get("/api/portfolio/buy-windows", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_buy_windows_requires_membership(client_as, tenant_a, tenant_b):
    c = client_as(tenant_b)
    r = c.get("/api/portfolio/buy-windows", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 403


def test_buy_windows_unauthenticated(client, tenant_a):
    r = client.get("/api/portfolio/buy-windows", params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 401


def test_buy_window_single_model_missing_404(client_as, tenant_a):
    import uuid
    c = client_as(tenant_a)
    r = c.get(f"/api/portfolio/buy-windows/{uuid.uuid4()}")
    assert r.status_code in (403, 404)  # unknown model → not found (or refused before lookup)


# ── Scrum 70 (Part 2): lock/hold verdict ─────────────────────────────────────

def test_buy_windows_unaffected_by_forecast_storage(client_as, tenant_a, forward_model, db):
    """The literal proof the Part 1/Part 2 design is meant to guarantee:
    _available_index_range's unfiltered max()/min() over index_values never
    sees forecast data, because forecast data is never written there."""
    cm, commodity_id = forward_model
    c = client_as(tenant_a)

    before = c.get(f"/api/portfolio/buy-windows/{cm.id}").json()

    _insert_run(
        db, commodity_id, REGION, datetime.now(timezone.utc),
        [(2030, 1, 999.0, 900.0, 1050.0)],  # deliberately absurd — would be obvious if it leaked in
    )

    after = c.get(f"/api/portfolio/buy-windows/{cm.id}").json()
    assert after == before


def test_verdict_insufficient_without_projection(client_as, tenant_a, forward_model):
    cm, _ = forward_model
    r = client_as(tenant_a).get(f"/api/portfolio/buy-windows/{cm.id}/verdict")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "insufficient"
    assert body["forecast_should_cost"] is None
    assert len(body["data_gaps"]) >= 1


def test_verdict_returns_horizon_and_vintage(client_as, tenant_a, forward_model, db):
    from app.services.costing_engine import _current_quarter, _advance_quarter

    cm, commodity_id = forward_model
    now_y, now_q = _current_quarter()
    h_year, h_quarter = _advance_quarter(now_y, now_q, 4)
    vintage = datetime.now(timezone.utc)
    run = _insert_run(db, commodity_id, REGION, vintage, [(h_year, h_quarter, 120.0, 115.0, 125.0)])

    r = client_as(tenant_a).get(f"/api/portfolio/buy-windows/{cm.id}/verdict")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["horizon_quarters"] == 4
    assert body["horizon_year"] == h_year
    assert body["horizon_quarter"] == h_quarter
    assert body["forecast_method"] == run.method
    assert body["forecast_vintage"] is not None
    assert body["forecast_should_cost"] == pytest.approx(120.0)  # 100 base * (120/100) ratio
    assert body["verdict"] == "lock"  # +20% >= +3% threshold


def test_verdict_changes_with_new_vintage(client_as, tenant_a, forward_model, db):
    """Fixture with two vintages for the same series — the verdict must use
    the LATEST one and flip when the forecast direction flips."""
    from app.services.costing_engine import _current_quarter, _advance_quarter

    cm, commodity_id = forward_model
    now_y, now_q = _current_quarter()
    h_year, h_quarter = _advance_quarter(now_y, now_q, 4)
    c = client_as(tenant_a)

    up_run = _insert_run(
        db, commodity_id, REGION, datetime(2025, 1, 1, tzinfo=timezone.utc),
        [(h_year, h_quarter, 120.0, 115.0, 125.0)],
    )
    body = c.get(f"/api/portfolio/buy-windows/{cm.id}/verdict").json()
    assert body["verdict"] == "lock"
    assert body["forecast_should_cost"] == pytest.approx(120.0)

    down_run = _insert_run(
        db, commodity_id, REGION, datetime(2025, 6, 1, tzinfo=timezone.utc),
        [(h_year, h_quarter, 90.0, 85.0, 95.0)],
    )
    body2 = c.get(f"/api/portfolio/buy-windows/{cm.id}/verdict").json()
    assert body2["verdict"] == "hold"
    assert body2["forecast_should_cost"] == pytest.approx(90.0)
    assert body2["forecast_vintage"] != body["forecast_vintage"]


def test_verdict_all_fixed_formula_is_neutral(client_as, tenant_a, db, tenant_b):
    """A formula with no commodity-linked components has nothing to forecast —
    that's deterministically 'neutral', not 'insufficient' (missing data)."""
    product = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"],
                       created_by=tenant_a["user_id"], name="Fixed-only product", unit="kg")
    db.add(product)
    db.flush()
    cm = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=product.id,
                    created_by=tenant_a["user_id"], region=REGION, currency="USD")
    db.add(cm)
    db.flush()
    fv = FormulaVersion(cost_model_id=cm.id, base_price=50, base_year=BASE_Y, base_quarter=BASE_Q,
                         formula_type="simple", margin_type="pct", margin_value=0)
    db.add(fv)
    db.flush()
    db.add(FormulaComponent(formula_version_id=fv.id, label="Fixed line", commodity_id=None, weight=1.0))
    db.commit()

    try:
        r = client_as(tenant_a).get(f"/api/portfolio/buy-windows/{cm.id}/verdict")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["verdict"] == "neutral"
        assert body["forecast_should_cost"] == pytest.approx(50.0)
        assert body["forecast_method"] == "fixed_formula_no_forecast_needed"

        r_forbidden = client_as(tenant_b).get(f"/api/portfolio/buy-windows/{cm.id}/verdict")
        assert r_forbidden.status_code in (403, 404)
    finally:
        from sqlalchemy import text
        db.execute(text("DELETE FROM cost_models WHERE id = :id"), {"id": str(cm.id)})
        db.commit()


def test_verdict_requires_membership(client_as, tenant_b, forward_model):
    cm, _ = forward_model
    r = client_as(tenant_b).get(f"/api/portfolio/buy-windows/{cm.id}/verdict")
    assert r.status_code in (403, 404)


def test_verdict_unauthenticated(client, forward_model):
    cm, _ = forward_model
    r = client.get(f"/api/portfolio/buy-windows/{cm.id}/verdict")
    assert r.status_code == 401
