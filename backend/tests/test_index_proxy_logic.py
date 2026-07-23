"""Admin proxy-index editor (Scrum 67).

PUT /api/indexes/{id}/proxy-logic — super-admin edits a commodity index's structured
proxy_logic (validated), audit-logged. Non-super-admin → 403; bad spec → 422; missing → 404.
"""
from __future__ import annotations

from app.database import SessionLocal, bypass_rls_var
from app.models.index_data import CommodityIndex


def _seed_index(name="ZZ Proxy Test", retrieval_status="blocked"):
    bypass_rls_var.set(True)
    s = SessionLocal()
    ci = CommodityIndex(name=name, unit="$/mt", currency="USD", category="Chemical",
                        provider="test", frequency="Quarterly", scrape_enabled=False,
                        retrieval_status=retrieval_status)
    s.add(ci)
    s.commit()
    cid = ci.id

    def cleanup():
        bypass_rls_var.set(True)
        obj = s.query(CommodityIndex).filter(CommodityIndex.id == cid).first()
        if obj:
            s.delete(obj)
        s.commit()
        s.close()
    return cid, cleanup


VALID_SPEC = {
    "base_index": "Brent Crude", "operation": "ratio", "spread": 12.5,
    "spread_unit": "pct", "recalibration": "Quarterly", "note": "estimated from crude",
}


def test_super_admin_sets_proxy_logic(client_as, user_factory):
    su = user_factory(is_super_admin=True)
    cid, cleanup = _seed_index()
    try:
        r = client_as(su).put(f"/api/indexes/{cid}/proxy-logic",
                              json={"proxy_logic": VALID_SPEC, "retrieval_status": "good_proxy"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["proxy_logic"]["operation"] == "ratio"
        assert body["retrieval_status"] == "good_proxy"
        # persisted
        bypass_rls_var.set(True)
        s = SessionLocal()
        ci = s.query(CommodityIndex).filter(CommodityIndex.id == cid).first()
        assert ci.proxy_logic["spread"] == 12.5
        assert ci.retrieval_status == "good_proxy"
        s.close()
    finally:
        cleanup()


def test_bad_spec_422(client_as, user_factory):
    su = user_factory(is_super_admin=True)
    cid, cleanup = _seed_index()
    try:
        r = client_as(su).put(f"/api/indexes/{cid}/proxy-logic",
                              json={"proxy_logic": {"operation": "nonsense"}})
        assert r.status_code == 422, r.text
    finally:
        cleanup()


def test_non_super_admin_403(client_as, user_factory):
    u = user_factory()  # not super admin
    cid, cleanup = _seed_index()
    try:
        r = client_as(u).put(f"/api/indexes/{cid}/proxy-logic", json={"proxy_logic": VALID_SPEC})
        assert r.status_code == 403, r.text
    finally:
        cleanup()


def test_unknown_index_404(client_as, user_factory):
    su = user_factory(is_super_admin=True)
    r = client_as(su).put("/api/indexes/999999999/proxy-logic", json={"proxy_logic": None})
    assert r.status_code == 404, r.text
