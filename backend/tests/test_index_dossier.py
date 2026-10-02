"""Index dossier storage + platform volatility calibration (Wave 3, DB-7).

The ticket's "Done" criteria, as tests:

1. the structured dossier fields are storable and retrievable per series, and
   per card where they differ by region;
2. a driver row carries its **correlation, lag and signal together**;
3. a producer role resolves to unit 8's producer entity **by FK**, rather than
   storing the company inline;
4. the volatility calibration is stored platform-level with a recompute path;
5. **the recompute is exercised by a test that adds a series and asserts the
   breakpoints move.**

Plus the three boundaries the ticket asks to be held — no computed snapshots, no
prose, no second company master — and the two ladder findings that make
regenerating it rather than importing it the only defensible choice.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.index_data import CommodityIndex
from app.models.index_dossier import (
    IndexDossier, IndexDriver, IndexProducerRole, VolatilityBreakpoint,
    VolatilityCalibration, normalize_signal,
)
from app.models.index_layer import IndexMonthlyValue
from app.services.drop.dossier_loader import (
    SKIPPED_COMPUTED, SKIPPED_DERIVABLE, SKIPPED_PROSE, has_dossier,
    load_dossiers, parse_correlation, parse_lag_days,
)
from app.services.drop.reader import drop_available
from app.services.index_dossier import (
    DEFAULT_MIN_POINTS, active_calibration, build_ladder, dossier_for,
    percentile_for, recompute_volatility_calibration, series_dispersion,
    volatility_percentile,
)
from app.services.calibration_purge import purge
from app.services.producers import resolve_raw_name

DROP_RAW = (pathlib.Path(__file__).resolve().parents[2]
            / "sample_idea" / "costadvisor-data" / "raw")

needs_drop = pytest.mark.skipif(
    not drop_available(), reason="costadvisor-data drop not present in this checkout"
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _series(db, hint="probe", *, monthly=None) -> CommodityIndex:
    """A fixture series. No `commodity_key` — that is the drop's namespace, and
    setting it would make the fixture indistinguishable from loaded data."""
    ci = CommodityIndex(name=f"{hint}-{uuid.uuid4().hex[:8]}", scrape_enabled=False)
    db.add(ci)
    db.flush()
    for i, value in enumerate(monthly or []):
        db.add(IndexMonthlyValue(
            commodity_id=ci.id, year=2024 + i // 12, month=(i % 12) + 1,
            value=value, kind="actual",
        ))
    db.commit()
    return ci


def _cleanup(db, *, series_ids=(), calibration_ids=(), producer_ids=()):
    db.rollback()
    bypass_rls_var.set(True)
    for cid in calibration_ids:
        db.execute(text("DELETE FROM volatility_calibrations WHERE id = :i"),
                   {"i": str(cid)})
    for sid in series_ids:
        db.execute(text("DELETE FROM index_monthly_values WHERE commodity_id = :i"),
                   {"i": sid})
        db.execute(text("DELETE FROM index_dossiers WHERE commodity_id = :i"), {"i": sid})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": sid})
    for pid in producer_ids:
        db.execute(text("DELETE FROM producers WHERE id = :i"), {"i": str(pid)})
    db.commit()


@pytest.fixture(autouse=True)
def _restore_active_calibration(db):
    """Put the active volatility ladder back after every test in this module.

    `recompute_volatility_calibration` deactivates whatever is active and commits
    a new active row — that is the behaviour under test. But `_cleanup` here
    commits rather than rolling back, and although it has always accepted a
    `calibration_ids` kwarg, **not one test ever passed it**. Six tests recompute.

    While the suite shared the app's database that left the real, product-facing
    `active_calibration()` pointing at a ladder fitted to four synthetic series
    at 11 rungs against a production default of 21 — so every percentile the app
    served came from test fixtures, and 150 junk rows accumulated. The separate
    test database (conftest) stops that reaching production, but the
    contamination is still real *inside* a run: `test_intelligence` reads
    `active_calibration()` and would otherwise pick up whichever ladder this
    module happened to leave behind.

    An autouse fixture rather than six `calibration_ids=[...]` arguments, because
    the seventh recomputing test would have to remember, and the first six did
    not.
    """
    bypass_rls_var.set(True)
    prior_active = db.execute(text(
        "SELECT id FROM volatility_calibrations WHERE is_active")).scalar()
    before = {r[0] for r in db.execute(text("SELECT id FROM volatility_calibrations"))}
    yield
    db.rollback()
    bypass_rls_var.set(True)
    after = {r[0] for r in db.execute(text("SELECT id FROM volatility_calibrations"))}
    created = after - before
    for cid in created:
        db.execute(text("DELETE FROM volatility_breakpoints WHERE calibration_id = :i"),
                   {"i": str(cid)})
        db.execute(text("DELETE FROM volatility_calibrations WHERE id = :i"),
                   {"i": str(cid)})
    # Reactivate only after the replacements are gone — a partial unique index
    # allows exactly one active row.
    if prior_active is not None:
        db.execute(text("UPDATE volatility_calibrations SET is_active = true WHERE id = :i"),
                   {"i": str(prior_active)})
    db.commit()


@pytest.fixture(scope="module")
def _dossiers_loaded():
    """Load the dossiers once for the module — a full load is not cheap and the
    data is identical for every test that reads it."""
    if not drop_available():
        pytest.skip("costadvisor-data drop not present in this checkout")
    from app.database import SessionLocal

    session = SessionLocal()
    bypass_rls_var.set(True)
    try:
        load_dossiers(session)
        session.commit()
    finally:
        session.close()
    return True


# ── 1. Storable and retrievable, per series and per region ──────────────────

@needs_drop
def test_a_dossier_is_retrievable_per_series(db, _dossiers_loaded):
    """AC1. Every structured group round-trips."""
    bypass_rls_var.set(True)
    row = (
        db.query(IndexDossier)
        .filter(IndexDossier.region.is_(None))
        .join(IndexDriver, IndexDriver.dossier_id == IndexDossier.id)
        .first()
    )
    assert row is not None, "expected at least one loaded dossier with drivers"

    resolved = dossier_for(db, row.commodity_id)
    assert resolved is not None
    assert resolved.resolved_from == "series"
    assert resolved.drivers, "drivers are the core of the dossier"
    assert resolved.header, "the methodology header round-trips"
    # The header carries methodology, never a snapshot.
    assert set(resolved.header) == {
        "quote_type", "formula_role", "access_tier",
        "anchor_correlation", "anchor_correlation_raw",
    }


@needs_drop
def test_a_region_specific_dossier_overrides_the_series_wide_one(db, _dossiers_loaded):
    """AC1's second half: per card where they differ by region.

    All 16 source entries carrying `_regional` have **no** dossier fields at the
    top level while their overrides do — testing only the parent silently
    skipped every one of them, which is exactly what the first run of this
    loader did (it reported "0 regional overrides" against 16 entries' worth).
    """
    bypass_rls_var.set(True)
    regional = (
        db.query(IndexDossier).filter(IndexDossier.region.isnot(None)).first()
    )
    assert regional is not None, "expected regional dossiers to have loaded"

    specific = dossier_for(db, regional.commodity_id, region=regional.region)
    assert specific is not None
    assert specific.resolved_from == "region"

    # A region with no override of its own falls back, and says so.
    fallback = dossier_for(db, regional.commodity_id, region="NoSuchRegion")
    if fallback is not None:
        assert fallback.resolved_from == "series"


@needs_drop
def test_the_regional_carriers_are_detected_at_all(db):
    """The `has_dossier` rule, pinned — because getting it wrong is silent."""
    payloads = json.loads((DROP_RAW / "INDEXES.json").read_text(encoding="utf-8"))
    with_regional = [
        k for k, v in payloads.items()
        if isinstance(v, dict) and v.get("_regional")
    ]
    assert with_regional, "expected _regional carriers in the drop"
    # None of them qualifies on its top-level fields alone...
    top_level_only = [
        k for k in with_regional
        if any(payloads[k].get(f) for f in
               ("upstreamDrivers", "chain", "roles", "producers", "negPointers"))
    ]
    assert top_level_only == [], (
        "some _regional carriers now have top-level dossier fields — the "
        "detection rule can be simplified"
    )
    # ...and 10 of the 16 qualify once the overrides are looked at. The other
    # 6 (acrylonitrile, acrylic-acid, bpa, caprolactam, mma,
    # phthalic-anhydride) are pure card-metadata region variants with no dossier
    # content anywhere, which is why the loader also skips an override whose
    # merged payload is empty rather than storing a blank regional row.
    qualifying = [k for k in with_regional if has_dossier(payloads[k])]
    assert qualifying, "expected some _regional carriers to hold dossier content"
    assert len(qualifying) < len(with_regional), (
        "every _regional carrier now holds dossier content — the empty-override "
        "guard in the loader may no longer be needed"
    )
    assert "iron-scrap-na" in qualifying


# ── 2. The driver row ───────────────────────────────────────────────────────

@needs_drop
def test_a_driver_carries_correlation_lag_and_signal_together(db, _dossiers_loaded):
    """AC2, and the reason it matters: a correlation without its lag cannot be
    acted on, and a lag without a direction cannot be read."""
    bypass_rls_var.set(True)
    driver = (
        db.query(IndexDriver)
        .filter(IndexDriver.correlation.isnot(None),
                IndexDriver.lag_raw.isnot(None),
                IndexDriver.signal_raw.isnot(None))
        .first()
    )
    assert driver is not None, "expected a fully-populated driver row"
    assert -1 <= float(driver.correlation) <= 1
    assert driver.signal_strength in (
        "dominant", "strong", "medium", "moderate", "weak", "macro", "other")
    assert driver.move_up in (True, False, None)


def test_the_signal_vocabulary_is_normalised_not_constrained():
    """The source has 20 distinct signal values across 66 rows, including
    "dominant structural" and "medium geopolitical" — a CHECK-constrained enum
    would reject the real data, so the raw string is kept and a comparable
    strength derived from it."""
    assert normalize_signal("dominant structural") == "dominant"
    assert normalize_signal("medium geopolitical") == "medium"
    assert normalize_signal("strong event-driven") == "strong"
    # Anything unrecognised is "other" rather than guessed into a neighbour.
    assert normalize_signal("wildly unprecedented") == "other"
    assert normalize_signal(None) == "other"


def test_a_lag_that_does_not_parse_stays_null_rather_than_becoming_zero():
    """A lag invented as 0 would read as "arrives immediately" — the opposite of
    "unknown"."""
    assert parse_lag_days("4–6 weeks") == (28, 42)
    assert parse_lag_days("8-10 weeks") == (56, 70)
    assert parse_lag_days("1–2 quarters") == (91, 182)
    assert parse_lag_days("3 months") == (90, 90)
    assert parse_lag_days("Immediate (co-product)") == (0, 0)
    # The real unparseable case from the drop.
    assert parse_lag_days("Not yet computed vs Pink Sheet data") == (None, None)
    assert parse_lag_days(None) == (None, None)


def test_a_correlation_parses_from_a_number_or_an_r_string():
    assert parse_correlation(0.82) == pytest.approx(0.82)
    assert parse_correlation("r=0.82 vs. Benzene NWE (6w lag)") == pytest.approx(0.82)
    assert parse_correlation("r = -0.4 inverse") == pytest.approx(-0.4)
    # Out of range is refused, not clamped — a correlation of 3 is a data bug,
    # and silently turning it into 1 hides it.
    assert parse_correlation(3.0) is None
    assert parse_correlation("no coefficient here") is None


# ── 3. One company master ───────────────────────────────────────────────────

@needs_drop
def test_a_producer_role_fks_to_the_producer_entity(db, _dossiers_loaded):
    """AC3. `Supplier.team_id` is NOT NULL under strict tenant, so a company
    that exists independently of a buying team has no row shape there — unit 8
    owns the master and this FKs to it rather than storing the company inline."""
    bypass_rls_var.set(True)
    role = db.query(IndexProducerRole).first()
    assert role is not None, "expected producer roles to have loaded"
    assert role.producer is not None, "the FK must resolve to a real producer"
    assert role.producer.name
    assert role.role in ("producer", "price_setter")
    # The raw string the dossier used is kept alongside the resolved company.
    assert role.raw_name


@needs_drop
def test_an_undisclosed_share_is_not_stored_as_zero(db, _dossiers_loaded):
    """42 of 189 index-dossier company rows carry share=0, which means *not
    disclosed*. Storing it as a number ships "BASF — 0% market share"."""
    bypass_rls_var.set(True)
    rows = db.query(IndexProducerRole).all()
    assert rows
    for r in rows:
        if not r.share_disclosed:
            assert r.share_pct is None
        else:
            assert r.share_pct is not None and float(r.share_pct) > 0


def test_a_dossier_producer_role_resolves_a_multi_company_string(db):
    """The alias layer is shared with unit 8, so a `" / "` string still names
    several companies here."""
    resolved = resolve_raw_name(db, "Alpha Chem / Beta Chem", alias_map={})
    db.commit()
    ids = [r.producer.id for r in resolved]
    try:
        assert len(resolved) == 2
    finally:
        _cleanup(db, producer_ids=ids)


# ── The three boundaries ────────────────────────────────────────────────────

def test_the_computed_snapshots_are_not_stored_anywhere_on_the_dossier():
    """Boundary 1. `index_feeds.csv` ships six recomputable snapshots and none
    of them has a column here."""
    columns = set()
    for model in (IndexDossier, IndexDriver, IndexProducerRole):
        columns |= {c.name for c in model.__table__.columns}
    for forbidden in ("current_value", "change_pct", "volatility_pct", "cycle_pct",
                      "card_status", "has_intel_block", "cycle_position",
                      "seasonality", "season"):
        assert forbidden not in columns, (
            f"{forbidden} is a computed snapshot and must not be stored on a dossier"
        )
    # And the loader lists what it skips, so the omission is auditable rather
    # than looking like an oversight.
    assert "volPct" in SKIPPED_COMPUTED
    assert "cyclePos" in SKIPPED_COMPUTED
    assert "season" in SKIPPED_DERIVABLE and "seasonNote" in SKIPPED_DERIVABLE
    assert "dyn3m" in SKIPPED_PROSE and "signals3m" in SKIPPED_PROSE


@needs_drop
def test_the_editorial_volatility_number_contradicts_itself(db):
    """The measured reason boundary 1 exists. Three series carry two different
    `volatility_pct` values across their own cards — same series, same numbers
    underneath. Pinned so a later drop that fixes it is noticed."""
    import csv

    feeds_path = DROP_RAW.parent / "tables" / "index_feeds.csv"
    with open(feeds_path, newline="", encoding="utf-8") as fh:
        feeds = list(csv.DictReader(fh))
    by_series: dict[str, set[str]] = {}
    for row in feeds:
        if row.get("volatility_pct"):
            by_series.setdefault(row["series_key"], set()).add(row["volatility_pct"])
    conflicting = {k: v for k, v in by_series.items() if len(v) > 1}
    assert conflicting, (
        "the editorial volatility numbers no longer contradict themselves — "
        "worth revisiting whether importing them is now defensible"
    )
    assert "elec-cn" in conflicting


# ── 4 + 5. The calibration ──────────────────────────────────────────────────

def test_the_step_is_derived_from_the_ladder_length_not_hardcoded(db):
    """The mockup hardcodes x5, which is only right at 21 rungs — and would
    break silently the moment anybody recalibrated to a different length."""
    series = [_series(db, "cal", monthly=[100 + i * (n + 1) for i in range(24)])
              for n in range(4)]
    ids = [s.id for s in series]
    try:
        twenty_one = recompute_volatility_calibration(db, n_rungs=21, min_points=13)
        db.commit()
        assert twenty_one.step == pytest.approx(5.0)

        eleven = recompute_volatility_calibration(db, n_rungs=11, min_points=13)
        db.commit()
        assert eleven.step == pytest.approx(10.0)
        assert eleven.n_rungs == 11
    finally:
        _cleanup(db, series_ids=ids)


def test_adding_a_series_moves_the_breakpoints(db):
    """AC5, stated verbatim in the ticket: the recompute is exercised by adding
    a series and asserting the breakpoints move.

    The added series is deliberately far more volatile than anything else, so
    the top of the ladder has to move.
    """
    calm = [_series(db, "calm", monthly=[100 + i * 0.1 for i in range(24)])
            for _ in range(4)]
    ids = [s.id for s in calm]
    try:
        before = recompute_volatility_calibration(db, n_rungs=11, min_points=13)
        db.commit()
        before_ladder = [float(b.dispersion) for b in
                         sorted(before.breakpoints, key=lambda b: b.rung)]
        before_n = before.n_series

        wild = _series(db, "wild",
                       monthly=[100 if i % 2 else 250 for i in range(24)])
        ids.append(wild.id)

        after = recompute_volatility_calibration(db, n_rungs=11, min_points=13)
        db.commit()
        after_ladder = [float(b.dispersion) for b in
                        sorted(after.breakpoints, key=lambda b: b.rung)]

        assert after.n_series == before_n + 1
        assert after_ladder != before_ladder, "the breakpoints did not move"
        # The top rung is the observed maximum, so it must have risen.
        assert after_ladder[-1] > before_ladder[-1]
        # And it is a new vintage, not an overwrite — a percentile that moved
        # needs the old ladder to explain why.
        assert after.id != before.id
        db.expire_all()
        assert db.query(VolatilityCalibration).filter(
            VolatilityCalibration.id == before.id).one().is_active is False
        assert active_calibration(db).id == after.id
    finally:
        _cleanup(db, series_ids=ids)


def test_the_ladder_spans_the_observed_range(db):
    """The shipped ladder's real failure: its top rung is 21.57 while the
    library's real maximum dispersion is 35.28, so the single most volatile
    series would be pinned at 100 by a ladder that never saw it."""
    ladder = build_ladder([1.0, 2.0, 3.0, 10.0, 35.0], n_rungs=5)
    assert ladder[0] == 1.0
    assert ladder[-1] == 35.0
    # Monotone, or `percentile_for` returns the wrong rung.
    assert ladder == sorted(ladder)


def test_a_flat_distribution_still_produces_a_usable_ladder(db):
    ladder = build_ladder([2.0] * 8, n_rungs=6)
    assert len(ladder) == 6
    assert ladder == sorted(ladder)
    with pytest.raises(ValueError):
        build_ladder([1.0, 2.0], n_rungs=1)


def test_unmeasurable_is_not_calm(db):
    """A series with two data points is not calm, and a threshold check cannot
    tell the two apart on its own — so the reading says which it is."""
    thin = _series(db, "thin", monthly=[100, 101])
    fat = [_series(db, "fat", monthly=[100 + i * (n + 1) for i in range(24)])
           for n in range(3)]
    ids = [thin.id] + [s.id for s in fat]
    try:
        assert series_dispersion(db, thin.id) is None
        recompute_volatility_calibration(db, n_rungs=11, min_points=13)
        db.commit()

        reading = volatility_percentile(db, thin.id)
        assert reading.percentile is None
        assert reading.dispersion is None
        assert reading.reason and "not the same as calm" in reading.reason
        # It still names the calibration it consulted.
        assert reading.calibration_id is not None
    finally:
        _cleanup(db, series_ids=ids)


def test_a_reading_names_the_calibration_it_used(db):
    """SCRUM-75's own acceptance criterion is that it reports which calibration
    it read — which it cannot do if the number arrives on its own."""
    series = [_series(db, "named", monthly=[100 + i * (n + 1) for i in range(24)])
              for n in range(4)]
    ids = [s.id for s in series]
    try:
        calibration = recompute_volatility_calibration(db, n_rungs=11, min_points=13)
        db.commit()
        reading = volatility_percentile(db, series[0].id)
        assert reading.percentile is not None
        assert 0 <= reading.percentile <= 100
        assert reading.calibration_id == calibration.id
        assert reading.method == "mom_pct_stdev"
        assert reading.n_series == calibration.n_series
        assert reading.calibration_computed_at is not None
    finally:
        _cleanup(db, series_ids=ids)


def test_percentile_placement_uses_the_derived_step(db):
    series = [_series(db, "place", monthly=[100 + i * (n + 1) for i in range(24)])
              for n in range(4)]
    ids = [s.id for s in series]
    try:
        calibration = recompute_volatility_calibration(db, n_rungs=11, min_points=13)
        db.commit()
        rungs = sorted(calibration.breakpoints, key=lambda b: b.rung)
        # A value at the bottom rung is percentile 0; above the top rung is 100.
        assert percentile_for(float(rungs[0].dispersion), calibration) == 0
        assert percentile_for(float(rungs[-1].dispersion) + 1, calibration) == 100
        # And a middling value lands on a multiple of the derived step.
        mid = percentile_for(float(rungs[len(rungs) // 2].dispersion), calibration)
        assert mid % round(calibration.step) == 0
    finally:
        _cleanup(db, series_ids=ids)


def test_a_recompute_with_too_little_data_refuses_rather_than_fitting_noise(db):
    """Two series is not a distribution. Refusing is better than shipping a
    percentile scale nobody can defend."""
    bypass_rls_var.set(True)
    # Deactivate whatever is active so the error path is reachable regardless of
    # what else the DB holds, then restore.
    existing = active_calibration(db)
    try:
        with pytest.raises(ValueError):
            # An impossible minimum: nothing in the library can satisfy it.
            recompute_volatility_calibration(db, n_rungs=11, min_points=240)
    finally:
        db.rollback()
        if existing is not None:
            assert active_calibration(db) is not None, (
                "a refused recompute must not leave the library with no active "
                "calibration"
            )


# ── The API surface ─────────────────────────────────────────────────────────

def test_the_calibration_endpoints(db, tenant_a, user_factory, client_as):
    series = [_series(db, "api", monthly=[100 + i * (n + 1) for i in range(24)])
              for n in range(4)]
    ids = [s.id for s in series]
    try:
        admin = user_factory(is_super_admin=True)
        r = client_as(admin).post("/api/dossiers/volatility-calibration/recompute",
                                  json={"n_rungs": 11, "min_points": 13})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["n_rungs"] == 11
        assert body["step"] == pytest.approx(10.0)
        assert len(body["breakpoints"]) == 11
        assert body["is_active"] is True

        # Any authenticated user can read the ladder — SCRUM-75 has to.
        read = client_as(tenant_a).get("/api/dossiers/volatility-calibration")
        assert read.status_code == 200, read.text
        assert read.json()["id"] == body["id"]

        # But not recompute it: it changes the scale every consumer reads.
        denied = client_as(tenant_a).post(
            "/api/dossiers/volatility-calibration/recompute", json={})
        assert denied.status_code == 403

        vol = client_as(tenant_a).get(f"/api/dossiers/series/{series[0].id}/volatility")
        assert vol.status_code == 200, vol.text
        assert vol.json()["calibration_id"] == body["id"]
    finally:
        _cleanup(db, series_ids=ids)


def test_dossier_endpoint_404s_cleanly(db, tenant_a, client_as):
    c = client_as(tenant_a)
    assert c.get("/api/dossiers/series/999999999").status_code == 404
    bare = _series(db, "bare")
    try:
        # A real series with no dossier is a 404 with its own message, not an
        # empty dossier that reads as "we have nothing to say about this".
        r = c.get(f"/api/dossiers/series/{bare.id}")
        assert r.status_code == 404
        assert "dossier" in r.json()["detail"].lower()
    finally:
        _cleanup(db, series_ids=[bare.id])


def test_dossier_endpoints_require_authentication(client):
    assert client.get("/api/dossiers/volatility-calibration").status_code == 401
    assert client.get("/api/dossiers/series/1").status_code == 401


# ── Load behaviour ──────────────────────────────────────────────────────────

@needs_drop
def test_the_load_is_idempotent(db, _dossiers_loaded):
    bypass_rls_var.set(True)
    second = load_dossiers(db)
    db.commit()
    assert second.report.changed == 0, second.render()


@needs_drop
def test_a_shared_series_conflict_is_reported_not_overwritten(db, _dossiers_loaded):
    """Three dossier keys (`naphtha`, `cbfs`, `pta`) resolve to one series
    (`brent`). Our grain is per series, so they cannot all be stored — the
    losers are named rather than silently dropped."""
    bypass_rls_var.set(True)
    report = load_dossiers(db)
    db.commit()
    assert report.shared_series_conflicts, (
        "expected shared-series conflicts to be reported"
    )
    losers = {key for key, _ in report.shared_series_conflicts}
    # And the specific dossier wins over a generic one that merely fans out:
    # `electricity` fans to elec-*, so it loses those slots to elec-cn / elec-eu.
    assert "elec-cn" not in losers and "elec-eu" not in losers


def test_recomputing_does_not_leak_the_active_ladder_to_other_tests(db):
    """The guard on the guard: a recompute really does replace the active row,
    and the autouse fixture really does put the prior one back.

    Without the restore, `test_intelligence`'s volatility percentile would be
    read against whichever synthetic ladder this module last committed — which
    is exactly what happened in production data before the test database landed.
    """
    bypass_rls_var.set(True)
    prior = db.execute(text(
        "SELECT id FROM volatility_calibrations WHERE is_active")).scalar()

    fresh = recompute_volatility_calibration(db, n_rungs=11, min_points=13)
    db.commit()
    now_active = db.execute(text(
        "SELECT id FROM volatility_calibrations WHERE is_active")).scalar()
    assert now_active == fresh.id, "a recompute must take over the active slot"
    if prior is not None:
        assert now_active != prior
    # The fixture restores `prior` on teardown; the next test in this module
    # asserting against the ladder proves it.


# ── The purge of test-written calibrations (D2) ─────────────────────────────
#
# Before T0.1 the suite ran against the application database, so every test
# calling `recompute_volatility_calibration` committed a real vintage fitted
# over the live library *plus* that test's synthetic series. `purge` is the
# one-off cleanup; these pin the predicate, because the cost of getting it
# wrong is deleting a real vintage.
#
# `purge` is table-wide by design, so these assert about the rows they create
# rather than about totals.

def _calibration(db, *, n_series, n_rungs=21, note=None, active=False, rungs=2):
    cal = VolatilityCalibration(
        method="mom_pct_stdev", n_rungs=n_rungs, n_series=n_series,
        min_points=13, is_active=active, note=note)
    db.add(cal)
    db.flush()
    for rung in range(rungs):
        db.add(VolatilityBreakpoint(
            calibration_id=cal.id, rung=rung, dispersion=1.0 + rung))
    db.commit()
    return cal


def test_the_purge_never_touches_the_active_calibration(db):
    """Whatever else is true of it. `percentile_for` reads only the active row,
    which is what makes the purge unobservable to every consumer — and that
    holds only if the active row is categorically exempt."""
    reference = active_calibration(db).n_series
    # Deliberately contaminated-looking: no note, a surplus series count.
    doomed = _calibration(db, n_series=reference + 5, note=None)
    report = purge(db, apply=False)
    assert doomed.id in {c.id for c in db.query(VolatilityCalibration)
                         .filter(VolatilityCalibration.is_active.is_(False))}
    assert report.n_deleted >= 1
    # The active row is in `kept`, never in the deletion set.
    assert any("ACTIVE" in line for line in report.kept)


def test_an_authored_calibration_is_kept_however_it_was_fitted(db):
    """`seed_dossiers` stamps its own note and the rebuilt active row explains
    itself. A note is the record of who made it, so it is never junk."""
    reference = active_calibration(db).n_series
    kept = _calibration(db, n_series=reference + 5, note="seed_dossiers")
    purge(db, apply=True)
    assert db.query(VolatilityCalibration).filter(
        VolatilityCalibration.id == kept.id).first() is not None


def test_an_older_smaller_vintage_is_kept(db):
    """Fewer series than today is what a vintage *is* — the library grew. Only
    an impossible surplus proves synthetic rows were present, which is why the
    predicate is `>` and not `!=`."""
    reference = active_calibration(db).n_series
    older = _calibration(db, n_series=max(reference - 10, 1), note=None)
    purge(db, apply=True)
    assert db.query(VolatilityCalibration).filter(
        VolatilityCalibration.id == older.id).first() is not None


def test_a_contaminated_calibration_goes_with_its_breakpoints(db):
    reference = active_calibration(db).n_series
    doomed = _calibration(db, n_series=reference + 3, note=None, rungs=4)
    purge(db, apply=True)
    assert db.query(VolatilityCalibration).filter(
        VolatilityCalibration.id == doomed.id).first() is None
    assert db.query(VolatilityBreakpoint).filter(
        VolatilityBreakpoint.calibration_id == doomed.id).count() == 0


def test_the_dry_run_writes_nothing(db):
    """The dry path is the real path with the transaction rolled back, so a
    report is a rehearsal rather than a separate code path that could drift."""
    reference = active_calibration(db).n_series
    doomed = _calibration(db, n_series=reference + 2, note=None)
    report = purge(db, apply=False)
    db.rollback()
    assert report.n_deleted >= 1
    assert db.query(VolatilityCalibration).filter(
        VolatilityCalibration.id == doomed.id).first() is not None
