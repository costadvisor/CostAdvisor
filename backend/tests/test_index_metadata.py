"""Index metadata + proxy mapping (Scrum 57).

Covers the acceptance criteria:
- An index carries tier + frequency + role + retrieval_status + free-source (+ proxy).
- The 158 feeds reconcile to the region-agnostic (commodity, region) shape — region
  is stripped off the name, not stored on the index.
- The 2 blocked feeds (ilmenite, rutile) are marked, not dropped.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.constants.index_metadata import (
    ACCESS_TIERS, FREQUENCIES, ROLES, RETRIEVAL_STATUSES, PROXY_OPERATIONS,
    validate_proxy_logic,
)
from app.database import bypass_rls_var
from app.models.index_data import CommodityIndex
import seed_index_metadata as seed


# ── Vocabularies ──────────────────────────────────────────────────────────────

def test_vocabularies():
    # "Proxy" and the compound/unknown cadences were added in Scrum 74 (DB-5):
    # the 2026-07 drop states them, and the narrower tuples rejected otherwise
    # valid rows outright. Asserted as a superset of the original Scrum-57
    # vocabulary so a future broadening does not need this test rewritten,
    # while a REMOVAL — which would start rejecting live data again — still
    # fails here.
    assert {"Free", "Partial", "Subscription"} <= set(ACCESS_TIERS)
    assert "Proxy" in ACCESS_TIERS

    assert {"Daily", "Weekly", "Monthly", "Quarterly", "Annual", "Irregular"} <= set(FREQUENCIES)
    for cadence in ("Semi-annual", "Unknown", "Daily/Monthly"):
        assert cadence in FREQUENCIES

    assert set(ROLES) == {"feedstock", "energy", "fixed"}
    assert set(RETRIEVAL_STATUSES) == {"free", "good_proxy", "weak_proxy", "blocked"}
    assert "passthrough" in PROXY_OPERATIONS


def test_validate_proxy_logic():
    assert validate_proxy_logic(None) is None
    ok = {"base_index": "Brent", "operation": "spread", "spread": 120, "spread_unit": "abs",
          "recalibration": "Quarterly", "note": "tracks Brent + fixed spread"}
    assert validate_proxy_logic(ok) == ok
    with pytest.raises(ValueError):
        validate_proxy_logic({"bogus": 1})
    with pytest.raises(ValueError):
        validate_proxy_logic({"operation": "teleport"})
    with pytest.raises(ValueError):
        validate_proxy_logic({"recalibration": "Hourly"})
    with pytest.raises(ValueError):
        validate_proxy_logic({"spread": "lots"})


# ── Reconciliation pure functions ─────────────────────────────────────────────

def test_base_name_strips_region():
    assert seed.base_name("Iron scrap · China") == "Iron scrap"
    assert seed.base_name("Aluminium · Global") == "Aluminium"
    assert seed.base_name("Corn") == "Corn"  # no region suffix


def test_pick_representative_by_region_priority():
    feeds = [{"region": "CN"}, {"region": "Global"}, {"region": "NA"}]
    assert seed.pick_representative(feeds)["region"] == "Global"
    feeds2 = [{"region": "IN"}, {"region": "NA"}]
    assert seed.pick_representative(feeds2)["region"] == "NA"


def test_to_proxy_logic():
    assert seed.to_proxy_logic("free", "Public domain") is None          # free → no proxy
    good = seed.to_proxy_logic("good_proxy", "Processing spread ~$100/t")
    assert good["note"] == "Processing spread ~$100/t" and good["operation"] is None
    assert seed.to_proxy_logic("weak_proxy", "") is None                 # no prose → None
    assert seed.to_proxy_logic("blocked", "No free index")["note"] == "No free index"


# ── End-to-end reconciliation from the reference workbook ─────────────────────

def test_reference_reconciles_to_commodities_and_flags_blocked():
    if not seed.DEFAULT_XLSX.exists():
        pytest.skip("reference workbook not present")
    feeds = seed._read_feeds(seed.DEFAULT_XLSX)
    assert len(feeds) == 158  # the 158 feeds

    commodities = seed.build_commodities(feeds)
    assert len(commodities) == 59  # collapse to region-agnostic commodities

    # Blocked feeds are present (not dropped) and marked blocked.
    blocked = {n for n, m in commodities.items() if m["retrieval_status"] == "blocked"}
    assert any("ilmenite" in n.lower() for n in blocked)
    assert any("rutile" in n.lower() for n in blocked)

    # Every commodity carries the metadata + a valid vocabulary.
    for meta in commodities.values():
        assert meta["access_tier"] in ACCESS_TIERS
        assert meta["frequency"] in FREQUENCIES
        assert meta["role"] in ROLES
        assert meta["retrieval_status"] in RETRIEVAL_STATUSES
        validate_proxy_logic(meta["proxy_logic"])  # raises if malformed

    # No metadata dict carries a region field — region is not duplicated onto the index.
    assert all("region" not in meta for meta in commodities.values())


# ── Model round-trip (columns exist + proxy self-FK) ──────────────────────────

def test_commodity_index_metadata_roundtrip(db):
    base = CommodityIndex(name=f"__base_{uuid.uuid4().hex[:8]}", retrieval_status="free")
    db.add(base)
    db.flush()
    proxy = CommodityIndex(
        name=f"__proxy_{uuid.uuid4().hex[:8]}",
        access_tier="Subscription", role="feedstock", retrieval_status="good_proxy",
        free_source_name="WB Pink Sheet", free_source_url="https://example.org/pink",
        proxy_logic={"base_index": base.name, "operation": "spread", "spread": 80,
                     "spread_unit": "abs", "recalibration": "Quarterly", "note": "processing spread"},
        proxy_for_id=base.id,
    )
    db.add(proxy)
    db.commit()
    try:
        got = db.query(CommodityIndex).filter(CommodityIndex.id == proxy.id).first()
        assert got.access_tier == "Subscription"
        assert got.role == "feedstock"
        assert got.retrieval_status == "good_proxy"
        assert got.free_source_name == "WB Pink Sheet"
        assert got.proxy_logic["operation"] == "spread"
        assert got.proxy_for_id == base.id
        assert got.proxy_for.id == base.id  # self-FK relationship resolves
    finally:
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM commodity_indexes WHERE id IN (:a, :b)"), {"a": proxy.id, "b": base.id})
        db.commit()
