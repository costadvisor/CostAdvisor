"""A contract's term end cannot come before its term start (audit C-07).

Such a contract saved with a notice deadline a year in the past, so it landed
on the radar as an already-missed window and the form said "Contract created".
"""
from __future__ import annotations


def _create(c, tenant, **terms):
    return c.post(f"/api/contracts?team_id={tenant['team_id']}",
                  json={"reference": "TERM-1", "notice_days": 90, **terms})


def test_create_with_end_before_start_is_refused(client_as, tenant_a):
    r = _create(client_as(tenant_a), tenant_a, term_start="2026-12-31", term_end="2026-01-01")
    assert r.status_code == 422
    assert "before term start" in r.json()["detail"]


def test_update_checks_the_dates_it_leaves_behind(client_as, tenant_a):
    c = client_as(tenant_a)
    r = _create(c, tenant_a, term_start="2026-01-01", term_end="2026-12-31")
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    # Only one date sent: checked against the other one already stored.
    assert c.put(f"/api/contracts/{cid}", json={"term_end": "2025-06-30"}).status_code == 422
    assert c.put(f"/api/contracts/{cid}", json={"term_start": "2027-01-01"}).status_code == 422
    # Same-day and open-ended terms stay valid.
    assert c.put(f"/api/contracts/{cid}", json={"term_end": "2026-01-01"}).status_code == 200
    assert c.put(f"/api/contracts/{cid}", json={"term_end": None}).status_code == 200
