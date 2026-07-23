"""Region as a first-class entity (Scrum 56).

Covers the acceptance criteria:
- Region is a row, not a string, everywhere: the region columns are FKs to
  regions.code (a raw insert of an unknown region is rejected at the DB).
- Existing data maps with no orphaned strings (every region value has a row).
- A subregion can be added as a child with no migration (admin CRUD + parent_id).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal, bypass_rls_var
from app.models.region import Region
from app.models.index_data import CommodityIndex, IndexValue


def _temp_commodity(db) -> CommodityIndex:
    c = CommodityIndex(name=f"__test_c_{uuid.uuid4().hex[:8]}")
    db.add(c)
    db.commit()
    return c


# ── Seed + hierarchy ──────────────────────────────────────────────────────────

def test_regions_seeded_with_global_and_subregions(db):
    codes = {r.code for r in db.query(Region).all()}
    for expected in ("GLOBAL", "Europe", "NA", "Latam", "Asia", "ME", "Africa", "Oceania"):
        assert expected in codes, f"missing seeded region {expected}"
    # NWE is a subregion (child) of Europe.
    nwe = db.query(Region).filter(Region.code == "NWE").first()
    europe = db.query(Region).filter(Region.code == "Europe").first()
    assert nwe is not None and nwe.parent_id == europe.id


def test_no_orphaned_region_strings(db):
    """Every region value in the 5 tables resolves to a regions row."""
    orphans = db.execute(text("""
        SELECT DISTINCT r FROM (
            SELECT region AS r FROM index_values
            UNION SELECT region FROM index_overrides
            UNION SELECT region FROM team_index_sources
            UNION SELECT region FROM cost_models
            UNION SELECT destination_region FROM cost_models WHERE destination_region IS NOT NULL
            UNION SELECT origin_region FROM freight_lanes
            UNION SELECT destination_region FROM freight_lanes
        ) x
        WHERE r IS NOT NULL AND r <> '' AND r NOT IN (SELECT code FROM regions)
    """)).fetchall()
    assert orphans == [], f"orphaned region strings: {[o[0] for o in orphans]}"


# ── FK enforcement + auto-register safety net ─────────────────────────────────

def test_fk_rejects_unknown_region_at_db(db):
    """A raw insert (bypassing the ORM listener) with an unknown region is rejected."""
    c = _temp_commodity(db)
    try:
        with pytest.raises(IntegrityError):
            db.execute(text(
                "INSERT INTO index_values (commodity_id, region, year, quarter, value, source) "
                "VALUES (:c, '__NOPE__', 2099, 1, 1.0, 'test')"
            ), {"c": c.id})
            db.flush()
        db.rollback()
    finally:
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :c"), {"c": c.id})
        db.commit()


def test_orm_write_auto_registers_new_region(db):
    """Writing an IndexValue with a brand-new region through the ORM auto-creates
    the Region (before_flush listener), so the FK never 500s a legit write."""
    new_code = f"RGN_{uuid.uuid4().hex[:6]}"
    c = _temp_commodity(db)
    try:
        db.add(IndexValue(commodity_id=c.id, region=new_code, year=2099, quarter=1, value=1.0, source="test"))
        db.commit()  # would fail on the FK if the listener hadn't inserted the region
        assert db.query(Region).filter(Region.code == new_code).first() is not None
    finally:
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM index_values WHERE commodity_id = :c"), {"c": c.id})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :c"), {"c": c.id})
        db.execute(text("DELETE FROM regions WHERE code = :r"), {"r": new_code})
        db.commit()


def test_orm_write_canonicalizes_case_variant(db):
    """A case-variant of an existing code ('EUROPE') is rewritten onto the
    canonical row instead of minting a near-duplicate region."""
    c = _temp_commodity(db)
    try:
        iv = IndexValue(commodity_id=c.id, region="EUROPE", year=2099, quarter=1, value=1.0, source="test")
        db.add(iv)
        db.commit()
        stored = db.execute(text(
            "SELECT region FROM index_values WHERE commodity_id = :c"), {"c": c.id}).scalar()
        assert stored == "Europe"
        assert db.query(Region).filter(Region.code == "EUROPE").first() is None
    finally:
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM index_values WHERE commodity_id = :c"), {"c": c.id})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :c"), {"c": c.id})
        db.commit()


def test_orm_write_rewrites_known_alias(db):
    """The typo spellings the backfill once absorbed (EU, BLOBAL, …) are
    rewritten to canonical instead of re-registering as regions."""
    c = _temp_commodity(db)
    try:
        db.add(IndexValue(commodity_id=c.id, region="BLOBAL", year=2099, quarter=1, value=1.0, source="test"))
        db.add(IndexValue(commodity_id=c.id, region="EU", year=2099, quarter=2, value=1.0, source="test"))
        db.commit()
        stored = {r[0] for r in db.execute(text(
            "SELECT region FROM index_values WHERE commodity_id = :c"), {"c": c.id}).fetchall()}
        assert stored == {"GLOBAL", "Europe"}
        assert db.query(Region).filter(Region.code.in_(["BLOBAL", "EU"])).count() == 0
    finally:
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM index_values WHERE commodity_id = :c"), {"c": c.id})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :c"), {"c": c.id})
        db.commit()


def test_no_typo_regions_remain(db):
    """The rgc2b3c4d5e6 merge removed the backfilled typo rows; the closed
    vocabulary must stay closed."""
    typos = ["EU", "eu", "ASIA", "INDIA", "BLOBAL", "GLOBSL"]
    assert db.query(Region).filter(Region.code.in_(typos)).count() == 0


# ── Admin CRUD ────────────────────────────────────────────────────────────────

def test_create_subregion_as_child_no_migration(client_as, user_factory, db):
    admin = user_factory(is_super_admin=True)
    europe = db.query(Region).filter(Region.code == "Europe").first()
    code = f"SUB_{uuid.uuid4().hex[:6]}"
    try:
        r = client_as(admin).post("/api/regions/", json={"code": code, "name": "Test Sub", "parent_id": europe.id})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["parent_id"] == europe.id
        # Visible in the list.
        listed = {x["code"]: x for x in client_as(admin).get("/api/regions/").json()}
        assert code in listed and listed[code]["parent_id"] == europe.id
    finally:
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM regions WHERE code = :c"), {"c": code})
        db.commit()


def test_region_writes_require_super_admin(client_as, tenant_a):
    c = client_as(tenant_a)
    assert c.get("/api/regions/").status_code == 200            # read is open
    assert c.post("/api/regions/", json={"code": "X_DENY", "name": "x"}).status_code == 403


def test_delete_unused_region_ok(client_as, user_factory, db):
    admin = user_factory(is_super_admin=True)
    code = f"DEL_{uuid.uuid4().hex[:6]}"
    created = client_as(admin).post("/api/regions/", json={"code": code, "name": "Delete me"})
    assert created.status_code == 201
    rid = created.json()["id"]
    resp = client_as(admin).delete(f"/api/regions/{rid}")
    assert resp.status_code == 200
    bypass_rls_var.set(True)
    assert db.query(Region).filter(Region.id == rid).first() is None


def test_delete_region_in_use_conflicts(client_as, user_factory, db):
    admin = user_factory(is_super_admin=True)
    code = f"USE_{uuid.uuid4().hex[:6]}"
    region = Region(code=code, name="In use")
    db.add(region)
    db.flush()
    c = _temp_commodity(db)
    db.add(IndexValue(commodity_id=c.id, region=code, year=2099, quarter=2, value=2.0, source="test"))
    db.commit()
    rid = region.id
    try:
        resp = client_as(admin).delete(f"/api/regions/{rid}")
        assert resp.status_code == 409, resp.text
    finally:
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM index_values WHERE commodity_id = :c"), {"c": c.id})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :c"), {"c": c.id})
        db.execute(text("DELETE FROM regions WHERE id = :r"), {"r": rid})
        db.commit()
