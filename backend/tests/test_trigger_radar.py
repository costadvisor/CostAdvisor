"""Trigger radar (Wave 3, SCRUM-79 / MON-1).

Every acceptance criterion in the ticket, as a test:

1. a contract with auto-renew and a notice period returns its computed notice
   deadline, and a radar run opens a window closing on that deadline with
   `close_basis` naming the clause;
2. a move on a series that several type codes resolve to produces **one**
   window with the affected cost models attached — the count is asserted, not
   just that something fired;
3. a cost model with a line on a type code that has no series reports coverage
   `unknown`, and the payload names the unresolved code;
4. the single-window payload carries driver, evidence, threshold + unit, the
   line -> type code -> series path with proxy state, and open/close + basis;
5. the effective threshold comes from one accessor — changing the team default
   moves the boundary for a non-overridden subscription and leaves an
   overridden one alone (both halves asserted);
6. a closed window does not deliver;
7. the beat-schedule task names resolve to registered Celery tasks;
8. subscribing to the window trigger through the existing alerts API works end
   to end and writes an `alert_events` row.

Plus the decision the ticket asks to be written down and not done silently: the
radar **suggests** a negotiation state and never sets it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.database import SessionLocal, bypass_rls_var, current_user_id_var
from app.models.alerts import AlertEvent, AlertSubscription
from app.models.contract import Contract, ContractClause, ContractCostModel, compute_notice_deadline
from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.index_data import CommodityIndex, IndexValue
from app.models.index_layer import TypeCode
from app.models.product import Product
from app.models.radar import MarketSignal, NegotiationWindow, NegotiationWindowCostModel
from app.models.rbac import Permission, Role, RolePermission, TeamMemberRole
from app.models.supplier import Supplier
from app.models.team import Team, TeamMembership
from app.services.thresholds import effective_threshold
from app.services.trigger_radar import (
    COVERAGE_COVERED, COVERAGE_PARTIAL, COVERAGE_UNKNOWN, SUGGESTS_NOT_SETS,
    model_coverage, run_radar,
)

REGION = "Europe"
PREV_Y, PREV_Q = 2026, 1
CUR_Y, CUR_Q = 2026, 2
TODAY = date(2026, 8, 28)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _series(db, name_hint: str, *, levels=None) -> CommodityIndex:
    """A reference series. No `commodity_key` — that is the drop's namespace."""
    ci = CommodityIndex(name=f"{name_hint}-{uuid.uuid4().hex[:8]}", currency="USD", unit="t")
    db.add(ci)
    db.flush()
    for (y, q, v) in (levels or []):
        db.add(IndexValue(commodity_id=ci.id, region=REGION, year=y, quarter=q, value=v))
    db.commit()
    return ci


def _code(db, series, *, resolution="resolved", proxy_status="direct") -> TypeCode:
    tc = TypeCode(
        code=f"TC-{uuid.uuid4().hex[:8]}", resolution=resolution,
        resolves_to_id=series.id if series else None, proxy_status=proxy_status,
    )
    db.add(tc)
    db.commit()
    return tc


def _cost_model(db, tenant, *, series_weights, supplier=None, product_name="Product",
                unbound_index_line=False):
    """A runnable cost model. `series_weights` is [(CommodityIndex, weight)].

    `unbound_index_line` adds an index-linked line with no bound series — the
    shape a catalog line on an `ambiguous` type code freezes into, and the one
    that must read as a blind spot rather than as calm.
    """
    product = Product(
        id=uuid.uuid4(), team_id=tenant["team_id"], created_by=tenant["user_id"],
        name=f"{product_name}-{uuid.uuid4().hex[:4]}", unit="kg",
    )
    db.add(product)
    db.flush()
    cm = CostModel(
        id=uuid.uuid4(), team_id=tenant["team_id"], product_id=product.id,
        supplier_id=supplier.id if supplier else None, created_by=tenant["user_id"],
        region=REGION, currency="USD",
    )
    db.add(cm)
    db.flush()
    fv = FormulaVersion(
        cost_model_id=cm.id, base_price=100, base_year=PREV_Y, base_quarter=PREV_Q,
        formula_type="simple", margin_type="pct", margin_value=0,
    )
    db.add(fv)
    db.flush()
    for series, weight in series_weights:
        db.add(FormulaComponent(
            formula_version_id=fv.id, label=f"line-{series.name[:12]}",
            commodity_id=series.id, weight=weight, component_type="index",
        ))
    if unbound_index_line:
        db.add(FormulaComponent(
            formula_version_id=fv.id, label="Undecided code",
            commodity_id=None, weight=1.0, component_type="index",
        ))
    db.commit()
    return cm


def _contract(db, tenant, cm, supplier, *, notice_days=30, term_end=None,
              auto_renew=True) -> Contract:
    c = Contract(
        team_id=tenant["team_id"], supplier_id=supplier.id if supplier else None,
        reference=f"CT-{uuid.uuid4().hex[:6]}",
        term_start=TODAY - timedelta(days=300),
        term_end=term_end or (TODAY + timedelta(days=60)),
        auto_renew=auto_renew, notice_days=notice_days,
        price_review_cadence="annual", created_by=tenant["user_id"],
    )
    c.refresh_notice_deadline()
    db.add(c)
    db.flush()
    db.add(ContractClause(team_id=tenant["team_id"], contract_id=c.id,
                          clause_type="notice", label="Notice of non-renewal",
                          body=f"{notice_days} days written notice."))
    if cm is not None:
        db.add(ContractCostModel(team_id=tenant["team_id"], contract_id=c.id,
                                 cost_model_id=cm.id))
    db.commit()
    return c


def _cleanup(db, *, cost_model_ids=(), series_ids=(), code_ids=()):
    db.rollback()
    bypass_rls_var.set(True)
    for cid in cost_model_ids:
        db.execute(text("DELETE FROM cost_models WHERE id = :i"), {"i": str(cid)})
    for cid in code_ids:
        db.execute(text("DELETE FROM type_codes WHERE id = :i"), {"i": cid})
    for sid in series_ids:
        db.execute(text("DELETE FROM index_values WHERE commodity_id = :i"), {"i": sid})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": sid})
    db.commit()


# ── 1. Clause deadline ───────────────────────────────────────────────────────

def test_notice_deadline_is_term_end_minus_notice_period():
    assert compute_notice_deadline(date(2026, 12, 31), 30) == date(2026, 12, 1)
    # An absent deadline is a real state (a contract with no notice clause),
    # not a zero to fill in.
    assert compute_notice_deadline(None, 30) is None
    assert compute_notice_deadline(date(2026, 12, 31), None) is None


def test_contract_endpoint_returns_the_computed_deadline(db, tenant_a, client_as):
    sup = Supplier(team_id=tenant_a["team_id"], name="Notice Co")
    db.add(sup)
    db.commit()
    cm = _cost_model(db, tenant_a, series_weights=[], supplier=sup)
    try:
        r = client_as(tenant_a).post(
            f"/api/contracts?team_id={tenant_a['team_id']}",
            json={
                "supplier_id": sup.id,
                "reference": "AR-2026",
                "term_start": "2026-01-01",
                "term_end": "2026-12-31",
                "auto_renew": True,
                "notice_days": 45,
                "price_review_cadence": "annual",
                "cost_model_ids": [str(cm.id)],
                "clauses": [{"clause_type": "notice", "label": "Non-renewal notice",
                             "body": "45 days written notice."}],
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["notice_deadline"] == "2026-11-16"     # 2026-12-31 minus 45 days
        assert body["auto_renew"] is True
        # Flushed before serializing, so the response reflects what was written.
        assert len(body["clauses"]) == 1
        assert [c["cost_model_id"] for c in body["covered"]] == [str(cm.id)]

        # Editing term_end must move the stored deadline — it is derived, so the
        # two can never be allowed to disagree.
        r2 = client_as(tenant_a).put(
            f"/api/contracts/{body['id']}", json={"term_end": "2027-06-30"},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["notice_deadline"] == "2027-05-16"
    finally:
        _cleanup(db, cost_model_ids=[cm.id])


def test_radar_opens_a_clause_window_closing_on_the_notice_deadline(db, tenant_a):
    """AC1. The window closes on the deadline and the basis names the clause —
    it is not a synthesised date."""
    sup = Supplier(team_id=tenant_a["team_id"], name="Renewal Co")
    db.add(sup)
    db.commit()
    cm = _cost_model(db, tenant_a, series_weights=[], supplier=sup)
    c = _contract(db, tenant_a, cm, sup, notice_days=30,
                  term_end=TODAY + timedelta(days=60))
    try:
        run_radar(db, tenant_a["team_id"], today=TODAY)
        win = (
            db.query(NegotiationWindow)
            .filter(NegotiationWindow.team_id == tenant_a["team_id"],
                    NegotiationWindow.driver == "clause_deadline")
            .one()
        )
        assert win.closes_on == c.notice_deadline == TODAY + timedelta(days=30)
        assert win.close_basis == "clause_deadline"
        assert win.state == "open"
        assert win.evidence["auto_renew"] is True
        assert win.evidence["days_remaining"] == 30
        # The covered product rides on the window.
        assert [p.cost_model_id for p in win.products] == [cm.id]
    finally:
        _cleanup(db, cost_model_ids=[cm.id])


def test_a_deadline_beyond_the_lookahead_does_not_open_a_window(db, tenant_a):
    sup = Supplier(team_id=tenant_a["team_id"], name="Far Co")
    db.add(sup)
    db.commit()
    _contract(db, tenant_a, None, sup, notice_days=30,
              term_end=TODAY + timedelta(days=900))
    run_radar(db, tenant_a["team_id"], today=TODAY)
    assert db.query(NegotiationWindow).filter(
        NegotiationWindow.team_id == tenant_a["team_id"],
        NegotiationWindow.driver == "clause_deadline",
    ).count() == 0


# ── 2. One window per driver, not per product ────────────────────────────────

def test_one_series_move_opens_exactly_one_window_across_many_products(db, tenant_a):
    """AC2, and the whole reason the window exists.

    Three type codes resolve to one series and three products name it. The
    shipped alert rule walks each team cost model and each referenced index
    independently, so it would fire three near-identical events; the window
    groups by driver, so there is one.
    """
    series = _series(db, "brentish", levels=[
        (PREV_Y, PREV_Q, 100), (CUR_Y, CUR_Q, 130),      # +30%
    ])
    codes = [_code(db, series) for _ in range(3)]
    models = [
        _cost_model(db, tenant_a, series_weights=[(series, 1.0)], product_name=f"P{i}")
        for i in range(3)
    ]
    try:
        run_radar(db, tenant_a["team_id"], today=TODAY)
        wins = (
            db.query(NegotiationWindow)
            .filter(NegotiationWindow.team_id == tenant_a["team_id"],
                    NegotiationWindow.driver == "index_move")
            .all()
        )
        assert len(wins) == 1, [w.headline for w in wins]
        win = wins[0]
        assert win.scope_type == "commodity"
        assert win.scope_commodity_id == series.id
        # Every affected product attached — the grouping keeps the blast radius,
        # it does not discard it.
        assert {p.cost_model_id for p in win.products} == {m.id for m in models}
        assert win.evidence["move_pct"] == pytest.approx(30.0)
        assert win.evidence["direction"] == "up"
        # Many labels, one driver.
        assert len(win.evidence["type_codes_resolving_here"]) >= 1
    finally:
        _cleanup(db, cost_model_ids=[m.id for m in models],
                 code_ids=[c.id for c in codes], series_ids=[series.id])


def test_a_move_below_the_threshold_opens_nothing(db, tenant_a):
    series = _series(db, "calm", levels=[
        (PREV_Y, PREV_Q, 100), (CUR_Y, CUR_Q, 102),      # +2%, under the 10% default
    ])
    cm = _cost_model(db, tenant_a, series_weights=[(series, 1.0)])
    try:
        run_radar(db, tenant_a["team_id"], today=TODAY)
        assert db.query(NegotiationWindow).filter(
            NegotiationWindow.team_id == tenant_a["team_id"],
            NegotiationWindow.driver == "index_move",
        ).count() == 0
    finally:
        _cleanup(db, cost_model_ids=[cm.id], series_ids=[series.id])


def test_a_second_run_refreshes_rather_than_duplicating(db, tenant_a):
    """A driver still true tomorrow is the same window. A second row would
    double-count the opportunity and re-deliver it."""
    series = _series(db, "again", levels=[
        (PREV_Y, PREV_Q, 100), (CUR_Y, CUR_Q, 140),
    ])
    cm = _cost_model(db, tenant_a, series_weights=[(series, 1.0)])
    try:
        first = run_radar(db, tenant_a["team_id"], today=TODAY)
        assert first.summary["opened"] == 1
        second = run_radar(db, tenant_a["team_id"], today=TODAY)
        assert second.summary["opened"] == 0
        assert second.summary["refreshed"] == 1
        assert db.query(NegotiationWindow).filter(
            NegotiationWindow.team_id == tenant_a["team_id"]).count() == 1
        # And the product list is rebuilt, not duplicated.
        assert db.query(NegotiationWindowCostModel).filter(
            NegotiationWindowCostModel.team_id == tenant_a["team_id"]).count() == 1
    finally:
        _cleanup(db, cost_model_ids=[cm.id], series_ids=[series.id])


# ── 3. Coverage is tri-valued ───────────────────────────────────────────────

def test_a_no_series_type_code_makes_coverage_unknown_not_calm(db, tenant_a, client_as):
    """AC3. The failure mode this prevents: a comparison against a missing value
    returns falsey, so a two-state check reports a product whose only cost line
    has never had a price as calm forever."""
    dry = _series(db, "unbought")            # resolved target, but no values
    code = _code(db, dry, resolution="no_series")
    cm = _cost_model(db, tenant_a, series_weights=[(dry, 1.0)])
    try:
        cov = model_coverage(db, db.query(CostModel).filter(CostModel.id == cm.id).one())
        assert cov.coverage == COVERAGE_UNKNOWN
        assert code.code in cov.unresolved_codes

        r = client_as(tenant_a).get(f"/api/radar/coverage?team_id={tenant_a['team_id']}")
        assert r.status_code == 200, r.text
        row = next(m for m in r.json()["models"] if m["cost_model_id"] == str(cm.id))
        assert row["coverage"] == COVERAGE_UNKNOWN
        # The payload names the code — "buy this feed" is actionable, "no
        # signal" is not.
        assert code.code in row["unresolved_type_codes"]
        assert row["resolved_lines"] == 0 and row["total_index_lines"] == 1
    finally:
        _cleanup(db, cost_model_ids=[cm.id], code_ids=[code.id], series_ids=[dry.id])


def test_a_mixed_recipe_reads_as_partial(db, tenant_a):
    """The third value earns its place: some lines resolve and some cannot, so
    neither `covered` nor `unknown` is honest."""
    good = _series(db, "good", levels=[(CUR_Y, CUR_Q, 100)])
    dry = _series(db, "dry")
    good_code = _code(db, good)
    bad_code = _code(db, dry, resolution="ambiguous")
    # An `ambiguous` code resolves to nothing, so the frozen line has no bound
    # series — exactly the `unbound_index_line` shape.
    cm = _cost_model(db, tenant_a, series_weights=[(good, 0.6)], unbound_index_line=True)
    try:
        cov = model_coverage(db, db.query(CostModel).filter(CostModel.id == cm.id).one())
        assert cov.coverage == COVERAGE_PARTIAL
    finally:
        _cleanup(db, cost_model_ids=[cm.id], code_ids=[good_code.id, bad_code.id],
                 series_ids=[good.id, dry.id])


def test_an_all_fixed_recipe_is_covered_not_unknown(db, tenant_a):
    """A deliberately non-indexed cost is not a blind spot — "nothing to move"
    and "cannot tell" are different answers."""
    cm = _cost_model(db, tenant_a, series_weights=[])
    fv = db.query(FormulaVersion).filter(FormulaVersion.cost_model_id == cm.id).one()
    db.add(FormulaComponent(formula_version_id=fv.id, label="Conversion",
                            commodity_id=None, weight=1.0, component_type="fixed"))
    db.commit()
    try:
        cov = model_coverage(db, db.query(CostModel).filter(CostModel.id == cm.id).one())
        assert cov.coverage == COVERAGE_COVERED
    finally:
        _cleanup(db, cost_model_ids=[cm.id])


# ── 4. The inspection payload ───────────────────────────────────────────────

def test_single_window_payload_carries_everything_the_ticket_asks_for(
        db, tenant_a, client_as):
    """AC4: driver, evidence values, threshold + unit, the line -> type code ->
    series path with proxy state, and open/close with the close basis."""
    series = _series(db, "proxied", levels=[
        (PREV_Y, PREV_Q, 100), (CUR_Y, CUR_Q, 125),
    ])
    # proxy_status lives on both the cost line and the type-code row and the two
    # disagree on a meaningful share of lines. The radar reads the type-code
    # side, so the line is deliberately left saying otherwise.
    code = _code(db, series, proxy_status="proxy")
    cm = _cost_model(db, tenant_a, series_weights=[(series, 1.0)])
    db.query(FormulaComponent).filter(
        FormulaComponent.formula_version_id.in_(
            db.query(FormulaVersion.id).filter(FormulaVersion.cost_model_id == cm.id)
        )
    ).update({"is_proxy": False}, synchronize_session=False)
    db.commit()
    try:
        run_radar(db, tenant_a["team_id"], today=TODAY)
        win = db.query(NegotiationWindow).filter(
            NegotiationWindow.team_id == tenant_a["team_id"],
            NegotiationWindow.driver == "index_move").one()

        r = client_as(tenant_a).get(f"/api/radar/windows/{win.id}")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["driver"] == "index_move"
        assert body["threshold_value"] == pytest.approx(10.0)
        # The unit travels with the value; a bare number could be percent or money.
        assert body["threshold_unit"] == "pct"
        assert body["opens_on"] == TODAY.isoformat()
        assert body["close_basis"] == "unknown"
        assert body["closes_on"] is None

        ev = body["evidence"]
        assert ev["move_pct"] == pytest.approx(25.0)
        assert code.code in ev["type_codes_resolving_here"]
        path = ev["resolution_path"]
        assert path and path[0]["cost_model_id"] == str(cm.id)
        # Read from the type-code side, not the cost line's own is_proxy.
        assert path[0]["via_proxy"] is True
        assert body["products"][0]["via_proxy"] is True
        # A forward-looking close needs forecast storage; the basis says so
        # rather than a date being invented.
        assert "forecast" in ev["close_basis_note"]
    finally:
        _cleanup(db, cost_model_ids=[cm.id], code_ids=[code.id], series_ids=[series.id])


def test_the_radar_suggests_a_negotiation_state_and_never_sets_it(db, tenant_a, client_as):
    """The decision the ticket asks to be written down rather than done
    silently. `cost_models.negotiation_state` is audit-logged human intent
    (Scrum 25); a background job flipping it would put a decision nobody made
    into the audit trail."""
    assert SUGGESTS_NOT_SETS is True
    series = _series(db, "suggest", levels=[
        (PREV_Y, PREV_Q, 100), (CUR_Y, CUR_Q, 150),
    ])
    cm = _cost_model(db, tenant_a, series_weights=[(series, 1.0)])
    try:
        run_radar(db, tenant_a["team_id"], today=TODAY)
        db.expire_all()
        assert db.query(CostModel).filter(CostModel.id == cm.id).one().negotiation_state == "none"

        win = db.query(NegotiationWindow).filter(
            NegotiationWindow.team_id == tenant_a["team_id"],
            NegotiationWindow.driver == "index_move").one()
        body = client_as(tenant_a).get(f"/api/radar/windows/{win.id}").json()
        assert body["suggested_negotiation_state"] == "in_negotiation"
        assert body["current_negotiation_states"][str(cm.id)] == "none"
    finally:
        _cleanup(db, cost_model_ids=[cm.id], series_ids=[series.id])


def test_a_gap_window_on_a_contracted_product_closes_on_the_notice_date(db, tenant_a):
    """The two feeds composing: the gap says there is something to negotiate,
    the contract says by when."""
    from app.models.price_data import ActualPrice

    series = _series(db, "gapish", levels=[
        (PREV_Y, PREV_Q, 100), (CUR_Y, CUR_Q, 100),
    ])
    sup = Supplier(team_id=tenant_a["team_id"], name="Padder Co")
    db.add(sup)
    db.commit()
    cm = _cost_model(db, tenant_a, series_weights=[(series, 1.0)], supplier=sup)
    db.add(ActualPrice(cost_model_id=cm.id, uploaded_by=tenant_a["user_id"],
                       year=CUR_Y, quarter=CUR_Q, price=150))     # +50% vs should-cost
    db.commit()
    c = _contract(db, tenant_a, cm, sup, notice_days=15,
                  term_end=TODAY + timedelta(days=45))
    try:
        run_radar(db, tenant_a["team_id"], today=TODAY)
        win = db.query(NegotiationWindow).filter(
            NegotiationWindow.team_id == tenant_a["team_id"],
            NegotiationWindow.driver == "gap").one()
        assert win.closes_on == c.notice_deadline
        assert win.close_basis == "clause_deadline"
        assert win.evidence["contract_id"] == str(c.id)
        # Both sides are money here, unlike on the index feed.
        assert win.evidence["threshold_unit_applicable"] is True
    finally:
        _cleanup(db, cost_model_ids=[cm.id], series_ids=[series.id])


# ── Market signals ──────────────────────────────────────────────────────────

def test_a_manually_entered_signal_opens_a_window_on_day_one(db, tenant_a, client_as):
    """The feed has no producer and no source in the drop, so the manual path is
    what makes the radar usable now — an analyst can put a force majeure on the
    radar without a deploy."""
    sup = Supplier(team_id=tenant_a["team_id"], name="Disrupted Co")
    db.add(sup)
    db.commit()
    cm = _cost_model(db, tenant_a, series_weights=[], supplier=sup)
    try:
        r = client_as(tenant_a).post(
            f"/api/radar/signals?team_id={tenant_a['team_id']}",
            json={
                "signal_type": "disruption",
                "headline": "Force majeure declared at the Antwerp plant",
                "supplier_id": sup.id,
                "as_of_date": TODAY.isoformat(),
                "expires_at": (TODAY + timedelta(days=30)).isoformat(),
            },
        )
        assert r.status_code == 201, r.text
        # Entered by a person, so the date is authored — `as_of_inferred` is for
        # the imported-editorial case whose vantage date has to be synthesised.
        assert r.json()["origin"] == "manual"
        assert r.json()["as_of_inferred"] is False

        run_radar(db, tenant_a["team_id"], today=TODAY)
        win = db.query(NegotiationWindow).filter(
            NegotiationWindow.team_id == tenant_a["team_id"],
            NegotiationWindow.driver == "market_signal").one()
        assert win.close_basis == "signal_expiry"
        assert win.closes_on == TODAY + timedelta(days=30)
        assert [p.cost_model_id for p in win.products] == [cm.id]
    finally:
        _cleanup(db, cost_model_ids=[cm.id])


def test_an_expired_signal_opens_nothing(db, tenant_a):
    db.add(MarketSignal(
        team_id=tenant_a["team_id"], origin="manual", signal_type="policy",
        headline="Old news", as_of_date=TODAY - timedelta(days=90),
        expires_at=TODAY - timedelta(days=30),
    ))
    db.commit()
    run_radar(db, tenant_a["team_id"], today=TODAY)
    assert db.query(NegotiationWindow).filter(
        NegotiationWindow.team_id == tenant_a["team_id"],
        NegotiationWindow.driver == "market_signal").count() == 0


def test_platform_signals_are_super_admin_only(db, tenant_a, client_as):
    r = client_as(tenant_a).post(
        f"/api/radar/signals?team_id={tenant_a['team_id']}",
        json={"signal_type": "policy", "headline": "Platform-wide",
              "as_of_date": TODAY.isoformat(), "platform": True},
    )
    assert r.status_code == 403


# ── 5. One threshold accessor ───────────────────────────────────────────────

def test_team_default_moves_a_non_overridden_subscription_and_not_an_overridden_one(
        db, tenant_a):
    """AC5, both halves. This only holds because nothing reads the column
    directly — the radar and the alert layer share `effective_threshold`."""
    inheriting = AlertSubscription(
        team_id=tenant_a["team_id"], user_id=tenant_a["user_id"],
        trigger_type="index_move", threshold_pct=None,
    )
    overridden = AlertSubscription(
        team_id=tenant_a["team_id"], user_id=tenant_a["user_id"],
        trigger_type="index_move", threshold_pct=2.5,
    )
    db.add_all([inheriting, overridden])
    db.commit()

    # The migration's team default.
    assert effective_threshold(db, tenant_a["team_id"], inheriting).value == pytest.approx(10.0)
    assert effective_threshold(db, tenant_a["team_id"], inheriting).source == "team_default"
    assert effective_threshold(db, tenant_a["team_id"], overridden).value == pytest.approx(2.5)
    assert effective_threshold(db, tenant_a["team_id"], overridden).source == "subscription"

    team = db.query(Team).filter(Team.id == tenant_a["team_id"]).one()
    team.default_threshold_pct = 20.0
    db.commit()

    assert effective_threshold(db, tenant_a["team_id"], inheriting).value == pytest.approx(20.0)
    assert effective_threshold(db, tenant_a["team_id"], overridden).value == pytest.approx(2.5)


def test_the_team_default_actually_moves_the_fire_boundary(db, tenant_a):
    """Not just the accessor's arithmetic — the radar's behaviour changes."""
    series = _series(db, "boundary", levels=[
        (PREV_Y, PREV_Q, 100), (CUR_Y, CUR_Q, 108),      # +8%
    ])
    cm = _cost_model(db, tenant_a, series_weights=[(series, 1.0)])
    try:
        run_radar(db, tenant_a["team_id"], today=TODAY)
        assert db.query(NegotiationWindow).filter(
            NegotiationWindow.team_id == tenant_a["team_id"],
            NegotiationWindow.driver == "index_move").count() == 0

        team = db.query(Team).filter(Team.id == tenant_a["team_id"]).one()
        team.default_threshold_pct = 5.0
        db.commit()

        run_radar(db, tenant_a["team_id"], today=TODAY)
        assert db.query(NegotiationWindow).filter(
            NegotiationWindow.team_id == tenant_a["team_id"],
            NegotiationWindow.driver == "index_move").count() == 1
    finally:
        _cleanup(db, cost_model_ids=[cm.id], series_ids=[series.id])


def test_threshold_endpoint_round_trips_and_reports_inheritance(db, tenant_a, client_as):
    c = client_as(tenant_a)
    assert c.get(f"/api/alerts/threshold?team_id={tenant_a['team_id']}"
                 ).json()["default_threshold_pct"] == pytest.approx(10.0)

    r = c.put(f"/api/alerts/threshold?team_id={tenant_a['team_id']}",
              json={"default_threshold_pct": 15, "default_threshold_unit": "pct"})
    assert r.status_code == 200, r.text

    made = c.post(f"/api/alerts/subscriptions?team_id={tenant_a['team_id']}",
                  json={"trigger_type": "gap"})
    assert made.status_code == 201, made.text
    body = made.json()
    # Raw override absent, effective value inherited — both reported, so a UI
    # never has to re-derive the precedence.
    assert body["threshold_pct"] is None
    assert body["effective_threshold_pct"] == pytest.approx(15.0)
    assert body["threshold_source"] == "team_default"

    bad = c.put(f"/api/alerts/threshold?team_id={tenant_a['team_id']}",
                json={"default_threshold_pct": 15, "default_threshold_unit": "bananas"})
    assert bad.status_code == 422


# ── 6 + 8. Delivery ─────────────────────────────────────────────────────────

def test_subscribing_to_a_window_trigger_delivers_and_writes_an_alert_event(
        db, tenant_a, client_as):
    """AC8, end to end through the existing alerts API — delivery still lands in
    `alert_events`, not a second ledger."""
    series = _series(db, "deliver", levels=[
        (PREV_Y, PREV_Q, 100), (CUR_Y, CUR_Q, 145),
    ])
    cm = _cost_model(db, tenant_a, series_weights=[(series, 1.0)])
    try:
        made = client_as(tenant_a).post(
            f"/api/alerts/subscriptions?team_id={tenant_a['team_id']}",
            json={"trigger_type": "negotiation_window"},
        )
        assert made.status_code == 201, made.text
        assert made.json()["scope_label"] == "All negotiation windows"

        run_radar(db, tenant_a["team_id"], today=TODAY)
        r = client_as(tenant_a).post(f"/api/alerts/evaluate?team_id={tenant_a['team_id']}")
        assert r.status_code == 200, r.text
        assert r.json()["alerts_created"] >= 1

        events = db.query(AlertEvent).filter(
            AlertEvent.team_id == tenant_a["team_id"],
            AlertEvent.trigger_type == "negotiation_window").all()
        assert events
        assert events[0].detail["driver"] == "index_move"
        assert events[0].detail["close_basis"] == "unknown"

        # Deduped: the same standing window does not re-fire.
        again = client_as(tenant_a).post(f"/api/alerts/evaluate?team_id={tenant_a['team_id']}")
        assert again.json()["alerts_created"] == 0
    finally:
        _cleanup(db, cost_model_ids=[cm.id], series_ids=[series.id])


def test_a_closed_window_does_not_deliver(db, tenant_a, client_as):
    """AC6. A window whose close date has passed is closed by the radar, and
    delivery reads only open windows."""
    sup = Supplier(team_id=tenant_a["team_id"], name="Elapsed Co")
    db.add(sup)
    db.commit()
    cm = _cost_model(db, tenant_a, series_weights=[], supplier=sup)
    c = _contract(db, tenant_a, cm, sup, notice_days=30,
                  term_end=TODAY + timedelta(days=45))
    try:
        client_as(tenant_a).post(
            f"/api/alerts/subscriptions?team_id={tenant_a['team_id']}",
            json={"trigger_type": "negotiation_window"},
        )
        run_radar(db, tenant_a["team_id"], today=TODAY)
        win = db.query(NegotiationWindow).filter(
            NegotiationWindow.team_id == tenant_a["team_id"]).one()
        assert win.state == "open"

        # A later run, past the deadline: the window closes itself.
        after = c.notice_deadline + timedelta(days=1)
        run_radar(db, tenant_a["team_id"], today=after)
        db.expire_all()
        win = db.query(NegotiationWindow).filter(NegotiationWindow.id == win.id).one()
        assert win.state == "closed"
        assert win.closed_at is not None

        r = client_as(tenant_a).post(f"/api/alerts/evaluate?team_id={tenant_a['team_id']}")
        assert r.json()["alerts_created"] == 0
        assert db.query(AlertEvent).filter(
            AlertEvent.team_id == tenant_a["team_id"],
            AlertEvent.trigger_type == "negotiation_window").count() == 0
    finally:
        _cleanup(db, cost_model_ids=[cm.id])


def test_a_dismissed_window_is_not_reopened_by_the_next_run(db, tenant_a, client_as):
    series = _series(db, "dismissed", levels=[
        (PREV_Y, PREV_Q, 100), (CUR_Y, CUR_Q, 160),
    ])
    cm = _cost_model(db, tenant_a, series_weights=[(series, 1.0)])
    try:
        run_radar(db, tenant_a["team_id"], today=TODAY)
        win = db.query(NegotiationWindow).filter(
            NegotiationWindow.team_id == tenant_a["team_id"]).one()

        r = client_as(tenant_a).post(f"/api/radar/windows/{win.id}/dismiss")
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "dismissed"

        run_radar(db, tenant_a["team_id"], today=TODAY)
        db.expire_all()
        assert db.query(NegotiationWindow).filter(
            NegotiationWindow.id == win.id).one().state == "dismissed"
    finally:
        _cleanup(db, cost_model_ids=[cm.id], series_ids=[series.id])


# ── 7. The wiring that was never done ───────────────────────────────────────

def test_every_beat_schedule_entry_resolves_to_a_registered_task():
    """AC7, plus the latent half of the same bug.

    `evaluate_all_alerts` shipped with Scrum 24 and was never registered in
    `beat_schedule`, so alerts only ever fired via the on-demand POST. And
    `autodiscover_tasks(["app.tasks"])` looks for an `app.tasks.tasks` submodule
    that does not exist, so nothing was autodiscovered either — a beat entry for
    an unimported task fails at dispatch. Both are asserted here.
    """
    import celeryconfig
    from app.tasks import celery_app

    for module in celeryconfig.imports:
        __import__(module)

    names = {e["task"] for e in celeryconfig.beat_schedule.values()}
    assert "app.tasks.alerts.evaluate_all_alerts" in names
    assert "app.tasks.radar.run_all_radars" in names
    for name in names:
        assert name in celery_app.tasks, f"{name} is scheduled but not registered"


# ── Tenancy + permissions ───────────────────────────────────────────────────

def test_contracts_are_isolated_between_teams(db, tenant_a, tenant_b, client_as):
    sup = Supplier(team_id=tenant_a["team_id"], name="Private Co")
    db.add(sup)
    db.commit()
    c = _contract(db, tenant_a, None, sup)

    assert client_as(tenant_a).get(
        f"/api/contracts?team_id={tenant_a['team_id']}").status_code == 200
    # A non-member is refused by the permission gate, and RLS hides the row too.
    assert client_as(tenant_b).get(
        f"/api/contracts?team_id={tenant_a['team_id']}").status_code == 403
    assert client_as(tenant_b).get(f"/api/contracts/{c.id}").status_code in (403, 404)


def test_contracts_and_windows_rls_isolate_at_the_db(db, tenant_a, tenant_b):
    """Belt and braces: the policy itself, not just the app-layer gate.

    Uses a separate RLS-scoped session (the `test_rls.py` pattern) rather than
    flipping the shared one — reusing the fixture session would expire the ORM
    objects it still holds and fail on the refresh, not on the policy.
    """
    sup_a = Supplier(team_id=tenant_a["team_id"], name="RLS-A")
    sup_b = Supplier(team_id=tenant_b["team_id"], name="RLS-B")
    db.add_all([sup_a, sup_b])
    db.commit()
    _contract(db, tenant_a, None, sup_a)
    _contract(db, tenant_b, None, sup_b)
    db.add_all([
        NegotiationWindow(
            team_id=tenant_a["team_id"], driver="clause_deadline",
            driver_key=f"k-{uuid.uuid4().hex}", scope_type="portfolio",
            headline="A window", opens_on=TODAY,
        ),
        NegotiationWindow(
            team_id=tenant_b["team_id"], driver="clause_deadline",
            driver_key=f"k-{uuid.uuid4().hex}", scope_type="portfolio",
            headline="B window", opens_on=TODAY,
        ),
    ])
    db.commit()

    s = SessionLocal()
    bypass_rls_var.set(False)
    current_user_id_var.set(str(tenant_a["user_id"]))
    try:
        teams = {c.team_id for c in s.query(Contract).all()}
        assert tenant_a["team_id"] in teams and tenant_b["team_id"] not in teams
        wins = {w.team_id for w in s.query(NegotiationWindow).all()}
        assert tenant_a["team_id"] in wins and tenant_b["team_id"] not in wins
    finally:
        s.close()
        bypass_rls_var.set(True)


def test_platform_market_signals_are_visible_to_every_team(db, tenant_a, tenant_b):
    """The other policy shape: `team_id IS NULL` is a platform-curated signal
    that every team can read, while a team's own entries stay private. Under a
    strict-tenant policy the platform feed would be invisible and the whole
    facet would look broken."""
    platform = MarketSignal(
        team_id=None, origin="manual", signal_type="policy",
        headline="Platform-wide anti-dumping ruling", as_of_date=TODAY,
    )
    private = MarketSignal(
        team_id=tenant_b["team_id"], origin="manual", signal_type="disruption",
        headline="B private", as_of_date=TODAY,
    )
    db.add_all([platform, private])
    db.commit()
    platform_id = platform.id

    s = SessionLocal()
    bypass_rls_var.set(False)
    current_user_id_var.set(str(tenant_a["user_id"]))
    try:
        heads = {m.headline for m in s.query(MarketSignal).all()}
        assert "Platform-wide anti-dumping ruling" in heads
        assert "B private" not in heads
    finally:
        s.close()
        bypass_rls_var.set(True)
        # A platform-scoped row has no team to CASCADE from, so it survives the
        # tenant teardown and would then be a live signal in every later team's
        # radar run. Exactly the leak `commodity_key` caused in the resolution
        # tests; cleaned up explicitly for the same reason.
        db.rollback()
        db.execute(text("DELETE FROM market_signals WHERE id = :i"),
                   {"i": str(platform_id)})
        db.commit()


def test_a_role_that_can_cost_but_not_see_contracts_is_refused(
        db, tenant_a, user_factory, client_as):
    """The reason the `contracts.*` category exists at all: before it, everyone
    who could run a costing could read contract prices and notice dates."""
    member = user_factory()
    db.add(TeamMembership(user_id=member["user_id"], team_id=tenant_a["team_id"],
                          role="member"))
    role = Role(team_id=tenant_a["team_id"], name=f"Coster-{uuid.uuid4().hex[:6]}")
    db.add(role)
    db.flush()
    costing = db.query(Permission).filter(Permission.key == "costing.view").one()
    db.add(RolePermission(role_id=role.id, permission_id=costing.id))
    db.add(TeamMemberRole(user_id=member["user_id"], team_id=tenant_a["team_id"],
                          role_id=role.id))
    db.commit()

    c = client_as(member)
    # Can read the radar (costing.view) …
    assert c.get(f"/api/radar/windows?team_id={tenant_a['team_id']}").status_code == 200
    # … and cannot read contracts.
    assert c.get(f"/api/contracts?team_id={tenant_a['team_id']}").status_code == 403
    assert c.post(f"/api/contracts?team_id={tenant_a['team_id']}",
                  json={"reference": "nope"}).status_code == 403
    # And the probe the UI gates the nav entry on says so rather than 403ing:
    # hiding the entry needs an answer, not an error, or every page load for a
    # user without access logs a failure.
    probe = c.get(f"/api/contracts/can-access?team_id={tenant_a['team_id']}")
    assert probe.status_code == 200
    assert probe.json() == {"can_view": False, "can_edit": False, "can_delete": False}


def test_can_access_probe_is_not_parsed_as_a_contract_id(db, tenant_a, client_as):
    """`/can-access` is registered ahead of `/{contract_id}`; registered after,
    the literal segment would be parsed as a UUID and 422 instead."""
    r = client_as(tenant_a).get(f"/api/contracts/can-access?team_id={tenant_a['team_id']}")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"can_view", "can_edit", "can_delete"}
    # The team owner holds all three.
    assert body == {"can_view": True, "can_edit": True, "can_delete": True}


def test_contracts_permissions_exist_and_are_plan_granted(db):
    """The plan ceiling is applied BEFORE roles, so a key missing from a team's
    plan is denied for every non-super-admin no matter their role. Asserting the
    Dream Plan grant is what stops this feature shipping silently disabled."""
    keys = {"contracts.view", "contracts.edit", "contracts.delete"}
    rows = db.query(Permission).filter(Permission.key.in_(keys)).all()
    assert {r.key for r in rows} == keys
    assert all(r.category == "contracts" for r in rows)

    from app.models.rbac import Plan, PlanPermission
    dream = db.query(Plan).filter(Plan.name == "Dream Plan").first()
    if dream:      # seeded by the RBAC migration; skip if a bare DB
        granted = {
            p.key
            for p in db.query(Permission)
            .join(PlanPermission, PlanPermission.permission_id == Permission.id)
            .filter(PlanPermission.plan_id == dream.id, Permission.key.in_(keys))
            .all()
        }
        assert granted == keys


def test_radar_run_is_owner_admin_only(db, tenant_a, user_factory, client_as):
    member = user_factory()
    db.add(TeamMembership(user_id=member["user_id"], team_id=tenant_a["team_id"],
                          role="member"))
    db.commit()
    assert client_as(member).post(
        f"/api/radar/run?team_id={tenant_a['team_id']}").status_code == 403
    assert client_as(tenant_a).post(
        f"/api/radar/run?team_id={tenant_a['team_id']}").status_code == 200


def test_radar_endpoints_require_authentication(client):
    assert client.get("/api/radar/windows?team_id=" + str(uuid.uuid4())).status_code == 401
    assert client.get("/api/contracts?team_id=" + str(uuid.uuid4())).status_code == 401


def test_window_scoped_subscription_rejects_a_scope_it_cannot_use(db, tenant_a, client_as):
    """A supplier/contract scope on a gap alert has no meaning — 422 rather than
    a subscription that silently never fires."""
    sup = Supplier(team_id=tenant_a["team_id"], name="Scope Co")
    db.add(sup)
    db.commit()
    r = client_as(tenant_a).post(
        f"/api/alerts/subscriptions?team_id={tenant_a['team_id']}",
        json={"trigger_type": "gap", "supplier_id": sup.id},
    )
    assert r.status_code == 422, r.text
