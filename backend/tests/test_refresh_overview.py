"""Refresh overview, alerts and the daily check (Jira 209 S5).

* every index gets one row: route (S1), last refresh (S2), freshness (S4),
  and flags saying what is wrong;
* a scheduled refresh alerts after FAILURES_BEFORE_ALERT failures in a row,
  not after one;
* late data alerts, and says when nothing updates the index at all;
* late content-drop series are ONE alert — one action fixes them all;
* the daily check records itself as a `refresh_check` run, logs every alert
  and sends the summary to Sentry (stubbed here); it is on the beat schedule;
* the overview API is super-admin only.

Fixtures come from test_refresh_monitor; series are aged with quarterly
values against a fixed `as_of`. The test database holds other series too, so
assertions look only at the alerts naming this test's series.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.models.index_data import IndexValue
from app.models.index_refresh import IndexRefreshRun
from app.services.refresh_log import record
from app.services.refresh_overview import (
    CONTENT_DROP_LATE, FAILING, FAILURES_BEFORE_ALERT, LATE, build_alerts, refresh_overview,
)
from app.tasks import refresh_check
from tests.test_refresh_monitor import _run, _series, made  # noqa: F401 — fixture

AS_OF = date(2026, 11, 2)


def _quarter(db, series, year, quarter, value=100.0):
    db.add(IndexValue(commodity_id=series.id, region="Europe", year=year, quarter=quarter,
                      value=value, source="seed"))
    db.commit()


def _row(db, series, as_of=AS_OF):
    return next(r for r in refresh_overview(db, as_of=as_of) if r.commodity_id == series.id)


def _alerts_for(db, series_ids, as_of=AS_OF):
    return [a for a in build_alerts(refresh_overview(db, as_of=as_of))
            if set(a.commodity_ids) & set(series_ids)]


# ── Rows and flags ───────────────────────────────────────────────────────────

def test_a_row_joins_route_last_refresh_and_freshness(db, made):
    s = _series(db, made, scrape_enabled=False, frequency="Quarterly")
    _quarter(db, s, 2026, 3)
    run = _run(db, made)
    record(db, run, commodity_id=s.id, label=s.name, route="scraper", outcome="ok",
           rows=1, latest_period="2026Q3")
    row = _row(db, s)
    assert row.route == "none"                        # scrape_enabled is off
    assert row.state == "current" and row.last_actual == "2026-09"
    assert row.stale_after_months == 6 and row.stale_rule == "quarterly"
    assert row.last_outcome == "ok" and row.last_success_at is not None
    assert row.flags == ["no_route"]


def test_problems_come_first(db, made):
    late = _series(db, made, frequency="Quarterly")
    _quarter(db, late, 2025, 1)
    fine = _series(db, made, frequency="Quarterly")
    _quarter(db, fine, 2026, 3)
    order = [r.commodity_id for r in refresh_overview(db, as_of=AS_OF)]
    assert order.index(late.id) < order.index(fine.id)


# ── Failing ──────────────────────────────────────────────────────────────────

def test_one_failure_is_not_an_alert_two_in_a_row_are(db, made):
    s = _series(db, made, frequency="Quarterly")
    _quarter(db, s, 2026, 3)
    run = _run(db, made)
    record(db, run, commodity_id=s.id, label=s.name, route="scraper", outcome="failed",
           error="HTTPStatusError: 400 Bad Request\ntraceback...")
    assert FAILING not in _row(db, s).flags
    assert _alerts_for(db, [s.id]) == []

    for _ in range(FAILURES_BEFORE_ALERT - 1):
        record(db, run, commodity_id=s.id, label=s.name, route="scraper", outcome="failed",
               error="HTTPStatusError: 400 Bad Request\ntraceback...")
    assert FAILING in _row(db, s).flags
    [alert] = _alerts_for(db, [s.id])
    assert alert.kind == FAILING
    assert f"failed {FAILURES_BEFORE_ALERT} runs in a row" in alert.message
    assert "400 Bad Request" in alert.message and "traceback" not in alert.message


def test_a_success_clears_the_alert(db, made):
    s = _series(db, made, frequency="Quarterly")
    _quarter(db, s, 2026, 3)
    run = _run(db, made)
    for _ in range(FAILURES_BEFORE_ALERT):
        record(db, run, commodity_id=s.id, label=s.name, route="scraper", outcome="no_data")
    record(db, run, commodity_id=s.id, label=s.name, route="scraper", outcome="ok", rows=1)
    assert _alerts_for(db, [s.id]) == []


# ── Late ─────────────────────────────────────────────────────────────────────

def test_late_data_alerts_and_says_when_nothing_updates_it(db, made):
    s = _series(db, made, frequency="Quarterly")
    _quarter(db, s, 2025, 4)                          # observed through 2025-12: 11 months old
    row = _row(db, s)
    assert LATE in row.flags and "no_route" in row.flags
    [alert] = _alerts_for(db, [s.id])
    assert alert.kind == LATE
    assert "11 months old" in alert.message
    assert "nothing updates it" in alert.message


def test_late_content_drop_series_are_one_alert(db, made):
    a = _series(db, made, commodity_key=f"rf-{uuid.uuid4().hex[:6]}", frequency="Monthly")
    b = _series(db, made, commodity_key=f"rf-{uuid.uuid4().hex[:6]}", frequency="Monthly")
    for s in (a, b):
        _quarter(db, s, 2026, 2)                      # through 2026-06
    assert _alerts_for(db, [a.id, b.id]) == []        # 5 months old: within the drop's 6

    alerts = _alerts_for(db, [a.id, b.id], as_of=date(2027, 1, 1))   # 7 months old
    [grouped] = alerts
    assert grouped.kind == CONTENT_DROP_LATE
    assert {a.id, b.id} <= set(grouped.commodity_ids)
    assert "re-load the content drop" in grouped.message


# ── The daily check ──────────────────────────────────────────────────────────

def test_the_daily_check_records_logs_and_reports(db, made, monkeypatch, caplog):
    s = _series(db, made, frequency="Quarterly")
    _quarter(db, s, 2024, 1)                          # long late, so there is an alert
    sent = []
    monkeypatch.setattr(refresh_check, "capture_message", lambda msg, level: sent.append((msg, level)))

    result = refresh_check.check_refresh_health()

    run = (db.query(IndexRefreshRun).filter(IndexRefreshRun.task == "refresh_check")
           .order_by(IndexRefreshRun.started_at.desc()).first())
    made["runs"].append(run.id)
    assert result["alerts"] >= 1
    assert run.note == result["summary"] and run.finished_at is not None
    assert any(s.name in r.getMessage() for r in caplog.records)
    [(msg, level)] = sent
    assert level == "warning" and s.name in msg


def test_the_check_is_on_the_beat_schedule():
    import celeryconfig
    assert "app.tasks.refresh_check" in celeryconfig.imports
    entry = celeryconfig.beat_schedule["check-refresh-health-daily"]
    assert entry["task"] == "app.tasks.refresh_check.check_refresh_health"


# ── API ──────────────────────────────────────────────────────────────────────

def test_overview_api_is_super_admin_only(db, made, client, client_as, tenant_a, user_factory):
    s = _series(db, made, frequency="Quarterly")
    _quarter(db, s, 2025, 1)
    assert client.get("/api/refresh/overview").status_code == 401
    assert client_as(tenant_a).get("/api/refresh/overview").status_code == 403

    r = client_as(user_factory(is_super_admin=True)).get(
        "/api/refresh/overview", params={"as_of": "2026-11-02"})
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"] == "2026-11-02"
    assert body["counts"]["late"] >= 1 and body["counts"]["alerts"] == len(body["alerts"])
    mine = next(i for i in body["indexes"] if i["commodity_id"] == s.id)
    assert "late" in mine["flags"] and mine["stale_rule"] == "quarterly"
    assert any(s.id in a["commodity_ids"] for a in body["alerts"])
