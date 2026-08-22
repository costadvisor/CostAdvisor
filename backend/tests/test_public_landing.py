"""Public landing-page data endpoint (Phase 1 — landing real-data wiring).

Covers GET /api/indexes/public-quarterly: public (no auth), platform-level commodity
index series only, oldest-first points + QoQ delta, and no tenant/override fields leaked.
Mirrors the FX public-daily pattern.
"""
from __future__ import annotations

from app.database import SessionLocal, bypass_rls_var
from app.models.index_data import CommodityIndex, IndexValue


def _seed_commodity(name, region, points, unit="$/mt", category="Chemical"):
    """Insert a CommodityIndex + IndexValue rows (RLS bypassed); return cleanup fn.
    `points` = [(year, quarter, value), ...]."""
    bypass_rls_var.set(True)
    s = SessionLocal()
    ci = CommodityIndex(name=name, unit=unit, currency="USD", category=category,
                        provider="test", frequency="Quarterly", scrape_enabled=False)
    s.add(ci)
    s.flush()
    for y, q, v in points:
        s.add(IndexValue(commodity_id=ci.id, region=region, year=y, quarter=q, value=v, source="scraped"))
    s.commit()
    cid = ci.id

    def cleanup():
        bypass_rls_var.set(True)
        s.query(IndexValue).filter(IndexValue.commodity_id == cid).delete()
        obj = s.query(CommodityIndex).filter(CommodityIndex.id == cid).first()
        if obj:
            s.delete(obj)
        s.commit()
        s.close()
    return cleanup


def test_public_quarterly_no_auth_oldest_first(client):
    name = "ZZ Test Acid"
    cleanup = _seed_commodity(name, "Europe", [(2025, 3, 100.0), (2025, 4, 110.0), (2026, 1, 121.0)])
    try:
        # No auth cookie — public endpoint must still serve
        r = client.get("/api/indexes/public-quarterly", params={"commodities": name})
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data) == 1
        item = data[0]
        assert item["commodity_name"] == name
        assert item["region"] == "Europe"
        # oldest-first series
        assert [(p["year"], p["quarter"]) for p in item["points"]] == [(2025, 3), (2025, 4), (2026, 1)]
        assert item["latest"] == 121.0
        assert item["prev"] == 110.0
        # QoQ = (121-110)/110*100 = 10%
        assert round(item["qoq_pct"], 2) == 10.0
    finally:
        cleanup()


def test_public_quarterly_no_tenant_leak(client):
    name = "ZZ Test Base"
    cleanup = _seed_commodity(name, "GLOBAL", [(2025, 4, 50.0), (2026, 1, 55.0)])
    try:
        r = client.get("/api/indexes/public-quarterly", params={"commodities": name})
        assert r.status_code == 200, r.text
        item = r.json()[0]
        # No tenant / override / internal fields exposed
        for forbidden in ("team_id", "override_id", "override_by", "commodity_id", "source"):
            assert forbidden not in item, f"leaked field: {forbidden}"
    finally:
        cleanup()


def test_public_quarterly_default_headline_set(client):
    # No params → curated headline set; must return 200 and a list (content depends on seeded data).
    r = client.get("/api/indexes/public-quarterly")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_public_quarterly_unknown_commodity_skipped(client):
    r = client.get("/api/indexes/public-quarterly", params={"commodities": "Nonexistent XYZ Commodity"})
    assert r.status_code == 200, r.text
    assert r.json() == []
