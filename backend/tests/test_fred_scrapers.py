"""FD-1 remainder — 3 new FRED-backed commodity scrapers (CU, CORN, LNG-JKM).

Each scraper is a thin subclass of the already-tested FREDScraper base
(commodity_name + SERIES_ID only) — these tests exist to pin the wiring
(name/series id) and the shared quarterly-averaging behavior, not to
re-test FREDScraper's parsing logic itself.
"""
import asyncio
from types import SimpleNamespace

import app.services.scrapers.fred as fred_module
from app.services.scrapers.fred import (
    FREDCopperScraper, FREDCornScraper, FREDLNGJKMScraper,
)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _fake_client(payload: dict):
    class FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _FakeResponse(payload)

    return FakeAsyncClient


def _set_fred_key(monkeypatch):
    monkeypatch.setattr(fred_module, "get_settings", lambda: SimpleNamespace(fred_api_key="test-key"))


def _fixture(values):
    return {"observations": [{"date": d, "value": v} for d, v in values]}


def test_fred_copper_scraper_quarterly_average(monkeypatch):
    _set_fred_key(monkeypatch)
    fixture = _fixture([
        ("2025-01-01", "9000.0"),
        ("2025-02-01", "9100.0"),
        ("2025-03-01", "9200.0"),
    ])
    monkeypatch.setattr(fred_module.httpx, "AsyncClient", _fake_client(fixture))
    points = asyncio.run(FREDCopperScraper().fetch())
    assert [(p.year, p.quarter, p.value) for p in points] == [(2025, 1, 9100.0)]
    assert points[0].region == "GLOBAL"


def test_fred_corn_scraper_quarterly_average(monkeypatch):
    _set_fred_key(monkeypatch)
    fixture = _fixture([
        ("2025-04-01", "200.0"),
        ("2025-05-01", "210.0"),
        ("2025-06-01", "220.0"),
    ])
    monkeypatch.setattr(fred_module.httpx, "AsyncClient", _fake_client(fixture))
    points = asyncio.run(FREDCornScraper().fetch())
    assert [(p.year, p.quarter, p.value) for p in points] == [(2025, 2, 210.0)]


def test_fred_lng_jkm_scraper_quarterly_average(monkeypatch):
    _set_fred_key(monkeypatch)
    fixture = _fixture([
        ("2025-07-01", "18.0"),
        ("2025-08-01", "19.0"),
        ("2025-09-01", "20.0"),
    ])
    monkeypatch.setattr(fred_module.httpx, "AsyncClient", _fake_client(fixture))
    points = asyncio.run(FREDLNGJKMScraper().fetch())
    assert [(p.year, p.quarter, p.value) for p in points] == [(2025, 3, 19.0)]


def test_fred_scraper_skips_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(fred_module, "get_settings", lambda: SimpleNamespace(fred_api_key=""))
    points = asyncio.run(FREDCopperScraper().fetch())
    assert points == []


def test_new_scrapers_registered_under_short_codes():
    from app.services.scraper import SCRAPER_REGISTRY, SCRAPER_SOURCE_LABELS
    assert SCRAPER_REGISTRY["CU"] is FREDCopperScraper
    assert SCRAPER_REGISTRY["CORN"] is FREDCornScraper
    assert SCRAPER_REGISTRY["LNG-JKM"] is FREDLNGJKMScraper
    assert SCRAPER_SOURCE_LABELS["CU"] == "FRED"
    assert SCRAPER_SOURCE_LABELS["CORN"] == "FRED"
    assert SCRAPER_SOURCE_LABELS["LNG-JKM"] == "FRED"
