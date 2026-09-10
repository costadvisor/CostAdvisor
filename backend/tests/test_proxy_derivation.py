"""Proxy derivation engine + swap backlog (Wave 3, SCRUM-80 / FD-1).

The ticket's "Done" criteria, as tests:

* a value produced from a configured derivation comes back tagged as derived
  and names the base series and the operation behind it;
* asking for a value on a `no_series` type code returns an explicit
  unresolvable state naming the series it wanted — never a null and never a
  carried-forward number;
* current / stale / never-had-it are three distinguishable states, with the
  age attached;
* the swap backlog is queryable ranked by the cost weight actually sitting
  behind each type code.

**Assertions are on resolution state, never on a membership list of specific
codes** — per the ticket, and per the drop README's "build against the shape,
not the numbers".

Nearly everything here builds its own fixtures. That is not a convenience:
**no series in the loaded catalog has an executable derivation.** 128 rows
carry `proxy_logic`, and every one of them has `operation: None` /
`base_index: None` — only the analyst `note` was ever filled in. So the
executor genuinely had nothing to run, and a test reading the catalog for a
configured spec would silently pass by finding none.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.constants.index_metadata import PROXY_OPERATIONS
from app.database import bypass_rls_var
from app.models.formula_template import (
    FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexMonthlyValue, TypeCode
from app.services.drop import drop_available
from app.services.drop.index_loader import load_index_layer
from app.services.proxy_derivation import (
    ABSENT, CURRENT, STALE, UNRESOLVABLE_AMBIGUOUS, UNRESOLVABLE_NO_HISTORY,
    UNRESOLVABLE_NO_SERIES, blocked_series, derivation_spec, derive_value,
    resolve_with_provenance, swap_backlog, type_code_value,
)

needs_drop = pytest.mark.skipif(
    not drop_available(), reason="costadvisor-data drop not present in this checkout"
)


# ── Fixtures ─────────────────────────────────────────────────────────────────
#
# Deliberately no `commodity_key`: that is the drop's namespace, and setting it
# would make a fixture indistinguishable from loaded data (the loader would try
# to reconcile it, and the library-wide invariant tests would scan it).

def _series(db, *, proxy_logic=None) -> CommodityIndex:
    ci = CommodityIndex(
        name=f"fx-{uuid.uuid4().hex[:8]}", scrape_enabled=False, proxy_logic=proxy_logic,
    )
    db.add(ci)
    db.commit()
    return ci


def _months(db, series, points):
    """`points` is [(year, month, value, kind), ...]."""
    for year, month, value, kind in points:
        db.add(IndexMonthlyValue(
            commodity_id=series.id, year=year, month=month, value=value, kind=kind,
        ))
    db.commit()


def _code(db, series, *, resolution="resolved", **kw) -> TypeCode:
    tc = TypeCode(
        code=f"TC-{uuid.uuid4().hex[:8]}", resolution=resolution,
        resolves_to_id=series.id if series else None, **kw,
    )
    db.add(tc)
    db.commit()
    return tc


def _combo(db, tenant, region="Europe"):
    tpl = FormulaTemplate(
        team_id=tenant["team_id"], created_by=tenant["user_id"],
        name=f"tpl-{uuid.uuid4().hex[:6]}", code=f"C-{uuid.uuid4().hex[:6]}",
        expression=None,
    )
    db.add(tpl)
    db.commit()
    db.add(FormulaRegionCoverage(template_id=tpl.id, region=region, base_price=1000))
    db.commit()
    return tpl


def _cleanup(db, *, template_ids=(), code_ids=(), series_ids=()):
    # Roll back first: a test that failed mid-transaction leaves the session
    # unusable, and cleanup would then fail and leak rows into the next run.
    db.rollback()
    bypass_rls_var.set(True)
    for tid in template_ids:
        db.execute(text("DELETE FROM formula_templates WHERE id = :i"), {"i": str(tid)})
    for cid in code_ids:
        db.execute(text("DELETE FROM type_codes WHERE id = :i"), {"i": cid})
    for sid in series_ids:
        db.execute(text("DELETE FROM index_monthly_values WHERE commodity_id = :i"), {"i": sid})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": sid})
    db.commit()


# ── Value provenance: current / stale / never-had-it ─────────────────────────

def test_a_value_in_the_requested_quarter_reads_as_current(db):
    s = _series(db)
    try:
        _months(db, s, [(2026, 1, 100, "actual"), (2026, 2, 110, "actual"),
                        (2026, 3, 120, "actual")])
        p = resolve_with_provenance(db, s.id, 2026, 1)
        assert p.status == CURRENT
        assert p.value == pytest.approx(110.0)   # the quarter is the mean of its months
        assert p.quarters_stale == 0
        assert p.observed_year == 2026 and p.observed_quarter == 1
    finally:
        _cleanup(db, series_ids=[s.id])


def test_a_carried_forward_value_reads_as_stale_and_says_how_stale(db):
    """The distinction the costing path cannot make. `data_resolver`'s final
    carry-forward tier returns a number with no indication of when it was
    observed; this read attaches the age, and does not touch that path."""
    s = _series(db)
    try:
        _months(db, s, [(2025, 1, 100, "actual")])
        p = resolve_with_provenance(db, s.id, 2026, 1)
        assert p.status == STALE
        assert p.value == pytest.approx(100.0)
        assert p.quarters_stale == 4          # Q1-2025 -> Q1-2026
        assert p.observed_year == 2025 and p.observed_quarter == 1
        assert p.reason and "carried forward" in p.reason
    finally:
        _cleanup(db, series_ids=[s.id])


def test_never_had_it_is_a_third_state_not_a_zero(db):
    s = _series(db)
    try:
        p = resolve_with_provenance(db, s.id, 2026, 1)
        assert p.status == ABSENT
        assert p.value is None, "absent must never be reported as 0"
        assert not p.usable
        assert p.reason
    finally:
        _cleanup(db, series_ids=[s.id])


def test_a_period_before_all_history_is_absent_not_back_filled(db):
    """Carry-forward runs forward only. Reaching back would invent a value the
    series never had at that date."""
    s = _series(db)
    try:
        _months(db, s, [(2026, 1, 100, "actual")])
        p = resolve_with_provenance(db, s.id, 2024, 1)
        assert p.status == ABSENT
        assert p.value is None
    finally:
        _cleanup(db, series_ids=[s.id])


def test_forecast_points_are_not_served_as_actuals(db):
    """`kind` is in-band on the monthly table so an imported forecast and an
    observation share a series without becoming interchangeable."""
    s = _series(db)
    try:
        _months(db, s, [(2027, 1, 200, "forecast")])
        assert resolve_with_provenance(db, s.id, 2027, 1).status == ABSENT
        forecast = resolve_with_provenance(db, s.id, 2027, 1, kind="forecast")
        assert forecast.status == CURRENT
        assert forecast.kind == "forecast"
    finally:
        _cleanup(db, series_ids=[s.id])


# ── Derivation: the execution call site ──────────────────────────────────────

def test_a_derived_value_names_its_base_series_and_operation(db):
    """The core acceptance criterion. A number produced by executing a spec is
    tagged derived and fully attributed — a consumer can never mistake it for
    an observation."""
    base = _series(db)
    proxied = _series(db, proxy_logic={
        "base_index": None, "operation": "add", "spread": 40,
        "spread_unit": "abs", "recalibration": "annual",
        "note": "regional differential vs the parent feed",
    })
    try:
        _months(db, base, [(2026, 1, 100, "actual"), (2026, 2, 100, "actual"),
                           (2026, 3, 100, "actual")])
        # `base_index` holds a series NAME — that is how both the seed loader
        # and the admin editor write it.
        proxied.proxy_logic = {**proxied.proxy_logic, "base_index": base.name}
        db.commit()

        p = derive_value(db, proxied.id, 2026, 1)
        assert p.usable
        assert p.derived is True
        assert p.value == pytest.approx(140.0)

        d = p.derivation
        assert d["base_series"] == base.name
        assert d["base_series_id"] == base.id
        assert d["operation"] == "add"
        assert d["spread"] == 40
        assert d["base_value"] == pytest.approx(100.0)
        assert d["expression"]
        assert d["note"]
    finally:
        _cleanup(db, series_ids=[base.id, proxied.id])


def test_a_derivation_is_only_as_fresh_as_the_base_it_stands_on(db):
    """A stale base must not be laundered into a fresh-looking result."""
    base = _series(db)
    proxied = _series(db, proxy_logic={
        "base_index": None, "operation": "multiply", "spread": 1.2,
    })
    try:
        _months(db, base, [(2024, 1, 50, "actual")])
        proxied.proxy_logic = {**proxied.proxy_logic, "base_index": base.name}
        db.commit()

        p = derive_value(db, proxied.id, 2026, 1)
        assert p.usable
        assert p.value == pytest.approx(60.0)
        assert p.status == STALE
        assert p.quarters_stale == 8
        assert p.derivation["base_status"] == STALE
    finally:
        _cleanup(db, series_ids=[base.id, proxied.id])


def test_a_percentage_spread_is_applied_as_a_percentage(db):
    base = _series(db)
    proxied = _series(db, proxy_logic={
        "base_index": None, "operation": "spread", "spread": 10, "spread_unit": "pct",
    })
    try:
        _months(db, base, [(2026, 1, 200, "actual")])
        proxied.proxy_logic = {**proxied.proxy_logic, "base_index": base.name}
        db.commit()
        assert derive_value(db, proxied.id, 2026, 1).value == pytest.approx(220.0)
    finally:
        _cleanup(db, series_ids=[base.id, proxied.id])


def test_a_note_only_spec_is_a_configuration_state_not_a_failure(db):
    """Every spec in the loaded catalog is this shape: the analyst prose was
    written and the executable params never were. Reported as such, so the
    number of runnable derivations is visible rather than looking like a bug."""
    s = _series(db, proxy_logic={
        "base_index": None, "operation": None, "spread": None,
        "note": "Used as a proxy for gluconic acid pricing",
    })
    try:
        spec, why_not = derivation_spec(s)
        assert spec is None
        assert why_not and "analyst note" in why_not
        assert "gluconic" in why_not, "the note itself is surfaced, not swallowed"

        p = derive_value(db, s.id, 2026, 1)
        assert p.value is None and p.status == ABSENT
    finally:
        _cleanup(db, series_ids=[s.id])


def test_an_operation_the_spec_shape_cannot_express_is_refused_not_guessed(db):
    """`proxy_logic` carries one scalar `spread`; a fitted relation needs
    coefficients and there is nowhere to put them. Refusing is the honest
    outcome — guessing would produce a confidently wrong number."""
    unsupported = sorted(set(PROXY_OPERATIONS) - {
        "passthrough", "add", "multiply", "ratio", "spread",
    })
    assert unsupported, "regression: the vocabulary must still exceed what is executable"

    s = _series(db, proxy_logic={
        "base_index": "whatever", "operation": unsupported[0], "spread": 1,
    })
    try:
        spec, why_not = derivation_spec(s)
        assert spec is None
        assert why_not and unsupported[0] in why_not
    finally:
        _cleanup(db, series_ids=[s.id])


def test_a_base_index_we_do_not_hold_is_named_not_silently_skipped(db):
    s = _series(db, proxy_logic={
        "base_index": "a-series-nobody-has", "operation": "add", "spread": 5,
    })
    try:
        p = derive_value(db, s.id, 2026, 1)
        assert p.value is None
        assert "a-series-nobody-has" in p.reason
    finally:
        _cleanup(db, series_ids=[s.id])


# ── The type-code entry point ────────────────────────────────────────────────

def test_no_series_returns_an_unresolvable_state_naming_the_wanted_series(db):
    """The ticket states this one exactly: not a null, and not a
    carried-forward value. `no_series` means the target has no NUMBERS, so the
    series it wanted is still named — which is the sourcing instruction."""
    dry = _series(db)
    tc = _code(db, dry, resolution="no_series", swap_priority="A",
               ideal_index="the real regional assessment")
    try:
        r = type_code_value(db, tc.code, 2026, 1)
        assert r.resolvable is False
        assert r.value is None
        assert r.unresolvable_reason == UNRESOLVABLE_NO_SERIES
        assert r.wanted_series == dry.name
        assert r.ideal_index and r.swap_priority == "A"
    finally:
        _cleanup(db, code_ids=[tc.id], series_ids=[dry.id])


def test_ambiguous_is_a_different_unresolvable_state(db):
    """Never folded into `no_series`: one needs a feed bought, the other needs
    somebody to decide what the code means."""
    tc = _code(db, None, resolution="ambiguous")
    try:
        r = type_code_value(db, tc.code, 2026, 1)
        assert r.resolvable is False
        assert r.unresolvable_reason == UNRESOLVABLE_AMBIGUOUS
        assert r.wanted_series is None
    finally:
        _cleanup(db, code_ids=[tc.id])


def test_resolved_but_dry_is_a_third_unresolvable_state(db):
    """`resolved` with no numbers yet is a scrape problem, not a purchase one."""
    s = _series(db)
    tc = _code(db, s)
    try:
        r = type_code_value(db, tc.code, 2026, 1)
        assert r.resolvable is False
        assert r.unresolvable_reason == UNRESOLVABLE_NO_HISTORY
        assert r.wanted_series == s.name
        assert r.provenance is not None and r.provenance.status == ABSENT
    finally:
        _cleanup(db, code_ids=[tc.id], series_ids=[s.id])


def test_a_resolved_code_with_history_returns_an_observed_value(db):
    s = _series(db)
    tc = _code(db, s)
    try:
        _months(db, s, [(2026, 1, 75, "actual")])
        r = type_code_value(db, tc.code, 2026, 1)
        assert r.resolvable is True
        assert r.value == pytest.approx(75.0)
        assert r.provenance.derived is False, "an observation must not read as derived"
        assert r.provenance.status == CURRENT
    finally:
        _cleanup(db, code_ids=[tc.id], series_ids=[s.id])


def test_derivation_is_the_fallback_when_nothing_was_observed(db):
    """Observation first, derivation only when there is nothing to observe —
    and the result still says it was derived."""
    base = _series(db)
    proxied = _series(db, proxy_logic={"base_index": None, "operation": "passthrough"})
    tc = _code(db, proxied)
    try:
        _months(db, base, [(2026, 1, 33, "actual")])
        proxied.proxy_logic = {"base_index": base.name, "operation": "passthrough"}
        db.commit()

        r = type_code_value(db, tc.code, 2026, 1)
        assert r.resolvable is True
        assert r.value == pytest.approx(33.0)
        assert r.provenance.derived is True
        assert r.provenance.derivation["base_series"] == base.name
    finally:
        _cleanup(db, code_ids=[tc.id], series_ids=[base.id, proxied.id])


def test_unknown_type_code_is_none(db):
    assert type_code_value(db, "NOT-A-CODE-AT-ALL", 2026, 1) is None


# ── The swap backlog ─────────────────────────────────────────────────────────

def test_the_backlog_is_ranked_by_live_catalog_weight(db, tenant_a):
    """The acceptance criterion: ranked by the cost weight actually sitting
    behind each code, so a high-weight A sorts above a low-weight one."""
    s = _series(db)
    heavy = _code(db, s, swap_priority="A")
    light = _code(db, s, swap_priority="A")
    tpl = _combo(db, tenant_a)
    try:
        db.add(FormulaTemplateComponent(
            template_id=tpl.id, region="Europe", name="heavy",
            component_type="index", commodity_id=s.id, type_code_id=heavy.id,
            weight_pct=70,
        ))
        db.add(FormulaTemplateComponent(
            template_id=tpl.id, region="Europe", name="light",
            component_type="index", commodity_id=s.id, type_code_id=light.id,
            weight_pct=5,
        ))
        db.commit()

        backlog = swap_backlog(db, limit=10_000)
        by_code = {e.code: e for e in backlog.entries}
        assert by_code[heavy.code].catalog_weight == pytest.approx(70.0)
        assert by_code[light.code].catalog_weight == pytest.approx(5.0)
        assert by_code[heavy.code].line_count == 1

        codes = [e.code for e in backlog.entries]
        assert codes.index(heavy.code) < codes.index(light.code)
        weights = [e.catalog_weight for e in backlog.entries]
        assert weights == sorted(weights, reverse=True)
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[heavy.id, light.id],
                 series_ids=[s.id])


def test_margin_and_fixed_lines_stay_out_of_the_weight_denominator(db, tenant_a):
    """Margin is a line inside the 100% total in this catalog. Letting it into
    a weight-share aggregation would inflate every denominator and make the
    ranking meaningless."""
    s = _series(db)
    tc = _code(db, s)
    tpl = _combo(db, tenant_a)
    try:
        before = swap_backlog(db, limit=1).total_catalog_weight
        db.add(FormulaTemplateComponent(
            template_id=tpl.id, region="Europe", name="Supplier margin",
            component_type="fixed", type_code_id=tc.id, weight_pct=9,
        ))
        db.commit()
        after = swap_backlog(db, limit=1).total_catalog_weight
        assert after == pytest.approx(before), "a fixed line must not move the denominator"

        entry = next(e for e in swap_backlog(db, limit=10_000).entries if e.code == tc.code)
        assert entry.catalog_weight == 0.0
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[tc.id], series_ids=[s.id])


def test_the_backlog_can_be_filtered_to_one_rank(db):
    """A/B/C is a sourcing rank, not an accuracy score — so the ranks are
    filterable separately rather than averaged into one score."""
    s = _series(db)
    a = _code(db, s, swap_priority="A")
    c = _code(db, s, swap_priority="C")
    try:
        only_a = {e.code for e in swap_backlog(db, priority="A", limit=10_000).entries}
        assert a.code in only_a and c.code not in only_a
    finally:
        _cleanup(db, code_ids=[a.id, c.id], series_ids=[s.id])


def test_an_unpriceable_code_carrying_weight_appears_with_it(db, tenant_a):
    """The whole point of the ranking: a code we cannot price today, with the
    weight that makes buying its feed worth it."""
    dry = _series(db)
    tc = _code(db, dry, resolution="no_series", swap_priority="A",
               ideal_index="the assessment we would rather have")
    tpl = _combo(db, tenant_a)
    try:
        db.add(FormulaTemplateComponent(
            template_id=tpl.id, region="Europe", name="Unbought feed",
            component_type="index", commodity_id=dry.id, type_code_id=tc.id,
            weight_pct=40,
        ))
        db.commit()

        blocked = {b["code"]: b for b in blocked_series(db)}
        assert tc.code in blocked
        entry = blocked[tc.code]
        assert entry["resolution"] == "no_series"
        assert entry["catalog_weight"] == pytest.approx(40.0)
        # Names the feed to buy — that IS the sourcing instruction.
        assert entry["wanted_series"] == dry.name
        assert entry["ideal_index"]

        priceable = next(
            e for e in swap_backlog(db, limit=10_000).entries if e.code == tc.code
        ).priceable
        assert priceable is False
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[tc.id], series_ids=[dry.id])


# ── API surface ──────────────────────────────────────────────────────────────

def test_value_endpoint_serves_the_unresolvable_state(db, tenant_a, client_as):
    dry = _series(db)
    tc = _code(db, dry, resolution="no_series", swap_priority="B")
    try:
        r = client_as(tenant_a).get(
            f"/api/resolution/type-codes/{tc.code}/value?year=2026&quarter=1"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolvable"] is False
        assert body["value"] is None
        assert body["unresolvable_reason"] == UNRESOLVABLE_NO_SERIES
        assert body["wanted_series"] == dry.name
    finally:
        _cleanup(db, code_ids=[tc.id], series_ids=[dry.id])


def test_value_endpoint_serves_derivation_provenance(db, tenant_a, client_as):
    base = _series(db)
    proxied = _series(db, proxy_logic={
        "base_index": None, "operation": "add", "spread": 7,
    })
    tc = _code(db, proxied)
    try:
        _months(db, base, [(2026, 1, 10, "actual")])
        proxied.proxy_logic = {"base_index": base.name, "operation": "add", "spread": 7}
        db.commit()

        body = client_as(tenant_a).get(
            f"/api/resolution/type-codes/{tc.code}/value?year=2026&quarter=1"
        ).json()
        assert body["value"] == pytest.approx(17.0)
        assert body["provenance"]["derived"] is True
        assert body["provenance"]["derivation"]["operation"] == "add"
        assert body["provenance"]["derivation"]["base_series"] == base.name
    finally:
        _cleanup(db, code_ids=[tc.id], series_ids=[base.id, proxied.id])


def test_value_endpoint_rejects_a_nonsense_quarter(db, tenant_a, client_as):
    r = client_as(tenant_a).get(
        "/api/resolution/type-codes/anything/value?year=2026&quarter=9"
    )
    assert r.status_code == 422


def test_value_endpoint_404s_an_unknown_code(db, tenant_a, client_as):
    r = client_as(tenant_a).get(
        "/api/resolution/type-codes/NOT-A-CODE/value?year=2026&quarter=1"
    )
    assert r.status_code == 404


def test_backlog_endpoint_requires_authentication(client):
    assert client.get("/api/resolution/swap-backlog").status_code == 401


@needs_drop
def test_backlog_endpoint_returns_the_loaded_library(db, tenant_a, client_as):
    """Against the real drop: shape and ordering, not counts."""
    bypass_rls_var.set(True)
    report = load_index_layer(db)
    db.commit() if report.changed else db.rollback()

    body = client_as(tenant_a).get("/api/resolution/swap-backlog?limit=25").json()
    assert len(body["entries"]) <= 25
    weights = [e["catalog_weight"] for e in body["entries"]]
    assert weights == sorted(weights, reverse=True)
    for e in body["entries"]:
        assert e["resolution"] in {"resolved", "no_series", "ambiguous"}
        assert e["priceable"] == (e["resolution"] == "resolved")


@needs_drop
def test_no_loaded_series_has_an_executable_derivation_yet(db):
    """A finding, pinned so it is noticed when it changes.

    Every `proxy_logic` in the catalog carries only the analyst note — the
    `operation`/`base_index` params were never filled in. The executor is
    therefore live but idle, which is a configuration gap, not a code gap. When
    somebody configures the first real spec through the admin editor this test
    fails, and that failure is the signal to retire it.
    """
    bypass_rls_var.set(True)
    report = load_index_layer(db)
    db.commit() if report.changed else db.rollback()

    configured = [
        s.name for s in db.query(CommodityIndex).filter(
            CommodityIndex.proxy_logic.isnot(None),
        ).all()
        if derivation_spec(s)[0] is not None
    ]
    assert configured == [], (
        f"{len(configured)} series now carry executable proxy specs — "
        "the derivation path is no longer idle; retire this test"
    )
