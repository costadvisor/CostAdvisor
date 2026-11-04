"""Index refresh and freshness monitoring (Jira 209), S1-S3.

* S1 every index gets exactly one refresh route, with the reason;
* S2 every refresh attempt is recorded, with a state per index;
* S3 the scheduled scrape handles each index on its own, retries transient
  errors (3 tries in total), never retries a client error, and checks values
  before writing so a rejected fetch leaves the last good value in place.

Scrapers here are fakes: no test touches the network. Every fixture series is
built here, without `commodity_key` unless a test is about the content drop.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.index_data import CommodityIndex, IndexValue
from app.models.index_refresh import IndexRefreshAttempt, IndexRefreshRun, IndexRefreshState
from app.services import refresh_route, safe_refresh
from app.services.refresh_log import MAX_ERROR_CHARS, finish_run, record, start_run
from app.services.refresh_route import (
    COMPOSITE, CONTENT_DROP, CONTENT_DROP_INTERVAL_MONTHS, MANUAL_UPLOAD, NONE, SCRAPER,
    refresh_routes,
)
from app.services.scraper import BaseScraper, ScrapedDataPoint
from app.tasks import scrape_indexes

REGION = "Europe"


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def made(db):
    """Series and runs a test creates; removed afterwards."""
    created = {"series": [], "runs": []}
    yield created
    db.rollback()
    bypass_rls_var.set(True)
    for run_id in created["runs"]:
        db.execute(text("DELETE FROM index_refresh_runs WHERE id = :i"), {"i": str(run_id)})
    for s in created["series"]:
        for table in ("index_refresh_state", "index_refresh_attempts", "index_values"):
            db.execute(text(f"DELETE FROM {table} WHERE commodity_id = :i"), {"i": s.id})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": s.id})
    db.commit()


def _series(db, made, **kw) -> CommodityIndex:
    kw.setdefault("scrape_enabled", False)
    s = CommodityIndex(name=f"rf-{uuid.uuid4().hex[:8]}", **kw)
    db.add(s)
    db.commit()
    made["series"].append(s)
    return s


def _run(db, made, task="test") -> IndexRefreshRun:
    run = start_run(db, task)
    made["runs"].append(run.id)
    return run


def _point(value, year=2026, quarter=3):
    return ScrapedDataPoint(region=REGION, year=year, quarter=quarter, value=value)


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://example.test/series")
    return httpx.HTTPStatusError(str(code), request=req, response=httpx.Response(code, request=req))


class _FakeScraper:
    """Returns (or raises) one scripted outcome per fetch call."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def write(self, db, commodity, data):
        return BaseScraper.write(self, db, commodity, data)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(safe_refresh, "RETRY_BACKOFF_SECONDS", (0.0, 0.0))


def _attempt(db, run):
    return db.query(IndexRefreshAttempt).filter(IndexRefreshAttempt.run_id == run.id).one()


# ── S1: one route per index ──────────────────────────────────────────────────

def test_every_route_is_assigned_with_a_reason(db, made, monkeypatch):
    content = _series(db, made, commodity_key=f"rf-{uuid.uuid4().hex[:6]}", frequency="Monthly")
    scraped = _series(db, made, scrape_enabled=True, frequency=None)
    disabled = _series(db, made, scrape_enabled=False)
    uploaded = _series(db, made)
    composite = _series(db, made, composite_expression="A * 1", composite_variables={"A": 1})
    bare = _series(db, made)
    monkeypatch.setitem(refresh_route.SCRAPER_REGISTRY, scraped.name, BaseScraper)
    monkeypatch.setitem(refresh_route.SCRAPER_REGISTRY, disabled.name, BaseScraper)
    db.add(IndexValue(commodity_id=uploaded.id, region=REGION, year=2026, quarter=1,
                      value=10, source="admin_upload"))
    db.commit()

    routes = {r.commodity_id: r for r in refresh_routes(db)}
    assert routes[content.id].route == CONTENT_DROP
    assert routes[content.id].expected_interval_months == CONTENT_DROP_INTERVAL_MONTHS
    assert routes[scraped.id].route == SCRAPER
    assert routes[scraped.id].expected_interval_months == 3   # scrapers write quarters
    assert routes[uploaded.id].route == MANUAL_UPLOAD
    assert routes[composite.id].route == COMPOSITE
    assert routes[bare.id].route == NONE
    assert routes[disabled.id].route == NONE
    assert "scrape_enabled is off" in routes[disabled.id].detail


def test_frequency_sets_the_expected_interval(db, made, monkeypatch):
    annual = _series(db, made, scrape_enabled=True, frequency="Annual")
    monkeypatch.setitem(refresh_route.SCRAPER_REGISTRY, annual.name, BaseScraper)
    route = next(r for r in refresh_routes(db) if r.commodity_id == annual.id)
    assert route.expected_interval_months == 12


# ── S2: the refresh log ──────────────────────────────────────────────────────

def test_a_success_then_failures_update_the_state(db, made):
    s = _series(db, made)
    run = _run(db, made)
    record(db, run, commodity_id=s.id, label=s.name, route=SCRAPER, outcome="ok",
           rows=4, latest_period="2026Q3")
    record(db, run, commodity_id=s.id, label=s.name, route=SCRAPER, outcome="failed",
           error="boom")
    record(db, run, commodity_id=s.id, label=s.name, route=SCRAPER, outcome="rejected",
           error="jump")

    state = db.get(IndexRefreshState, s.id)
    db.refresh(state)
    assert state.last_outcome == "rejected"
    assert state.consecutive_failures == 2
    assert state.last_success_at is not None        # kept from the success
    assert state.latest_period == "2026Q3"          # last GOOD period
    assert state.last_error == "jump"

    record(db, run, commodity_id=s.id, label=s.name, route=SCRAPER, outcome="ok", rows=1)
    db.refresh(state)
    assert state.consecutive_failures == 0 and state.last_error is None


def test_run_counters_and_status(db, made):
    s = _series(db, made)
    ok_run, mixed_run, bad_run = _run(db, made), _run(db, made), _run(db, made)
    record(db, ok_run, commodity_id=s.id, label="a", route=SCRAPER, outcome="ok")
    record(db, mixed_run, commodity_id=s.id, label="a", route=SCRAPER, outcome="ok")
    record(db, mixed_run, commodity_id=s.id, label="b", route=SCRAPER, outcome="no_data")
    record(db, bad_run, commodity_id=s.id, label="a", route=SCRAPER, outcome="failed")
    assert finish_run(db, ok_run).status == "ok"
    assert finish_run(db, mixed_run).status == "partial"
    assert (mixed_run.attempted, mixed_run.succeeded, mixed_run.no_data) == (2, 1, 1)
    assert finish_run(db, bad_run).status == "failed"
    assert finish_run(db, _run(db, made)).status == "ok"    # nothing to do


def test_team_source_attempts_never_touch_the_index_state(db, made, tenant_a):
    """A team's own source failing says nothing about the shared index."""
    from app.models.index_data import TeamIndexSource

    s = _series(db, made)
    source = TeamIndexSource(team_id=tenant_a["team_id"], commodity_id=s.id, region=REGION,
                             source_type="scrape_url", scrape_url="https://example.test",
                             created_by=tenant_a["user_id"])
    db.add(source)
    db.commit()
    try:
        run = _run(db, made)
        attempt = record(db, run, commodity_id=s.id, team_source_id=source.id,
                         label=f"team source {source.id}", route="team_source",
                         outcome="failed", error="x")
        assert attempt.team_source_id == source.id
        assert db.get(IndexRefreshState, s.id) is None
    finally:
        db.rollback()
        db.execute(text("DELETE FROM index_refresh_attempts WHERE team_source_id = :i"),
                   {"i": source.id})
        db.execute(text("DELETE FROM team_index_sources WHERE id = :i"), {"i": source.id})
        db.commit()


def test_long_errors_are_cut_and_unknown_outcomes_refused(db, made):
    s = _series(db, made)
    run = _run(db, made)
    attempt = record(db, run, commodity_id=s.id, label="a", route=SCRAPER, outcome="failed",
                     error="x" * (MAX_ERROR_CHARS + 500))
    assert len(attempt.error) == MAX_ERROR_CHARS
    with pytest.raises(ValueError):
        record(db, run, commodity_id=s.id, label="a", route=SCRAPER, outcome="maybe")


# ── S3: retries ──────────────────────────────────────────────────────────────

def test_a_transient_error_is_retried_and_then_succeeds(db, made):
    s = _series(db, made)
    run = _run(db, made)
    scraper = _FakeScraper(httpx.ConnectTimeout("slow"), [_point(100.0)])
    assert scrape_indexes._refresh_one(db, run, s, scraper) == "updated_1"
    a = _attempt(db, run)
    assert (a.outcome, a.tries, a.latest_period) == ("ok", 2, "2026Q3")


def test_retries_stop_at_three_tries(db, made):
    s = _series(db, made)
    run = _run(db, made)
    scraper = _FakeScraper(*[_status_error(503)] * 3)
    scrape_indexes._refresh_one(db, run, s, scraper)
    a = _attempt(db, run)
    assert scraper.calls == safe_refresh.MAX_TRIES == 3
    assert (a.outcome, a.tries) == ("failed", 3)


def test_a_client_error_is_not_retried(db, made):
    """The Eurostat 400s: the request is wrong and will be wrong again."""
    s = _series(db, made)
    run = _run(db, made)
    scraper = _FakeScraper(_status_error(400))
    scrape_indexes._refresh_one(db, run, s, scraper)
    a = _attempt(db, run)
    assert scraper.calls == 1
    assert (a.outcome, a.tries) == ("failed", 1)
    assert "400" in a.error


def test_an_empty_answer_is_no_data(db, made):
    s = _series(db, made)
    run = _run(db, made)
    scrape_indexes._refresh_one(db, run, s, _FakeScraper([]))
    assert _attempt(db, run).outcome == "no_data"


# ── S3: checks before writing keep the last good value ───────────────────────

def _stored(db, s):
    return float(db.query(IndexValue.value).filter(
        IndexValue.commodity_id == s.id, IndexValue.year == 2026, IndexValue.quarter == 3,
    ).scalar())


def test_a_zero_value_is_rejected_and_the_last_good_value_stays(db, made):
    s = _series(db, made)
    db.add(IndexValue(commodity_id=s.id, region=REGION, year=2026, quarter=3, value=100,
                      source="scraped"))
    db.commit()
    run = _run(db, made)
    scrape_indexes._refresh_one(db, run, s, _FakeScraper([_point(0.0)]))
    a = _attempt(db, run)
    assert a.outcome == "rejected" and "zero or negative" in a.error
    assert _stored(db, s) == 100.0


def test_a_tenfold_jump_is_rejected_and_the_last_good_value_stays(db, made):
    s = _series(db, made)
    db.add(IndexValue(commodity_id=s.id, region=REGION, year=2026, quarter=2, value=35,
                      source="scraped"))
    db.commit()
    run = _run(db, made)
    # A NEW quarter is compared with the latest stored one before it.
    scrape_indexes._refresh_one(db, run, s, _FakeScraper([_point(350.0)]))
    a = _attempt(db, run)
    assert a.outcome == "rejected" and "limit ×3" in a.error
    assert db.query(IndexValue).filter(IndexValue.commodity_id == s.id,
                                       IndexValue.quarter == 3).count() == 0


def test_a_normal_move_is_written(db, made):
    s = _series(db, made)
    db.add(IndexValue(commodity_id=s.id, region=REGION, year=2026, quarter=3, value=100,
                      source="scraped"))
    db.commit()
    run = _run(db, made)
    assert scrape_indexes._refresh_one(db, run, s, _FakeScraper([_point(130.0)])) == "updated_1"
    assert _stored(db, s) == 130.0


# ── S3: one failure no longer stops the run ──────────────────────────────────

def test_scrape_all_carries_on_after_a_failure_and_still_syncs_fx(db, made, monkeypatch):
    broken = _series(db, made, scrape_enabled=True)
    healthy = _series(db, made, scrape_enabled=True)
    fakes = {broken.name: lambda: _FakeScraper(_status_error(400)),
             healthy.name: lambda: _FakeScraper([_point(50.0)])}
    monkeypatch.setattr(scrape_indexes, "SCRAPER_REGISTRY", fakes)
    synced = []
    monkeypatch.setattr(scrape_indexes, "sync_fx_rates", lambda db: synced.append(1) or 7)

    results = scrape_indexes.scrape_all()
    run = db.query(IndexRefreshRun).filter(IndexRefreshRun.task == "scrape_all") \
        .order_by(IndexRefreshRun.started_at.desc()).first()
    made["runs"].append(run.id)

    assert results[broken.name].startswith("failed")
    assert results[healthy.name] == "updated_1"
    assert results["_fx_synced"] == 7 and synced == [1]
    assert run.status == "partial" and (run.succeeded, run.failed) == (1, 1)


def test_a_failing_fx_sync_does_not_fail_the_run(db, made, monkeypatch):
    monkeypatch.setattr(scrape_indexes, "SCRAPER_REGISTRY", {})

    def boom(db):
        raise RuntimeError("ecb down")
    monkeypatch.setattr(scrape_indexes, "sync_fx_rates", boom)
    results = scrape_indexes.scrape_all()
    run = db.query(IndexRefreshRun).filter(IndexRefreshRun.task == "scrape_all") \
        .order_by(IndexRefreshRun.started_at.desc()).first()
    made["runs"].append(run.id)
    assert results["_fx_synced"].startswith("failed")
    assert run.finished_at is not None


# ── API ──────────────────────────────────────────────────────────────────────

def test_routes_need_sign_in_and_status_needs_super_admin(client, client_as, tenant_a, user_factory):
    assert client.get("/api/refresh/routes").status_code == 401
    member = client_as(tenant_a)
    r = member.get("/api/refresh/routes")
    assert r.status_code == 200 and set(r.json()["counts"]) >= {CONTENT_DROP, SCRAPER, NONE}
    assert member.get("/api/refresh/status").status_code == 403
    assert member.get("/api/refresh/runs").status_code == 403

    admin = client_as(user_factory(is_super_admin=True))
    assert admin.get("/api/refresh/status").status_code == 200
    assert admin.get("/api/refresh/runs", params={"limit": 3}).status_code == 200


def test_status_puts_failing_indexes_first(db, made, client_as, user_factory):
    s = _series(db, made)
    run = _run(db, made)
    record(db, run, commodity_id=s.id, label=s.name, route=SCRAPER, outcome="failed", error="x")
    body = client_as(user_factory(is_super_admin=True)).get("/api/refresh/status").json()
    assert body["failing"] >= 1
    assert body["indexes"][0]["last_outcome"] != "ok"
    assert s.id in {i["commodity_id"] for i in body["indexes"][:body["failing"]]}
