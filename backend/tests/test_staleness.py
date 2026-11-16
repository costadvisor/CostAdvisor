"""Stale flags on text (LIVE-5).

Rules (no database): data moved more than 10% since the baseline; typed
numbers drifted beyond what a review acknowledged; late data, unless a review
acknowledged that same month; older than six months (from a review, if any);
past `expires_at`. Readers get one calm sentence — never for age alone.

Database: the check fills a baseline and flags a made-up section when its
series moves; a review re-pins it and clears the flag; a typed "as of" date
that no longer matches flags its section. The worklist API is for content
approvers. The daily task records a run and is on the beat schedule.

Made-up text and series.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.index_layer import IndexMonthlyValue
from app.models.market_report import MarketReport, MarketReportSection
from app.models.index_refresh import IndexRefreshRun
from app.models.content_load import ContentLoad
from app.services.staleness import (
    DATA_LATE, DATA_MOVED, EXPIRED, OLD_TEXT, TYPED_DRIFTED, Text, _point_at, check, mark_reviewed,
    reader_note, reasons_for,
)
from app.tasks import staleness_check
from tests.test_live_resolver import _series, made  # noqa: F401 — fixture

TODAY = date(2026, 11, 10)
JUN, JUL = 2026 * 12 + 5, 2026 * 12 + 6     # month indexes


def _text(series=(), baseline=None, written=date(2026, 8, 1), expires=None):
    row = SimpleNamespace(baseline=baseline)
    return Text("section", row, "rep/process", written, list(series), expires_at=expires)


def _codes(reasons):
    return [r["code"] for r in reasons]


# ── Rules ────────────────────────────────────────────────────────────────────

def test_a_move_over_ten_percent_since_the_baseline_is_stale():
    base = {"series": {"brent": {"month": "2026-06", "value": 100.0}}}
    t = _text(["brent"], base)
    moved = reasons_for(t, TODAY, {"brent": {JUN: 100.0, JUL: 112.0}}, {}, 0)
    assert _codes(moved) == [DATA_MOVED] and "brent +12.0% since Jun 2026" in moved[0]["detail"]
    assert "revised" not in moved[0]["detail"]
    assert reasons_for(t, TODAY, {"brent": {JUN: 100.0, JUL: 109.0}}, {}, 0) == []


# ── R2: a replaced history is not a market move ──────────────────────────────

def test_a_replaced_history_does_not_flag_a_move_the_market_never_made():
    # Baseline Jun 100 on a drawn curve; the real series that replaced it has
    # Jun 80 and Jul 84 (+5%). Against the stored 100 that is -16%: a false alarm.
    t = _text(["power"], {"series": {"power": {"month": "2026-06", "value": 100.0}}})
    assert reasons_for(t, TODAY, {"power": {JUN: 80.0, JUL: 84.0}}, {}, 0) == []


def test_a_real_move_after_a_replaced_history_is_flagged_and_says_so():
    t = _text(["power"], {"series": {"power": {"month": "2026-06", "value": 100.0}}})
    r = reasons_for(t, TODAY, {"power": {JUN: 80.0, JUL: 92.0}}, {}, 0)      # +15% on today's series
    assert _codes(r) == [DATA_MOVED] and r[0]["moves"][0]["history_revised"] is True
    assert "history revised since" in r[0]["detail"]
    assert reader_note(r).startswith("May be out of date: power has moved +15%")


def test_no_flag_when_the_baseline_month_is_gone_from_todays_history():
    t = _text(["power"], {"series": {"power": {"month": "2026-06", "value": 100.0}}})
    assert reasons_for(t, TODAY, {"power": {JUL: 150.0}}, {}, 0) == []


def test_a_baseline_takes_only_points_that_existed_when_the_text_was_written():
    loaded = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
    history = {JUN: (100.0, loaded - timedelta(days=1)), JUL: (110.0, loaded + timedelta(days=3))}
    assert _point_at(history, date(2026, 8, 1), loaded) == {"month": "2026-06", "value": 100.0}
    assert _point_at(history, date(2026, 8, 1)) == {"month": "2026-07", "value": 110.0}


def test_drift_counts_only_beyond_what_a_review_acknowledged():
    assert _codes(reasons_for(_text(), TODAY, {}, {}, 3)) == [TYPED_DRIFTED]
    acked = _text(baseline={"ack": {TYPED_DRIFTED: 3}, "reviewed_at": "2026-11-01T00:00:00"})
    assert reasons_for(acked, TODAY, {}, {}, 3) == []
    assert _codes(reasons_for(acked, TODAY, {}, {}, 4)) == [TYPED_DRIFTED]


def test_late_data_is_flagged_until_a_review_acknowledges_that_month():
    late = {"brent": SimpleNamespace(state="stale", last_actual="2026-06")}
    assert _codes(reasons_for(_text(["brent"]), TODAY, {}, late, 0)) == [DATA_LATE]
    acked = _text(["brent"], {"ack": {DATA_LATE: {"brent": "2026-06"}}, "reviewed_at": "2026-11-01T00:00:00"})
    assert reasons_for(acked, TODAY, {}, late, 0) == []
    newer = {"brent": SimpleNamespace(state="stale", last_actual="2026-07")}
    assert _codes(reasons_for(acked, TODAY, {}, newer, 0)) == [DATA_LATE]


def test_old_text_counts_from_its_date_or_its_review_and_expiry_is_kept():
    old = reasons_for(_text(written=date(2026, 3, 1)), TODAY, {}, {}, 0)
    assert _codes(old) == [OLD_TEXT] and old[0]["months"] == 8
    reviewed = _text(written=date(2026, 3, 1), baseline={"reviewed_at": "2026-10-01T09:00:00"})
    assert reasons_for(reviewed, TODAY, {}, {}, 0) == []
    assert _codes(reasons_for(_text(expires=date(2026, 10, 1)), TODAY, {}, {}, 0)) == [EXPIRED]


def test_readers_get_one_calm_sentence_and_none_for_age_alone():
    moved = [{"code": DATA_MOVED, "moves": [{"series": "brent", "pct": 18.2, "from": "2026-06"}]},
             {"code": TYPED_DRIFTED, "count": 2}]
    assert reader_note(moved) == "May be out of date: brent has moved +18% since this was written (Jun 2026)."
    assert "no longer match" in reader_note([{"code": TYPED_DRIFTED, "count": 1}])
    assert reader_note([{"code": OLD_TEXT, "months": 9, "detail": "x"}]) is None
    assert reader_note(None) is None


# ── The check, on a made-up report ───────────────────────────────────────────

@pytest.fixture
def report(db, made):
    s = _series(db, made, [(2026, 6, 100, "actual")])
    slug = f"test_stale_{uuid.uuid4().hex[:8]}"
    db.add(MarketReport(slug=slug, report_file=f"{slug}.html", name="Stale test", as_of=date(2026, 8, 1)))
    db.flush()
    db.add(MarketReportSection(
        slug=slug, section_id="supply", ordinal=6,
        html=f"<p>Gas stands at {{{{index:{s.commodity_key}|last}}}}.</p>",
        derived_from={"series": [s.commodity_key], "recipe_lines": [], "bars": [],
                      "tokens": [f"{{{{index:{s.commodity_key}|last}}}}"]}))
    db.commit()
    yield {"slug": slug, "series": s}
    db.rollback()
    bypass_rls_var.set(True)
    db.execute(text("DELETE FROM market_reports WHERE slug = :s"), {"s": slug})
    db.commit()


def _section(db, slug):
    return db.query(MarketReportSection).filter_by(slug=slug).one()


def test_the_check_pins_a_baseline_then_flags_a_move_and_a_review_clears_it(db, report):
    check(db, today=TODAY)
    sec = _section(db, report["slug"])
    assert sec.baseline["series"][report["series"].commodity_key] == {"month": "2026-06", "value": 100.0}
    assert sec.is_stale is False

    db.add(IndexMonthlyValue(commodity_id=report["series"].id, year=2026, month=7, value=125, kind="actual"))
    db.flush()
    r = check(db, today=TODAY)
    sec = _section(db, report["slug"])
    assert sec.is_stale and _codes(sec.stale_reasons) == [DATA_MOVED]
    assert r.newly_stale >= 1

    mark_reviewed(db, "section", sec.id, by="tester", today=TODAY)
    check(db, today=TODAY)
    sec = _section(db, report["slug"])
    assert sec.is_stale is False
    assert sec.baseline["series"][report["series"].commodity_key]["value"] == 125.0
    assert sec.baseline["reviewed_by"] == "tester"
    db.rollback()


def test_a_typed_as_of_date_that_drifted_flags_its_section(db, report):
    s = report["series"]
    db.add(MarketReportSection(slug=report["slug"], section_id="overview", ordinal=1,
                               html=f"<p>{s.name} data as of September 2026 shows a tight market.</p>"))
    db.flush()
    check(db, today=TODAY)
    sec = db.query(MarketReportSection).filter_by(slug=report["slug"], section_id="overview").one()
    assert sec.is_stale and _codes(sec.stale_reasons) == [TYPED_DRIFTED]
    assert reader_note(sec.stale_reasons).startswith("May be out of date")
    db.rollback()


def test_a_drop_text_baseline_ignores_points_fetched_after_the_load(db, report):
    # R2: Laurent's export is loaded; later the app fetches a newer month itself.
    # The text was written against what the load brought, so its baseline is June.
    now = datetime.now(timezone.utc)
    load = ContentLoad(source_commit="test-r2", source_date=datetime(2026, 11, 6, tzinfo=timezone.utc),
                       started_at=now, finished_at=now + timedelta(days=1), dry_run=False)
    db.add(load)
    db.add(IndexMonthlyValue(commodity_id=report["series"].id, year=2026, month=7, value=125,
                             kind="actual", created_at=now + timedelta(days=2)))
    db.commit()
    try:
        check(db, today=TODAY)
        sec = _section(db, report["slug"])
        assert sec.baseline["series"][report["series"].commodity_key] == {"month": "2026-06", "value": 100.0}
        # ... and the July point it did not know about is a +25% move since.
        assert sec.is_stale and _codes(sec.stale_reasons) == [DATA_MOVED]
    finally:
        db.rollback()
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM content_loads WHERE source_commit = 'test-r2'"))
        db.commit()


# ── API and task ─────────────────────────────────────────────────────────────

def test_the_worklist_is_for_content_approvers(db, report, client_as, tenant_a, user_factory):
    assert client_as(tenant_a).get("/api/staleness/texts").status_code == 403
    check(db, today=TODAY)                       # pins the baseline: Jun 100
    db.add(IndexMonthlyValue(commodity_id=report["series"].id, year=2026, month=7, value=125, kind="actual"))
    db.commit()
    check(db, today=TODAY)                       # data moved +25%
    db.commit()
    admin = client_as(user_factory(is_super_admin=True))
    body = admin.get("/api/staleness/texts", params={"kind": "section"}).json()
    item = next(i for i in body["items"] if report["slug"] in i["where"])
    assert item["reader_note"].startswith("May be out of date")
    r = admin.post("/api/staleness/review", json={"kind": "section", "id": item["id"]})
    assert r.status_code == 200 and r.json()["reasons"] == []
    assert _section(db, report["slug"]).is_stale is False


def test_the_daily_task_records_its_run_and_is_scheduled(db, report, monkeypatch):
    monkeypatch.setattr(staleness_check, "capture_message", lambda *a, **k: None)
    result = staleness_check.check_staleness()
    run = (db.query(IndexRefreshRun).filter(IndexRefreshRun.task == "staleness_check")
           .order_by(IndexRefreshRun.started_at.desc()).first())
    assert run.note == result["summary"] and run.finished_at is not None
    db.execute(text("DELETE FROM index_refresh_runs WHERE id = :i"), {"i": str(run.id)})
    db.commit()
    import celeryconfig
    assert "app.tasks.staleness_check" in celeryconfig.imports
    assert celeryconfig.beat_schedule["check-staleness-daily"]["task"] == \
        "app.tasks.staleness_check.check_staleness"
