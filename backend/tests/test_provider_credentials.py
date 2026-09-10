"""Scrum 26 — team-supplied provider credentials.

Covers: owner/admin-only gating, audit logging that never carries the
plaintext secret, list/get responses that never leak it either, graceful
degradation (missing/unsupported credential never touches existing override
data), resolver provenance ("provider" wins over "scraped"), and the
FastmarketsAdapter's own auth-failure classification against a mocked
httpx.AsyncClient (no HTTP-mock library in this repo — same module-level
monkeypatch convention as test_auth_events.py).
"""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from app.models.index_data import (
    CommodityIndex, IndexOverride, IndexValue, TeamIndexSource, TeamProviderCredential,
)
from app.models.team import TeamMembership
from app.models.audit_log import AuditLog
from app.services.data_resolver import resolve_index_values, get_single_index_value_detailed
from app.services.providers import fastmarkets as fastmarkets_module
from app.services.providers.base import ProviderCredentialError

REGION = "Europe"


def _add_member(db, team_id, user_id, role="member"):
    db.add(TeamMembership(user_id=user_id, team_id=team_id, role=role))
    db.commit()


def _cleanup_credential(db, team_id, provider):
    db.query(TeamProviderCredential).filter(
        TeamProviderCredential.team_id == team_id, TeamProviderCredential.provider == provider
    ).delete()
    db.commit()


# ── Gating ────────────────────────────────────────────────────────────────

def test_create_credential_requires_owner_or_admin(client_as, tenant_a, tenant_b, db):
    _add_member(db, tenant_a["team_id"], tenant_b["user_id"], role="member")
    try:
        r = client_as(tenant_b).post("/api/indexes/provider-credentials", json={
            "team_id": str(tenant_a["team_id"]), "provider": "fastmarkets", "credential": {"api_key": "secret123"},
        })
        assert r.status_code == 403
    finally:
        db.query(TeamMembership).filter(
            TeamMembership.team_id == tenant_a["team_id"], TeamMembership.user_id == tenant_b["user_id"]
        ).delete()
        db.commit()


def test_create_credential_owner_ok(client_as, tenant_a, db):
    try:
        r = client_as(tenant_a).post("/api/indexes/provider-credentials", json={
            "team_id": str(tenant_a["team_id"]), "provider": "fastmarkets", "credential": {"api_key": "secret123"},
        })
        assert r.status_code == 200, r.text
        assert r.json()["provider"] == "fastmarkets"
        assert r.json()["status"] == "unverified"
    finally:
        _cleanup_credential(db, tenant_a["team_id"], "fastmarkets")


# ── Audit logging + no plaintext leakage ───────────────────────────────────

def test_create_audit_logged_never_leaks_secret(client_as, tenant_a, db):
    secret = "super-secret-key-xyz"
    try:
        r = client_as(tenant_a).post("/api/indexes/provider-credentials", json={
            "team_id": str(tenant_a["team_id"]), "provider": "fastmarkets", "credential": {"api_key": secret},
        })
        assert r.status_code == 200, r.text
        credential_id = r.json()["id"]

        db.expire_all()
        ev = db.query(AuditLog).filter(
            AuditLog.entity_type == "team_provider_credential", AuditLog.entity_id == str(credential_id)
        ).order_by(AuditLog.id.desc()).first()
        assert ev is not None
        assert ev.event_type == "create"
        assert secret not in json.dumps(ev.new_value or {})
        assert secret not in json.dumps(ev.previous_value or {})
    finally:
        _cleanup_credential(db, tenant_a["team_id"], "fastmarkets")


def test_rotate_updates_same_row_and_audits_rotate(client_as, tenant_a, db):
    try:
        c = client_as(tenant_a)
        first = c.post("/api/indexes/provider-credentials", json={
            "team_id": str(tenant_a["team_id"]), "provider": "fastmarkets", "credential": {"api_key": "key-one"},
        }).json()
        second = c.post("/api/indexes/provider-credentials", json={
            "team_id": str(tenant_a["team_id"]), "provider": "fastmarkets", "credential": {"api_key": "key-two"},
        }).json()
        assert second["id"] == first["id"]

        db.expire_all()
        ev = db.query(AuditLog).filter(
            AuditLog.entity_type == "team_provider_credential", AuditLog.entity_id == str(first["id"]),
            AuditLog.event_type == "rotate",
        ).first()
        assert ev is not None
        assert ev.new_value.get("rotated") is True
    finally:
        _cleanup_credential(db, tenant_a["team_id"], "fastmarkets")


def test_list_and_get_responses_never_include_secret(client_as, tenant_a, db):
    secret = "should-never-appear-anywhere"
    try:
        c = client_as(tenant_a)
        c.post("/api/indexes/provider-credentials", json={
            "team_id": str(tenant_a["team_id"]), "provider": "fastmarkets", "credential": {"api_key": secret},
        })
        r = c.get("/api/indexes/provider-credentials", params={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 200
        assert secret not in r.text
        for row in r.json():
            assert "credential" not in row
            assert "credential_encrypted" not in row
    finally:
        _cleanup_credential(db, tenant_a["team_id"], "fastmarkets")


def test_delete_audited(client_as, tenant_a, db):
    c = client_as(tenant_a)
    credential_id = c.post("/api/indexes/provider-credentials", json={
        "team_id": str(tenant_a["team_id"]), "provider": "fastmarkets", "credential": {"api_key": "x"},
    }).json()["id"]

    r = c.delete(f"/api/indexes/provider-credentials/{credential_id}")
    assert r.status_code == 200

    db.expire_all()
    ev = db.query(AuditLog).filter(
        AuditLog.entity_type == "team_provider_credential", AuditLog.entity_id == str(credential_id),
        AuditLog.event_type == "delete",
    ).first()
    assert ev is not None


# ── Providers listing ───────────────────────────────────────────────────────

def test_list_providers_shows_availability(client_as, tenant_a):
    r = client_as(tenant_a).get("/api/indexes/provider-credentials/providers")
    assert r.status_code == 200
    by_key = {p["key"]: p for p in r.json()}
    assert by_key["fastmarkets"]["adapter_available"] is True
    assert by_key["argus"]["adapter_available"] is False


# ── Resolution + graceful degradation ──────────────────────────────────────

@pytest.fixture
def provider_source(tenant_a, db):
    """A provider_credential-type TeamIndexSource for a fresh commodity, plus
    a pre-existing scraped IndexValue for the same period (to prove the
    provider override wins over it once fetched)."""
    suffix = uuid.uuid4().hex[:8]
    commodity = CommodityIndex(name=f"pc-test-{suffix}", currency="USD", unit="t")
    db.add(commodity)
    db.flush()
    db.add(IndexValue(commodity_id=commodity.id, region=REGION, year=2025, quarter=1, value=50))
    db.commit()

    source = TeamIndexSource(
        team_id=tenant_a["team_id"], commodity_id=commodity.id, region=REGION,
        source_type="provider_credential",
        scrape_config={"provider": "fastmarkets", "series_id": "MB-TEST-001"},
        created_by=tenant_a["user_id"],
    )
    db.add(source)
    db.commit()

    yield source, commodity

    db.query(IndexOverride).filter(IndexOverride.commodity_id == commodity.id).delete(synchronize_session=False)
    db.query(TeamIndexSource).filter(TeamIndexSource.id == source.id).delete(synchronize_session=False)
    db.query(IndexValue).filter(IndexValue.commodity_id == commodity.id).delete(synchronize_session=False)
    db.query(CommodityIndex).filter(CommodityIndex.id == commodity.id).delete(synchronize_session=False)
    db.commit()


def test_missing_credential_returns_clean_error_not_500(client_as, tenant_a, provider_source, db):
    source, _ = provider_source
    r = client_as(tenant_a).post(f"/api/indexes/sources/{source.id}/scrape-now")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert "credential" in body["error"].lower()


def test_unregistered_provider_gives_clear_not_supported_error(client_as, tenant_a, db):
    suffix = uuid.uuid4().hex[:8]
    commodity = CommodityIndex(name=f"pc-argus-{suffix}", currency="USD", unit="t")
    db.add(commodity)
    db.flush()
    source = TeamIndexSource(
        team_id=tenant_a["team_id"], commodity_id=commodity.id, region=REGION,
        source_type="provider_credential",
        scrape_config={"provider": "argus", "series_id": "X"},
        created_by=tenant_a["user_id"],
    )
    db.add(source)
    client_as(tenant_a).post("/api/indexes/provider-credentials", json={
        "team_id": str(tenant_a["team_id"]), "provider": "argus", "credential": {"api_key": "x"},
    })
    db.commit()
    try:
        r = client_as(tenant_a).post(f"/api/indexes/sources/{source.id}/scrape-now")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "error"
        assert "not yet supported" in body["error"].lower()
    finally:
        db.query(TeamIndexSource).filter(TeamIndexSource.id == source.id).delete(synchronize_session=False)
        db.query(CommodityIndex).filter(CommodityIndex.id == commodity.id).delete(synchronize_session=False)
        _cleanup_credential(db, tenant_a["team_id"], "argus")
        db.commit()


def test_provider_source_resolves_and_wins_over_scraped(client_as, tenant_a, provider_source, db, monkeypatch):
    source, commodity = provider_source

    async def fake_fetch_series(self, credential, series_id, region):
        from app.services.providers.base import ProviderPoint
        return [ProviderPoint(region=region, year=2025, quarter=1, value=999.0)]

    monkeypatch.setattr(fastmarkets_module.FastmarketsAdapter, "fetch_series", fake_fetch_series)

    client_as(tenant_a).post("/api/indexes/provider-credentials", json={
        "team_id": str(tenant_a["team_id"]), "provider": "fastmarkets", "credential": {"api_key": "x"},
    })
    try:
        r = client_as(tenant_a).post(f"/api/indexes/sources/{source.id}/scrape-now")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ok"
        assert r.json()["value"] == pytest.approx(999.0)

        db.expire_all()
        rows = resolve_index_values(db, tenant_a["team_id"], region=REGION, commodity_ids={commodity.id},
                                     year=2025, quarter=1)
        assert len(rows) == 1
        assert rows[0].value == pytest.approx(999.0)
        assert rows[0].source == "provider"

        val, src = get_single_index_value_detailed(db, tenant_a["team_id"], commodity.id, REGION, 2025, 1)
        assert val == pytest.approx(999.0)
        assert src == "provider"
    finally:
        _cleanup_credential(db, tenant_a["team_id"], "fastmarkets")


def test_delete_credential_source_degrades_without_touching_overrides(client_as, tenant_a, provider_source, db, monkeypatch):
    source, commodity = provider_source

    async def fake_fetch_series(self, credential, series_id, region):
        from app.services.providers.base import ProviderPoint
        return [ProviderPoint(region=region, year=2025, quarter=1, value=777.0)]

    monkeypatch.setattr(fastmarkets_module.FastmarketsAdapter, "fetch_series", fake_fetch_series)

    c = client_as(tenant_a)
    credential_id = c.post("/api/indexes/provider-credentials", json={
        "team_id": str(tenant_a["team_id"]), "provider": "fastmarkets", "credential": {"api_key": "x"},
    }).json()["id"]
    ok = c.post(f"/api/indexes/sources/{source.id}/scrape-now")
    assert ok.json()["status"] == "ok"

    before = db.query(IndexOverride).filter(IndexOverride.commodity_id == commodity.id).all()
    assert len(before) >= 1

    c.delete(f"/api/indexes/provider-credentials/{credential_id}")

    r = c.post(f"/api/indexes/sources/{source.id}/scrape-now")
    assert r.status_code == 200
    assert r.json()["status"] == "error"

    db.expire_all()
    after = db.query(IndexOverride).filter(IndexOverride.commodity_id == commodity.id).all()
    assert {(o.year, o.quarter, float(o.value)) for o in before} == {(o.year, o.quarter, float(o.value)) for o in after}


# ── FastmarketsAdapter unit tests (mocked httpx.AsyncClient) ───────────────

class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def _fake_client(response: _FakeResponse):
    class FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return response

    return FakeAsyncClient


def test_fastmarkets_adapter_success_fixture(monkeypatch):
    fixture = {"series": [
        {"period": "2025-01", "value": 100.5},
        {"period": "2025-04", "value": 103.25},
    ]}
    monkeypatch.setattr(fastmarkets_module.httpx, "AsyncClient", _fake_client(_FakeResponse(200, fixture)))

    adapter = fastmarkets_module.FastmarketsAdapter()
    points = asyncio.run(adapter.fetch_series({"api_key": "x"}, "MB-TEST", "Europe"))
    assert [(p.year, p.quarter, p.value) for p in points] == [(2025, 1, 100.5), (2025, 2, 103.25)]


def test_fastmarkets_adapter_401_maps_to_rejected(monkeypatch):
    monkeypatch.setattr(fastmarkets_module.httpx, "AsyncClient", _fake_client(_FakeResponse(401, {"error": "invalid_key"})))
    adapter = fastmarkets_module.FastmarketsAdapter()
    with pytest.raises(ProviderCredentialError) as exc_info:
        asyncio.run(adapter.fetch_series({"api_key": "x"}, "MB-TEST", "Europe"))
    assert exc_info.value.reason == "rejected"


def test_fastmarkets_adapter_expired_token_maps_to_expired(monkeypatch):
    monkeypatch.setattr(fastmarkets_module.httpx, "AsyncClient", _fake_client(_FakeResponse(401, {"error": "token_expired"})))
    adapter = fastmarkets_module.FastmarketsAdapter()
    with pytest.raises(ProviderCredentialError) as exc_info:
        asyncio.run(adapter.fetch_series({"api_key": "x"}, "MB-TEST", "Europe"))
    assert exc_info.value.reason == "expired"


def test_fastmarkets_adapter_missing_api_key_rejected():
    adapter = fastmarkets_module.FastmarketsAdapter()
    with pytest.raises(ProviderCredentialError) as exc_info:
        asyncio.run(adapter.fetch_series({}, "MB-TEST", "Europe"))
    assert exc_info.value.reason == "rejected"
