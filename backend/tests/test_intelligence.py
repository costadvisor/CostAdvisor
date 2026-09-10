"""Intelligence derivation service (Wave 3, SCRUM-75 / INT-1).

The ticket's acceptance criteria, as tests:

1. the full derived payload for a (formula, region) combo, with **no CostModel
   and no team product** involved;
2. given a product, the same payload is reachable by resolving product → combo,
   and **the two paths return the same numbers**;
3. a combo with an unresolved cost line **names those lines**, and the trust
   grade in the payload is **the value stored by SCRUM-78**, asserted equal to
   the stored field;
4. a combo with no cost lines, or no base-period anchor, returns nulls and a
   **stated reason — not a 500**;
5. seasonality is 12 values; a combo whose lines carry no seasonal factors
   returns flat 100s; a combo mixing seasonal and fixed lines returns an
   amplitude **damped by the fixed share**;
6. the volatility percentile is reproducible from the stored calibration, and
   the payload **states which calibration** it was computed against;
7. the cycle-position **verdict text and window label come from the same
   constant**;
8. the output is inspectable as JSON for any combo, with no UI.

The ticket also asks for the awkward shapes to be pinned rather than the happy
path, so most of these build the shape they are about.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.cost_model import CostModel, FormulaVersion
from app.models.formula_template import (
    FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_data import CommodityIndex, IndexValue
from app.models.index_layer import IndexMonthlyValue
from app.models.index_seasonality import IndexSeasonalFactor
from app.models.product import Product
from app.services.formula_resolver import evaluate_weighted_template
from app.services.index_dossier import (
    active_calibration, percentile_for, recompute_volatility_calibration,
)
from app.services.index_seasonality import METHOD_RATIO_TO_CENTRED_MA12
from app.services.intelligence import (
    CYCLE_FLAT_SPREAD, CYCLE_HIGH, CYCLE_LOW, CYCLE_WINDOW_QUARTERS,
    LONG_WINDOW_QUARTERS, SHORT_WINDOW_QUARTERS, VERDICT_FLAT,
    VERDICT_NEAR_BOTTOM, VERDICT_NEAR_TOP, VERDICT_MID, combo_for_cost_model,
    cycle_verdict, cycle_window_label, derive,
)
from app.services.trust import apply_assessment

BASE = (2024, 1)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _series(db, *, quarterly=None, monthly=None, seasonal=None) -> CommodityIndex:
    """A commodity with whichever history the test needs.

    `quarterly` is `[(year, quarter, value)]` written to the legacy table the
    costing engine reads; `monthly` is `[(year, month, value)]` in the drop's
    layer; `seasonal` is 12 factors.
    """
    ci = CommodityIndex(name=f"int-{uuid.uuid4().hex[:8]}", scrape_enabled=False)
    db.add(ci)
    db.flush()
    for year, quarter, value in (quarterly or []):
        db.add(IndexValue(commodity_id=ci.id, region="Europe",
                          year=year, quarter=quarter, value=value))
    for year, month, value in (monthly or []):
        db.add(IndexMonthlyValue(commodity_id=ci.id, year=year, month=month,
                                 value=value, kind="actual"))
    if seasonal:
        for month, factor in enumerate(seasonal, start=1):
            db.add(IndexSeasonalFactor(
                commodity_id=ci.id, month=month, factor=factor,
                method=METHOD_RATIO_TO_CENTRED_MA12, window_months=42))
    db.commit()
    return ci


def _combo(db, created_by, *, lines, region="Europe", base=BASE, base_price=1000,
           margin_pct=None):
    """`lines` is `[(weight, commodity_or_None, component_type)]`."""
    tpl = FormulaTemplate(
        team_id=None, created_by=created_by,
        name=f"tpl-{uuid.uuid4().hex[:6]}", code=f"I-{uuid.uuid4().hex[:8]}",
        expression=None,
    )
    db.add(tpl)
    db.flush()
    cov = FormulaRegionCoverage(
        template_id=tpl.id, region=region, base_price=base_price,
        currency="USD", margin_pct=margin_pct,
        base_year=base[0] if base else None,
        base_quarter=base[1] if base else None,
    )
    db.add(cov)
    for i, (weight, commodity, ctype) in enumerate(lines):
        db.add(FormulaTemplateComponent(
            template_id=tpl.id, region=region, variant="",
            name=f"line-{i}", component_type=ctype,
            commodity_id=commodity.id if commodity else None,
            weight_pct=weight, sort_order=i,
        ))
    db.commit()
    return tpl, cov


def _quarters(base, values):
    """`[(year, quarter, value)]` walking forward from `base`."""
    out, (year, quarter) = [], base
    for value in values:
        out.append((year, quarter, value))
        quarter += 1
        if quarter > 4:
            year, quarter = year + 1, 1
    return out


def _cleanup(db, *, template_ids=(), series_ids=(), product_ids=(),
             cost_model_ids=()):
    db.rollback()
    bypass_rls_var.set(True)
    for cid in cost_model_ids:
        db.execute(text("DELETE FROM cost_models WHERE id = :i"), {"i": str(cid)})
    for pid in product_ids:
        db.execute(text("DELETE FROM products WHERE id = :i"), {"i": str(pid)})
    for tid in template_ids:
        db.execute(text("DELETE FROM formula_templates WHERE id = :i"), {"i": str(tid)})
    for sid in series_ids:
        for table in ("index_seasonal_factors", "index_monthly_values", "index_values"):
            db.execute(text(f"DELETE FROM {table} WHERE commodity_id = :i"), {"i": sid})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": sid})
    db.commit()


# ── 1. Combo grain, no CostModel ────────────────────────────────────────────

def test_the_payload_derives_from_a_combo_with_no_cost_model(db, tenant_a):
    """AC1. The Intelligence library renders the platform catalogue with region
    as a selector; an endpoint keyed on a product could not serve it at all."""
    s = _series(db, quarterly=_quarters(BASE, [100, 110, 120, 130]))
    tpl, cov = _combo(db, tenant_a["user_id"],
                      lines=[(70, s, "index"), (30, None, "fixed")])
    try:
        result = derive(db, tpl.id, "Europe")
        assert result.evaluable is True
        assert result.reason is None
        assert len(result.series) == 4
        # Base 100 by construction, rebasing on the recipe's own weight sum.
        assert result.series[0]["level"] == pytest.approx(100.0)
        # 70% of a +30% move: 100*(0.7*1.3 + 0.3*1.0) = 121
        assert result.series[-1]["level"] == pytest.approx(121.0)
        assert len(result.components) == 2
        assert sum(c["contribution_pct"] for c in result.components) == pytest.approx(
            result.series[-1]["level"], abs=0.01)
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[s.id])


def test_the_fast_path_agrees_with_the_canonical_single_period_evaluator(db, tenant_a):
    """The series is evaluated by flattening once rather than calling
    `evaluate_weighted_template` per quarter — so the two are pinned equal here,
    or the fast path could quietly drift from the canonical maths.

    Uses the legacy quarterly table on purpose: that is the store both can see.
    """
    s = _series(db, quarterly=_quarters(BASE, [100, 105, 118]))
    tpl, cov = _combo(db, tenant_a["user_id"],
                      lines=[(60, s, "index"), (40, None, "fixed")])
    try:
        result = derive(db, tpl.id, "Europe")
        last = result.series[-1]
        canonical = evaluate_weighted_template(
            db, None, tpl.id, "Europe", last["year"], last["quarter"])
        assert canonical["evaluable"] is True
        assert last["level"] == pytest.approx(canonical["index_level_pct"], abs=0.01)
        assert result.value_sources["matches_costing_engine"] is True
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[s.id])


def test_margin_inside_the_hundred_is_not_applied_twice(db, tenant_a):
    """`coverage.margin_pct` is descriptive — the margin line is already inside
    the recipe, and applying the percentage again would double-count it."""
    s = _series(db, quarterly=_quarters(BASE, [100, 200]))
    tpl, cov = _combo(db, tenant_a["user_id"],
                      lines=[(90, s, "index"), (10, None, "fixed")],
                      margin_pct=10)
    try:
        result = derive(db, tpl.id, "Europe")
        # 100*(0.9*2.0 + 0.1*1.0) = 190 — not 190*1.1.
        assert result.series[-1]["level"] == pytest.approx(190.0)
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[s.id])


def test_the_drops_monthly_series_is_visible_and_the_store_is_named(db, tenant_a):
    """The drop's 121 series landed in the monthly layer, not the legacy
    quarterly table, so without this the series would be empty for nearly every
    catalog combo.

    `data_resolver` has since gained a monthly tier, so reading that store is no
    longer a divergence from the costing engine — both read it, both take the
    quarter mean of the actual months. The payload still names the store: it is
    the one place a caller can see where a level came from, and this test is what
    would catch the two sides drifting apart again.
    """
    monthly = [(2024, m, 100.0) for m in range(1, 4)] + \
              [(2024, m, 150.0) for m in range(4, 7)]
    s = _series(db, monthly=monthly)
    tpl, cov = _combo(db, tenant_a["user_id"], lines=[(100, s, "index")])
    try:
        result = derive(db, tpl.id, "Europe")
        assert len(result.series) == 2
        assert result.series[-1]["level"] == pytest.approx(150.0)
        assert result.components[0]["value_source"] == "index_monthly_values"
        assert result.value_sources["by_store"]["index_monthly_values"] == 1
        # No longer a divergence: the costing engine reads this store too.
        assert result.value_sources["matches_costing_engine"] is True
        assert "the two agree" in result.value_sources["note"]

        # And the two really do agree, rather than merely claiming to — the
        # resolver is asked directly for the same period the combo evaluated.
        from app.services.data_resolver import get_single_index_value_detailed
        engine_value, engine_source = get_single_index_value_detailed(
            db, tenant_a["team_id"], s.id, "Europe", 2024, 2)
        assert engine_source == "monthly_actual"
        assert engine_value == pytest.approx(150.0)
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[s.id])


# ── 2. Product → combo ──────────────────────────────────────────────────────

def test_a_product_reaches_the_same_numbers(db, tenant_a, client_as):
    """AC2. The product is not the thing being derived; it is how Portfolio gets
    to the combo."""
    s = _series(db, quarterly=_quarters(BASE, [100, 140]))
    tpl, cov = _combo(db, tenant_a["user_id"], lines=[(100, s, "index")])
    product = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"],
                      created_by=tenant_a["user_id"], name="P", unit="kg",
                      formula_template_id=tpl.id)
    db.add(product)
    db.flush()
    cm = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"],
                   product_id=product.id, created_by=tenant_a["user_id"],
                   region="Europe", currency="USD")
    db.add(cm)
    db.commit()
    try:
        ref = combo_for_cost_model(db, cm)
        assert ref is not None
        assert ref.template_id == tpl.id and ref.region == "Europe"
        assert "product.formula_template_id" in ref.via

        c = client_as(tenant_a)
        by_combo = c.get(f"/api/intelligence/combos/{tpl.id}/Europe"
                         f"?team_id={tenant_a['team_id']}").json()
        by_product = c.get(f"/api/intelligence/cost-models/{cm.id}"
                           f"?team_id={tenant_a['team_id']}").json()
        assert by_product["resolved_via"]
        # The same numbers, not merely the same shape.
        for field in ("series", "components", "change", "cycle", "seasonality"):
            assert by_product[field] == by_combo[field]
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[s.id],
                 product_ids=[product.id], cost_model_ids=[cm.id])


def test_a_product_with_no_catalog_link_is_told_why(db, tenant_a, client_as):
    product = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"],
                      created_by=tenant_a["user_id"], name="Unlinked", unit="kg")
    db.add(product)
    db.flush()
    cm = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"],
                   product_id=product.id, created_by=tenant_a["user_id"],
                   region="Europe", currency="USD")
    db.add(cm)
    db.commit()
    try:
        assert combo_for_cost_model(db, cm) is None
        r = client_as(tenant_a).get(f"/api/intelligence/cost-models/{cm.id}"
                                    f"?team_id={tenant_a['team_id']}")
        assert r.status_code == 422
        assert "catalog formula" in r.json()["detail"]
    finally:
        _cleanup(db, product_ids=[product.id], cost_model_ids=[cm.id])


# ── 3. Unresolved lines + the stored trust grade ────────────────────────────

def test_unresolved_lines_are_named_and_the_trust_grade_is_the_stored_one(
        db, tenant_a):
    """AC3. The grade is SCRUM-78's derivation and stored field — this engine
    reads it and reports the inputs it was computed from, never recomputes it."""
    good = _series(db, quarterly=_quarters(BASE, [100, 120]))
    dry = _series(db)      # no history at all
    tpl, cov = _combo(db, tenant_a["user_id"],
                      lines=[(60, good, "index"), (40, dry, "index")])
    try:
        apply_assessment(db, cov)
        db.commit()

        result = derive(db, tpl.id, "Europe")
        # The unresolved line is named, not silently ridden flat.
        assert [g["line"] for g in result.data_gaps] == ["line-1"]
        assert result.data_gaps[0]["commodity_id"] == dry.id
        flat = next(c for c in result.components if c["commodity_id"] == dry.id)
        assert flat["has_data"] is False
        assert flat["ratio"] == pytest.approx(1.0)

        db.expire_all()
        stored = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.id == cov.id).one()
        assert result.trust["grade"] == stored.trust_grade
        assert result.trust["needs_review"] == stored.needs_review
        assert result.trust["inputs"] == stored.trust_inputs
        assert "SCRUM-78" in result.trust["source"]
        # The proxy-status column SCRUM-78 canonicalised is echoed, not chosen.
        assert result.trust["proxy_status_source"] == (
            stored.trust_inputs or {}).get("proxy_status_source")
        # And the coverage vocabularies stay separate from the grade.
        assert result.trust["coverage_tier"] == stored.coverage_tier
        assert result.trust["proxy_density_tier"] == stored.proxy_density_tier
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[good.id, dry.id])


# ── 4. Not everything has an answer ─────────────────────────────────────────

def test_a_combo_with_no_lines_gets_a_reason_not_a_500(db, tenant_a, client_as):
    """AC4. The mockup throws on these; that behaviour is not worth porting."""
    tpl, cov = _combo(db, tenant_a["user_id"], lines=[])
    try:
        r = client_as(tenant_a).get(f"/api/intelligence/combos/{tpl.id}/Europe"
                                    f"?team_id={tenant_a['team_id']}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["evaluable"] is False
        assert body["reason"] == "no weighted lines"
        assert body["series"] == []
        assert body["cycle"] is None
    finally:
        _cleanup(db, template_ids=[tpl.id])


def test_a_combo_with_no_base_anchor_gets_a_reason(db, tenant_a):
    s = _series(db, quarterly=_quarters(BASE, [100, 120]))
    tpl, cov = _combo(db, tenant_a["user_id"], lines=[(100, s, "index")], base=None)
    try:
        result = derive(db, tpl.id, "Europe")
        assert result.evaluable is False
        assert result.reason == "coverage has no base period anchor"
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[s.id])


def test_a_combo_with_no_base_price_is_still_a_level(db, tenant_a):
    """The series is a level, not a price — money only appears where the combo
    carries an anchor."""
    s = _series(db, quarterly=_quarters(BASE, [100, 125]))
    tpl, cov = _combo(db, tenant_a["user_id"], lines=[(100, s, "index")],
                      base_price=None)
    try:
        result = derive(db, tpl.id, "Europe")
        assert result.evaluable is True
        assert result.reason == "no base price anchor — index level only"
        assert result.series[-1]["level"] == pytest.approx(125.0)
        assert all(c["contribution_abs"] is None for c in result.components)
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[s.id])


def test_an_unknown_combo_is_a_reason_not_a_crash(db, tenant_a):
    result = derive(db, uuid.uuid4(), "Europe")
    assert result.evaluable is False
    assert result.reason == "unknown formula template"


# ── 5. Seasonality ──────────────────────────────────────────────────────────

def test_seasonality_is_twelve_values_and_flat_without_factors(db, tenant_a):
    """AC5, first half."""
    s = _series(db, quarterly=_quarters(BASE, [100, 110]))
    tpl, cov = _combo(db, tenant_a["user_id"], lines=[(100, s, "index")])
    try:
        result = derive(db, tpl.id, "Europe")
        assert len(result.seasonality["factors"]) == 12
        assert result.seasonality["factors"] == [100.0] * 12
        assert result.seasonality["spread"] == pytest.approx(0.0)
        assert result.seasonality["seasonal_weight_pct"] == pytest.approx(0.0)
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[s.id])


def test_a_fixed_share_damps_the_seasonal_amplitude(db, tenant_a):
    """AC5, second half — and the reason the rule exists. A combo that is
    largely fixed cost genuinely has a flatter profile than its feedstock;
    dropping those lines instead of contributing 100 would inflate the amplitude
    of exactly the combos that should look calmest."""
    # Normalised to mean exactly 100, which is what `compute_factors` guarantees
    # in production — a hand-written profile that averages 98 makes the blend's
    # mean 99 and the invariant below meaningless.
    raw = [90, 92, 96, 100, 104, 110, 106, 102, 98, 96, 92, 90]
    profile = [round(v * 12 / sum(raw), 4) * 100 for v in raw]
    profile = [round(v, 4) for v in profile]
    s = _series(db, quarterly=_quarters(BASE, [100, 110]), seasonal=profile)
    pure_tpl, _ = _combo(db, tenant_a["user_id"], lines=[(100, s, "index")])
    mixed_tpl, _ = _combo(db, tenant_a["user_id"],
                          lines=[(50, s, "index"), (50, None, "fixed")])
    try:
        pure = derive(db, pure_tpl.id, "Europe").seasonality
        mixed = derive(db, mixed_tpl.id, "Europe").seasonality

        assert pure["factors"] == pytest.approx(profile, abs=0.01)
        assert pure["seasonal_weight_pct"] == pytest.approx(100.0)
        assert mixed["seasonal_weight_pct"] == pytest.approx(50.0)
        # Exactly half the amplitude, because the fixed half contributes flat.
        assert mixed["spread"] == pytest.approx(pure["spread"] / 2, abs=0.02)
        assert mixed["peak_month"] == pure["peak_month"]
        # Still centred on 100 — the flat lines pull toward the mean, not off it.
        assert sum(mixed["factors"]) / 12 == pytest.approx(100.0, abs=0.05)
    finally:
        _cleanup(db, template_ids=[pure_tpl.id, mixed_tpl.id], series_ids=[s.id])


# ── 6. Volatility ───────────────────────────────────────────────────────────

def test_the_volatility_percentile_is_reproducible_and_names_its_calibration(
        db, tenant_a):
    """AC6. The ladder is DB-7's; this engine reads it and says which one."""
    monthly = [(2024 + i // 12, (i % 12) + 1, 100 + (8 if i % 2 else -8))
               for i in range(30)]
    s = _series(db, quarterly=_quarters(BASE, [100, 110]), monthly=monthly)
    # A few calm series so the ladder has a distribution to fit over.
    calm = [_series(db, monthly=[(2024 + i // 12, (i % 12) + 1, 100 + i * (n + 1) * 0.05)
                                 for i in range(30)])
            for n in range(4)]
    tpl, cov = _combo(db, tenant_a["user_id"], lines=[(100, s, "index")])
    try:
        calibration = recompute_volatility_calibration(db, n_rungs=11, min_points=13)
        db.commit()

        result = derive(db, tpl.id, "Europe")
        vol = result.volatility
        assert vol["dispersion"] is not None
        assert vol["percentile"] is not None
        # Says which ladder produced it — SCRUM-75 reports, DB-7 owns.
        assert vol["calibration_id"] == calibration.id
        assert vol["calibration_computed_at"] is not None
        assert vol["method"] == calibration.method
        # And it is reproducible from the stored ladder.
        assert vol["percentile"] == percentile_for(vol["dispersion"], calibration)
    finally:
        _cleanup(db, template_ids=[tpl.id],
                 series_ids=[s.id] + [c.id for c in calm])


def test_a_combo_with_no_monthly_history_says_so(db, tenant_a):
    """"Not measurable" is not "calm" — the same rule the ladder itself holds."""
    s = _series(db, quarterly=_quarters(BASE, [100, 110]))
    tpl, cov = _combo(db, tenant_a["user_id"], lines=[(100, s, "index")])
    try:
        vol = derive(db, tpl.id, "Europe").volatility
        assert vol["percentile"] is None
        assert vol["dispersion"] is None
        assert vol["reason"] and "not the same as calm" in vol["reason"]
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[s.id])


def test_a_fixed_share_damps_the_volatility_too(db, tenant_a):
    """Same damping rule as seasonality, for the same reason."""
    monthly = [(2024 + i // 12, (i % 12) + 1, 100 + (10 if i % 2 else -10))
               for i in range(30)]
    s = _series(db, monthly=monthly)
    calm = [_series(db, monthly=[(2024 + i // 12, (i % 12) + 1, 100 + i * 0.05)
                                 for i in range(30)]) for _ in range(3)]
    pure_tpl, _ = _combo(db, tenant_a["user_id"], lines=[(100, s, "index")])
    mixed_tpl, _ = _combo(db, tenant_a["user_id"],
                          lines=[(40, s, "index"), (60, None, "fixed")])
    try:
        recompute_volatility_calibration(db, n_rungs=11, min_points=13)
        db.commit()
        pure = derive(db, pure_tpl.id, "Europe").volatility
        mixed = derive(db, mixed_tpl.id, "Europe").volatility
        assert pure["dispersion"] > mixed["dispersion"]
        assert mixed["monthly_weight_pct"] == pytest.approx(40.0)
    finally:
        _cleanup(db, template_ids=[pure_tpl.id, mixed_tpl.id],
                 series_ids=[s.id] + [c.id for c in calm])


# ── 7. The one window constant ──────────────────────────────────────────────

def test_the_verdict_and_the_window_label_come_from_the_same_constant():
    """AC7, stated verbatim in the ticket. The bug this prevents: the frontend
    computes the percentile over whatever history exists while hardcoding
    "24-month", and the mockup does the same."""
    label = cycle_window_label()
    assert label == f"{CYCLE_WINDOW_QUARTERS * 3}-month"
    for percentile, expected in ((95.0, VERDICT_NEAR_TOP),
                                 (55.0, VERDICT_MID),
                                 (5.0, VERDICT_NEAR_BOTTOM)):
        verdict, sentence = cycle_verdict(percentile, spread=50.0)
        assert verdict == expected
        # Every sentence carries the label generated from the same constant.
        assert label in sentence
    flat_verdict, flat_sentence = cycle_verdict(50.0, spread=0.1)
    assert flat_verdict == VERDICT_FLAT
    assert label in flat_sentence


def test_the_thresholds_are_three_verdicts_at_70_and_40():
    assert CYCLE_HIGH == 70.0 and CYCLE_LOW == 40.0
    assert cycle_verdict(70.0, 50.0)[0] == VERDICT_NEAR_TOP
    assert cycle_verdict(69.9, 50.0)[0] == VERDICT_MID
    assert cycle_verdict(40.0, 50.0)[0] == VERDICT_NEAR_BOTTOM
    assert cycle_verdict(40.1, 50.0)[0] == VERDICT_MID


def test_a_flat_series_gets_the_fourth_case(db, tenant_a):
    """The percentile formula cannot express "has not moved" — a 0.1-point range
    has no top and no bottom, and a naive percentile would report one."""
    s = _series(db, quarterly=_quarters(BASE, [100, 100.2, 100.1, 100.3]))
    tpl, cov = _combo(db, tenant_a["user_id"], lines=[(100, s, "index")])
    try:
        cycle = derive(db, tpl.id, "Europe").cycle
        assert cycle["spread"] < CYCLE_FLAT_SPREAD
        assert cycle["verdict"] == VERDICT_FLAT
        assert cycle["window_label"] == cycle_window_label()
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[s.id])


def test_the_cycle_reads_a_rising_series_as_near_the_top(db, tenant_a):
    s = _series(db, quarterly=_quarters(BASE, [100, 105, 110, 120, 135]))
    tpl, cov = _combo(db, tenant_a["user_id"], lines=[(100, s, "index")])
    try:
        result = derive(db, tpl.id, "Europe")
        assert result.cycle["percentile"] == pytest.approx(100.0)
        assert result.cycle["verdict"] == VERDICT_NEAR_TOP
        assert result.cycle["periods_used"] == len(result.series)
        assert result.change["short_window_quarters"] == SHORT_WINDOW_QUARTERS
        assert result.change["long_window_quarters"] == LONG_WINDOW_QUARTERS
        # +12.5% over one quarter (120 -> 135).
        assert result.change["short_pct"] == pytest.approx(12.5)
        # The long window is wider than the history, so it is None, not zero.
        assert result.change["long_pct"] is None
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[s.id])


# ── 8. Inspectable as JSON, and the API surface ─────────────────────────────

def test_the_payload_is_inspectable_as_json_for_any_combo(db, tenant_a, client_as):
    """AC8. Every block present or explicitly absent, with no UI."""
    s = _series(db, quarterly=_quarters(BASE, [100, 130]))
    tpl, cov = _combo(db, tenant_a["user_id"], lines=[(100, s, "index")])
    try:
        r = client_as(tenant_a).get(f"/api/intelligence/combos/{tpl.id}/Europe"
                                    f"?team_id={tenant_a['team_id']}")
        assert r.status_code == 200, r.text
        body = r.json()
        for block in ("series", "components", "change", "cycle", "seasonality",
                      "volatility", "trust", "data_gaps", "value_sources"):
            assert block in body
        assert body["template_code"] == tpl.code
        assert body["coverage_region"] == "Europe"
    finally:
        _cleanup(db, template_ids=[tpl.id], series_ids=[s.id])


def test_the_batch_endpoint_serves_a_page_of_tiles(db, tenant_a, client_as):
    """One request per visible tile is what does not scale to the platform
    catalogue."""
    s = _series(db, quarterly=_quarters(BASE, [100, 115]))
    tpls = [_combo(db, tenant_a["user_id"], lines=[(100, s, "index")])[0]
            for _ in range(3)]
    try:
        r = client_as(tenant_a).post(
            f"/api/intelligence/combos?team_id={tenant_a['team_id']}",
            json={"combos": [{"template_id": str(t.id), "region": "Europe"}
                             for t in tpls]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 3
        assert {row["template_id"] for row in body["results"]} == {
            str(t.id) for t in tpls}
        # And it is capped, so it does not become the same problem larger.
        too_many = client_as(tenant_a).post(
            f"/api/intelligence/combos?team_id={tenant_a['team_id']}",
            json={"combos": [{"template_id": str(tpls[0].id), "region": "Europe"}] * 51},
        )
        assert too_many.status_code == 422
    finally:
        _cleanup(db, template_ids=[t.id for t in tpls], series_ids=[s.id])


def test_intelligence_endpoints_require_authentication(client):
    assert client.get(
        f"/api/intelligence/combos/{uuid.uuid4()}/Europe"
        f"?team_id={uuid.uuid4()}").status_code == 401


def test_an_unknown_template_is_404_not_an_empty_payload(db, tenant_a, client_as):
    r = client_as(tenant_a).get(
        f"/api/intelligence/combos/{uuid.uuid4()}/Europe"
        f"?team_id={tenant_a['team_id']}")
    assert r.status_code == 404


def test_the_query_budget_does_not_grow_with_the_number_of_periods(db, tenant_a):
    """The read-path decision, asserted: the series is evaluated by flattening
    once and reading each commodity's whole history in one query, so a longer
    window costs no more queries. Calling `evaluate_weighted_template` per
    quarter would have been one flatten and one read per line per period."""
    from sqlalchemy import event

    short = _series(db, quarterly=_quarters(BASE, [100, 110]))
    long = _series(db, quarterly=_quarters(BASE, [100 + i for i in range(12)]))
    short_tpl, _ = _combo(db, tenant_a["user_id"], lines=[(100, short, "index")])
    long_tpl, _ = _combo(db, tenant_a["user_id"], lines=[(100, long, "index")])

    def count(fn):
        calls = []
        engine = db.get_bind()

        def before(conn, cursor, statement, params, context, many):
            calls.append(statement)

        event.listen(engine, "before_cursor_execute", before)
        try:
            fn()
        finally:
            event.remove(engine, "before_cursor_execute", before)
        return len(calls)

    try:
        db.expire_all()
        short_result = derive(db, short_tpl.id, "Europe")
        db.expire_all()
        n_short = count(lambda: derive(db, short_tpl.id, "Europe"))
        db.expire_all()
        long_result = derive(db, long_tpl.id, "Europe")
        db.expire_all()
        n_long = count(lambda: derive(db, long_tpl.id, "Europe"))

        assert len(short_result.series) == 2
        assert len(long_result.series) == 12
        assert n_long == n_short, (
            f"{n_short} queries for 2 periods vs {n_long} for 12 — the read is "
            "scaling with the window"
        )
    finally:
        _cleanup(db, template_ids=[short_tpl.id, long_tpl.id],
                 series_ids=[short.id, long.id])
