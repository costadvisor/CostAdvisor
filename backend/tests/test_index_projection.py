"""Scrum 70 (Part 1) — index projection service.

Covers the fit/hold/no-history decision tree, the non-destructive-vintage
guarantee (re-running never overwrites a prior run), and the two endpoints.
The OLS fit itself is pinned against hand-computed values so a method change
shows up as a diff, not a silent surprise.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.index_data import CommodityIndex, IndexValue
from app.models.index_projection import IndexProjectionRun, IndexProjectionPoint
from app.services.index_projection import run_projection, latest_projection

REGION = "Europe"


def _make_commodity(db, tag: str) -> int:
    c = CommodityIndex(name=f"proj-test-{tag}-{uuid.uuid4().hex[:8]}", currency="USD", unit="t")
    db.add(c)
    db.flush()
    return c.id


def _add_values(db, commodity_id: int, points: list[tuple[int, int, float]]):
    db.add_all([
        IndexValue(commodity_id=commodity_id, region=REGION, year=y, quarter=q, value=v)
        for y, q, v in points
    ])
    db.commit()


def _cleanup(db, commodity_id: int):
    run_ids = [r.id for r in db.query(IndexProjectionRun).filter(IndexProjectionRun.commodity_id == commodity_id).all()]
    if run_ids:
        db.query(IndexProjectionPoint).filter(IndexProjectionPoint.run_id.in_(run_ids)).delete(synchronize_session=False)
        db.query(IndexProjectionRun).filter(IndexProjectionRun.id.in_(run_ids)).delete(synchronize_session=False)
    db.query(IndexValue).filter(IndexValue.commodity_id == commodity_id).delete(synchronize_session=False)
    db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).delete(synchronize_session=False)
    db.commit()


# A near-linear series (small noise) so the fit is real but not degenerate.
FITTED_SERIES = [
    (2024, 1, 100.0),
    (2024, 2, 104.0),
    (2024, 3, 107.0),
    (2024, 4, 111.0),
    (2025, 1, 116.0),
    (2025, 2, 118.0),
]


def test_project_series_with_history_returns_fitted_vintage(db):
    cid = _make_commodity(db, "fitted")
    try:
        _add_values(db, cid, FITTED_SERIES)
        run = run_projection(db, cid, REGION, horizon_quarters=2)

        assert run.status == "fitted"
        assert run.method == "ols_linear_trend"
        assert run.vintage_at is not None
        assert run.history_points_used == 6
        assert len(run.points) == 2
        for p in run.points:
            assert p.value is not None
            assert p.ci_lo is not None and p.ci_hi is not None
            assert p.ci_lo < p.value < p.ci_hi
    finally:
        _cleanup(db, cid)


def test_fitted_output_is_pinned(db):
    """Hand-computed OLS fit on FITTED_SERIES — pins the exact numbers so a
    method change shows up as a diff, not a surprise."""
    cid = _make_commodity(db, "pinned")
    try:
        _add_values(db, cid, FITTED_SERIES)
        run = run_projection(db, cid, REGION, horizon_quarters=2)

        p1, p2 = run.points[0], run.points[1]
        assert (p1.year, p1.quarter) == (2025, 3)
        assert (p2.year, p2.quarter) == (2025, 4)

        assert float(p1.value) == pytest.approx(122.3333, abs=0.01)
        assert float(p1.ci_lo) == pytest.approx(121.125, abs=0.01)
        assert float(p1.ci_hi) == pytest.approx(123.5416, abs=0.01)

        assert float(p2.value) == pytest.approx(126.0476, abs=0.01)
        assert float(p2.ci_lo) == pytest.approx(124.6995, abs=0.01)
        assert float(p2.ci_hi) == pytest.approx(127.3958, abs=0.01)
    finally:
        _cleanup(db, cid)


def test_rerun_projection_creates_new_vintage_leaves_first_intact(db):
    cid = _make_commodity(db, "rerun")
    try:
        _add_values(db, cid, FITTED_SERIES)
        first = run_projection(db, cid, REGION, horizon_quarters=2)
        first_id = first.id
        first_point_values = [(p.year, p.quarter, float(p.value)) for p in first.points]

        second = run_projection(db, cid, REGION, horizon_quarters=2)

        assert second.id != first_id
        assert second.vintage_at >= first.vintage_at

        reloaded_first = db.query(IndexProjectionRun).filter(IndexProjectionRun.id == first_id).first()
        assert reloaded_first is not None
        assert [(p.year, p.quarter, float(p.value)) for p in reloaded_first.points] == first_point_values

        latest = latest_projection(db, cid, REGION)
        assert latest.id == second.id
    finally:
        _cleanup(db, cid)


def test_flat_tail_marked_as_hold(db):
    """Earlier-varying history with a dead-flat last 4 quarters — the 'last
    actual repeated forward' shape must be marked hold, not fitted."""
    cid = _make_commodity(db, "flat")
    try:
        series = [
            (2023, 1, 90.0), (2023, 2, 95.0), (2023, 3, 100.0), (2023, 4, 98.0),
            (2024, 1, 105.0), (2024, 2, 105.0), (2024, 3, 105.0), (2024, 4, 105.0),
        ]
        _add_values(db, cid, series)
        run = run_projection(db, cid, REGION, horizon_quarters=3)

        assert run.status == "hold"
        assert run.method == "hold_flat_variance"
        assert len(run.points) == 3
        for p in run.points:
            assert float(p.value) == pytest.approx(105.0)
            assert p.ci_lo is None
            assert p.ci_hi is None
    finally:
        _cleanup(db, cid)


def test_no_history_returns_explicit_result(db):
    cid = _make_commodity(db, "nohist")
    try:
        run = run_projection(db, cid, REGION, horizon_quarters=4)

        assert run is not None
        assert run.status == "no_history"
        assert run.method == "no_history"
        assert run.points == []
    finally:
        _cleanup(db, cid)


def test_insufficient_points_marked_hold(db):
    cid = _make_commodity(db, "fewpoints")
    try:
        series = [(2024, 1, 100.0), (2024, 2, 103.0), (2024, 3, 108.0)]
        _add_values(db, cid, series)
        run = run_projection(db, cid, REGION, horizon_quarters=2)

        assert run.status == "hold"
        assert run.method == "hold_insufficient_points"
        for p in run.points:
            assert float(p.value) == pytest.approx(108.0)
    finally:
        _cleanup(db, cid)


def test_project_endpoint_super_admin_only(client_as, tenant_a, db):
    cid = _make_commodity(db, "epperm")
    try:
        _add_values(db, cid, FITTED_SERIES)
        r = client_as(tenant_a).post(f"/api/indexes/{cid}/project", params={"region": REGION})
        assert r.status_code == 403
    finally:
        _cleanup(db, cid)


def test_project_endpoint_super_admin_ok(client_as, user_factory, db):
    admin = user_factory(is_super_admin=True)
    cid = _make_commodity(db, "epadmin")
    try:
        _add_values(db, cid, FITTED_SERIES)
        r = client_as(admin).post(f"/api/indexes/{cid}/project", params={"region": REGION, "horizon": 2})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "fitted"
        assert len(body["points"]) == 2
    finally:
        _cleanup(db, cid)


def test_latest_projection_endpoint_any_authenticated_user(client_as, tenant_a, db):
    cid = _make_commodity(db, "eplatest")
    try:
        _add_values(db, cid, FITTED_SERIES)
        run_projection(db, cid, REGION, horizon_quarters=1)
        r = client_as(tenant_a).get(f"/api/indexes/{cid}/projections/latest", params={"region": REGION})
        assert r.status_code == 200
        assert r.json()["status"] == "fitted"
    finally:
        _cleanup(db, cid)


def test_latest_projection_endpoint_null_when_never_projected(client_as, tenant_a, db):
    cid = _make_commodity(db, "epnever")
    try:
        _add_values(db, cid, FITTED_SERIES)
        r = client_as(tenant_a).get(f"/api/indexes/{cid}/projections/latest", params={"region": REGION})
        assert r.status_code == 200
        assert r.json() is None
    finally:
        _cleanup(db, cid)
