"""The costing engine reading the drop's monthly series (Wave 3, unit 12 follow-up).

`IndexValue` is quarterly and region-keyed; the 2026-07 drop's 121 series landed
in `index_monthly_values`, which this resolver could not see. Measured on the
live catalogue when this was written: **76 of the 98 commodities the catalog's
cost lines reference are monthly-only**, so three quarters of the catalog was
invisible to should-cost, evolution, briefs and squeeze alike.

What these tests hold to:

1. a monthly-only series resolves, at the quarter mean, and is labelled;
2. `forecast` rows are **never** read — 726 of them exist, and one reaching a
   historical should-cost would be fabrication, not a fallback;
3. adding the tier changes **no** number that already resolved — every existing
   tier still wins over it;
4. a monthly-only series is carried forward past its last observation, the same
   protection the quarterly store has always had, rather than going flat;
5. a partial quarter is reported as a partial quarter, not passed off as three
   months of observation;
6. region is not a dimension in this layer, and the resolver does not pretend it
   is.
"""
import uuid

import pytest

from app.models.index_data import CommodityIndex, IndexOverride, IndexValue
from app.models.index_layer import IndexMonthlyValue
from app.services.data_resolver import get_single_index_value_detailed

Y, Q = 2025, 2
REGION = "Europe"
# The months inside 2025 Q2.
M1, M2, M3 = 4, 5, 6


@pytest.fixture
def series(db):
    """Three platform commodities. No team CASCADE covers these, so they are
    cleaned up explicitly."""
    suf = uuid.uuid4().hex[:6]
    monthly_only = CommodityIndex(name=f"MonthlyOnly-{suf}", unit="$/mt",
                                  currency="USD", category="Chemical")
    quarterly = CommodityIndex(name=f"Quarterly-{suf}", unit="$/mt",
                               currency="USD", category="Chemical")
    both = CommodityIndex(name=f"Both-{suf}", unit="$/mt",
                          currency="USD", category="Chemical")
    db.add_all([monthly_only, quarterly, both])
    db.flush()
    ids = [monthly_only.id, quarterly.id, both.id]
    db.commit()
    yield monthly_only, quarterly, both
    db.rollback()
    db.query(IndexMonthlyValue).filter(
        IndexMonthlyValue.commodity_id.in_(ids)).delete(synchronize_session=False)
    db.query(IndexOverride).filter(
        IndexOverride.commodity_id.in_(ids)).delete(synchronize_session=False)
    db.query(IndexValue).filter(
        IndexValue.commodity_id.in_(ids)).delete(synchronize_session=False)
    db.query(CommodityIndex).filter(
        CommodityIndex.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


def _month(db, cid, year, month, value, kind="actual"):
    db.add(IndexMonthlyValue(commodity_id=cid, year=year, month=month,
                             value=value, kind=kind))


def test_a_monthly_only_series_resolves_at_the_quarter_mean(db, series, tenant_a):
    """AC1. This is the whole point: without it, three quarters of the catalog's
    cost lines have no value at all and every one of them rides flat."""
    m, _, _ = series
    for month, value in ((M1, 90), (M2, 100), (M3, 110)):
        _month(db, m.id, Y, month, value)
    db.commit()

    value, source = get_single_index_value_detailed(
        db, tenant_a["team_id"], m.id, REGION, Y, Q)
    # The drop's own quarterly rollups are the mean of the quarter's months, so
    # this reads the same way rather than inventing a second convention.
    assert value == pytest.approx(100.0)
    assert source == "monthly_actual"


def test_forecast_months_are_never_read(db, series, tenant_a):
    """AC2. 726 forecast rows sit in the same table as 3,822 actuals. A forecast
    in a historical should-cost is fabrication, not a fallback."""
    m, _, _ = series
    _month(db, m.id, Y, M1, 100, kind="actual")
    _month(db, m.id, Y, M2, 500, kind="forecast")
    _month(db, m.id, Y, M3, 900, kind="forecast")
    db.commit()

    value, source = get_single_index_value_detailed(
        db, tenant_a["team_id"], m.id, REGION, Y, Q)
    assert value == pytest.approx(100.0), "a forecast leaked into the mean"
    # And it is not passed off as a full quarter of observation.
    assert source == "monthly_partial_quarter"


def test_a_quarter_with_no_actuals_at_all_does_not_resolve_from_forecasts(
        db, series, tenant_a):
    """The harder half of AC2: with *only* forecasts in the quarter, the tier
    must decline rather than quietly serve one."""
    m, _, _ = series
    for month in (M1, M2, M3):
        _month(db, m.id, Y, month, 500, kind="forecast")
    db.commit()

    value, source = get_single_index_value_detailed(
        db, tenant_a["team_id"], m.id, REGION, Y, Q)
    assert value is None
    assert source is None


def test_every_existing_tier_still_wins(db, series, tenant_a):
    """AC3. The tier is additive — it must not reorder anything that already
    resolved. Checked against the quarterly tiers and against a team override,
    which sits far above both."""
    _, qtr, both = series

    # A quarterly value for the exact region beats a monthly one for the same period.
    db.add(IndexValue(commodity_id=both.id, region=REGION, year=Y, quarter=Q, value=42))
    for month in (M1, M2, M3):
        _month(db, both.id, Y, month, 999)
    db.commit()
    value, source = get_single_index_value_detailed(
        db, tenant_a["team_id"], both.id, REGION, Y, Q)
    assert value == pytest.approx(42.0)
    assert source == "scraped_region"

    # A team override beats everything below it, monthly included.
    db.add(IndexOverride(team_id=tenant_a["team_id"], commodity_id=both.id,
                         region=REGION, year=Y, quarter=Q, value=7,
                         uploaded_by=tenant_a["user_id"]))
    db.commit()
    value, source = get_single_index_value_detailed(
        db, tenant_a["team_id"], both.id, REGION, Y, Q)
    assert value == pytest.approx(7.0)
    assert source == "team_override"

    # A commodity with only quarterly data is untouched by any of this.
    db.add(IndexValue(commodity_id=qtr.id, region=REGION, year=Y, quarter=Q, value=55))
    db.commit()
    assert get_single_index_value_detailed(
        db, tenant_a["team_id"], qtr.id, REGION, Y, Q) == (55.0, "scraped_region")


def test_a_monthly_only_series_is_carried_forward_past_its_last_observation(
        db, series, tenant_a):
    """AC4. The quarterly store has carried values forward since long before this
    layer existed, so a future reference quarter does not flatten every ratio to
    1.0. A monthly-only series needs the same protection or it resolves for
    history and silently goes flat the moment a model reaches past its data."""
    m, _, _ = series
    for month, value in ((M1, 90), (M2, 100), (M3, 110)):
        _month(db, m.id, Y, month, value)
    db.commit()

    # Two years past the last observation.
    value, source = get_single_index_value_detailed(
        db, tenant_a["team_id"], m.id, REGION, Y + 2, 1)
    assert value == pytest.approx(110.0), "carried the wrong month forward"
    assert source == "monthly_carry_forward"

    # And it never runs backwards: a quarter before all history has nothing to
    # carry, which is a real answer, not a zero.
    assert get_single_index_value_detailed(
        db, tenant_a["team_id"], m.id, REGION, Y - 5, 1) == (None, None)


def test_a_partial_quarter_says_so(db, series, tenant_a):
    """AC5. Refusing a two-month quarter would report "no data" for a period that
    has data; presenting it as `monthly_actual` would claim three observations
    where there are two. Neither is acceptable, so it gets its own label."""
    m, _, _ = series
    _month(db, m.id, Y, M1, 100)
    _month(db, m.id, Y, M2, 200)
    db.commit()

    value, source = get_single_index_value_detailed(
        db, tenant_a["team_id"], m.id, REGION, Y, Q)
    assert value == pytest.approx(150.0)
    assert source == "monthly_partial_quarter"


def test_region_is_not_a_dimension_in_the_monthly_layer(db, series, tenant_a):
    """AC6. Region is baked into the series key here (`ammonia-eu` and
    `ammonia-in` are different series), so there is no region dimension to fall
    back through and the resolver must not invent one."""
    m, _, _ = series
    for month, value in ((M1, 90), (M2, 100), (M3, 110)):
        _month(db, m.id, Y, month, value)
    db.commit()

    for region in ("Europe", "NA", "GLOBAL", "APAC"):
        value, source = get_single_index_value_detailed(
            db, tenant_a["team_id"], m.id, region, Y, Q)
        assert value == pytest.approx(100.0), f"{region} resolved differently"
        assert source == "monthly_actual"
