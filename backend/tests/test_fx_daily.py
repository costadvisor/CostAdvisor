"""FX daily history endpoint (Scrum 14d daily extension).

Covers GET /api/fx-rates/daily: requires auth and returns a pair's stored daily
series newest-first.
"""
from __future__ import annotations

from datetime import date

from app.database import SessionLocal, bypass_rls_var
from app.models.fx_daily_rate import FxDailyRate


def _seed_daily(pairs_dates):
    """Insert (from, to, date, rate) rows with RLS bypassed; return a cleanup fn."""
    bypass_rls_var.set(True)
    s = SessionLocal()
    for fc, tc, d, r in pairs_dates:
        s.add(FxDailyRate(from_currency=fc, to_currency=tc, date=d, rate=r))
    s.commit()

    def cleanup():
        bypass_rls_var.set(True)
        for fc, tc, d, _ in pairs_dates:
            row = s.query(FxDailyRate).filter_by(from_currency=fc, to_currency=tc, date=d).first()
            if row:
                s.delete(row)
        s.commit()
        s.close()
    return cleanup


def test_daily_requires_auth(client):
    r = client.get("/api/fx-rates/daily?from_currency=XAA&to_currency=XBB")
    assert r.status_code == 401, r.text


def test_public_daily_no_auth_ascending(client):
    cleanup = _seed_daily([
        ("XPA", "XPB", date(2026, 2, 1), 2.0),
        ("XPA", "XPB", date(2026, 2, 2), 2.1),
    ])
    try:
        # No auth cookie — public endpoint must still serve
        r = client.get("/api/fx-rates/public-daily?from_currency=XPA&to_currency=XPB")
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data) == 2
        assert data[0]["date"] == "2026-02-01"   # oldest-first
        assert data[-1]["date"] == "2026-02-02"
    finally:
        cleanup()


def test_daily_returns_series_newest_first(client_as, user_factory):
    user = user_factory()
    cleanup = _seed_daily([
        ("XAA", "XBB", date(2026, 1, 1), 1.10),
        ("XAA", "XBB", date(2026, 1, 2), 1.20),
        ("XAA", "XBB", date(2026, 1, 3), 1.30),
    ])
    try:
        r = client_as(user).get("/api/fx-rates/daily?from_currency=XAA&to_currency=XBB")
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data) == 3
        # newest-first
        assert data[0]["date"] == "2026-01-03"
        assert data[0]["rate"] == 1.3
        assert data[-1]["date"] == "2026-01-01"
    finally:
        cleanup()
