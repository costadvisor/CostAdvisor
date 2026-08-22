"""Taxonomy spine (DB-1): subfamily tier + platform/team forking.

Covers the done-when criteria:
- A team can fork a platform family/subfamily; the fork keeps origin_id so platform
  resolution survives a rename.
- RLS keeps one team from reading another team's taxonomy (platform rows readable
  by all).
- Every product still maps to a family; subfamily is optional.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import SessionLocal, bypass_rls_var, current_user_id_var
from app.models.chemical_family import ChemicalFamily
from app.models.subfamily import Subfamily


def _as_user(user_id):
    """Fresh RLS-scoped session acting as the given user (policies on)."""
    s = SessionLocal()
    bypass_rls_var.set(False)
    current_user_id_var.set(str(user_id))
    return s


def _cleanup_platform(db, family_ids: list[int]):
    """Platform rows (team_id IS NULL) aren't covered by the team CASCADE — remove
    them explicitly. Subfamilies cascade off the family FK."""
    bypass_rls_var.set(True)
    for fid in family_ids:
        db.execute(text("DELETE FROM chemical_families WHERE id = :id"), {"id": fid})
    db.commit()


# ── DB-level RLS ──────────────────────────────────────────────────────────────

def test_family_platform_visible_and_team_isolated(tenant_a, tenant_b, db):
    plat = ChemicalFamily(name=f"PLAT-{uuid.uuid4().hex[:6]}", code="F01")
    db.add(plat)
    db.flush()
    a_fork = ChemicalFamily(name="A-fork", team_id=tenant_a["team_id"], origin_id=plat.id)
    b_fork = ChemicalFamily(name="B-fork", team_id=tenant_b["team_id"], origin_id=plat.id)
    db.add_all([a_fork, b_fork])
    db.commit()

    s = _as_user(tenant_a["user_id"])
    try:
        names = {f.name for f in s.query(ChemicalFamily).all()}
        assert plat.name in names       # platform visible to all
        assert "A-fork" in names        # own team fork visible
        assert "B-fork" not in names    # other team fork isolated
    finally:
        s.close()
        _cleanup_platform(db, [plat.id])


def test_subfamily_platform_visible_and_team_isolated(tenant_a, tenant_b, db):
    plat_fam = ChemicalFamily(name=f"PLAT-{uuid.uuid4().hex[:6]}")
    db.add(plat_fam)
    db.flush()
    plat_sub = Subfamily(family_id=plat_fam.id, name="plat-sub", code="S01")
    a_sub = Subfamily(family_id=plat_fam.id, name="a-sub", team_id=tenant_a["team_id"])
    b_sub = Subfamily(family_id=plat_fam.id, name="b-sub", team_id=tenant_b["team_id"])
    db.add_all([plat_sub, a_sub, b_sub])
    db.commit()

    s = _as_user(tenant_a["user_id"])
    try:
        names = {x.name for x in s.query(Subfamily).all()}
        assert "plat-sub" in names
        assert "a-sub" in names
        assert "b-sub" not in names
    finally:
        s.close()
        _cleanup_platform(db, [plat_fam.id])  # cascades subfamilies


# ── Fork endpoint ─────────────────────────────────────────────────────────────

def test_fork_family_creates_team_copy_and_survives_rename(client_as, tenant_a, db):
    plat = ChemicalFamily(name=f"Surfactants-{uuid.uuid4().hex[:6]}", code="F07")
    db.add(plat)
    db.commit()
    try:
        c = client_as(tenant_a)
        r = c.post(f"/api/chemical-families/{plat.id}/fork",
                   json={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["team_id"] == str(tenant_a["team_id"])
        assert body["origin_id"] == plat.id
        assert body["name"] == plat.name
        assert body["code"] == "F07"
        fork_id = body["id"]

        # Rename the fork — origin_id must still resolve to the (unchanged) platform row.
        bypass_rls_var.set(True)
        fork = db.query(ChemicalFamily).filter(ChemicalFamily.id == fork_id).first()
        fork.name = "Cleaning Agents (Acme)"
        db.commit()
        assert fork.origin_id == plat.id
        origin = db.query(ChemicalFamily).filter(ChemicalFamily.id == fork.origin_id).first()
        assert origin is not None and origin.name == plat.name  # platform resolution intact
    finally:
        _cleanup_platform(db, [plat.id])


def test_cannot_fork_a_team_row(client_as, tenant_a, db):
    plat = ChemicalFamily(name=f"PLAT-{uuid.uuid4().hex[:6]}")
    db.add(plat)
    db.flush()
    team_row = ChemicalFamily(name="already-team", team_id=tenant_a["team_id"], origin_id=plat.id)
    db.add(team_row)
    db.commit()
    try:
        c = client_as(tenant_a)
        r = c.post(f"/api/chemical-families/{team_row.id}/fork",
                   json={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 400
    finally:
        # Deleting the platform row just NULLs the team row's origin_id (SET NULL),
        # so order is safe; the team row itself is removed here.
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM chemical_families WHERE team_id = :t"), {"t": str(tenant_a["team_id"])})
        db.commit()
        _cleanup_platform(db, [plat.id])


def test_duplicate_fork_conflicts(client_as, tenant_a, db):
    plat = ChemicalFamily(name=f"PLAT-{uuid.uuid4().hex[:6]}")
    db.add(plat)
    db.commit()
    try:
        c = client_as(tenant_a)
        first = c.post(f"/api/chemical-families/{plat.id}/fork",
                       json={"team_id": str(tenant_a["team_id"])})
        assert first.status_code == 201
        second = c.post(f"/api/chemical-families/{plat.id}/fork",
                        json={"team_id": str(tenant_a["team_id"])})
        assert second.status_code == 409
    finally:
        _cleanup_platform(db, [plat.id])


def test_fork_into_foreign_team_forbidden(client_as, tenant_a, tenant_b, db):
    plat = ChemicalFamily(name=f"PLAT-{uuid.uuid4().hex[:6]}")
    db.add(plat)
    db.commit()
    try:
        # tenant_b is not a member of tenant_a's team → 403
        c = client_as(tenant_b)
        r = c.post(f"/api/chemical-families/{plat.id}/fork",
                   json={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 403
    finally:
        _cleanup_platform(db, [plat.id])


# ── Edit endpoint (rename/re-code a fork) ─────────────────────────────────────

def test_team_can_edit_own_family_fork(client_as, tenant_a, db):
    plat = ChemicalFamily(name=f"PLAT-{uuid.uuid4().hex[:6]}", code="F20")
    db.add(plat)
    db.commit()
    try:
        c = client_as(tenant_a)
        fork = c.post(f"/api/chemical-families/{plat.id}/fork",
                      json={"team_id": str(tenant_a["team_id"])}).json()
        r = c.put(f"/api/chemical-families/{fork['id']}", json={"name": "Renamed by team", "code": "F20-A"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "Renamed by team"
        assert body["code"] == "F20-A"
        assert body["origin_id"] == plat.id   # rename doesn't break the origin link

        # The platform original is untouched.
        bypass_rls_var.set(True)
        origin = db.query(ChemicalFamily).filter(ChemicalFamily.id == plat.id).first()
        assert origin.name != "Renamed by team"
    finally:
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM chemical_families WHERE team_id = :t"), {"t": str(tenant_a["team_id"])})
        db.commit()
        _cleanup_platform(db, [plat.id])


def test_team_cannot_edit_platform_family(client_as, tenant_a, db):
    plat = ChemicalFamily(name=f"PLAT-{uuid.uuid4().hex[:6]}")
    db.add(plat)
    db.commit()
    try:
        c = client_as(tenant_a)
        r = c.put(f"/api/chemical-families/{plat.id}", json={"name": "Hijacked"})
        assert r.status_code == 403
    finally:
        _cleanup_platform(db, [plat.id])


def test_team_cannot_edit_another_teams_fork(client_as, tenant_a, tenant_b, db):
    plat = ChemicalFamily(name=f"PLAT-{uuid.uuid4().hex[:6]}")
    db.add(plat)
    db.flush()
    a_fork = ChemicalFamily(name="A-fork", team_id=tenant_a["team_id"], origin_id=plat.id)
    db.add(a_fork)
    db.commit()
    try:
        c = client_as(tenant_b)
        r = c.put(f"/api/chemical-families/{a_fork.id}", json={"name": "Hijacked"})
        assert r.status_code == 403
    finally:
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM chemical_families WHERE team_id = :t"), {"t": str(tenant_a["team_id"])})
        db.commit()
        _cleanup_platform(db, [plat.id])


def test_team_can_edit_own_subfamily_fork(client_as, tenant_a, db):
    fam = ChemicalFamily(name=f"PLAT-{uuid.uuid4().hex[:6]}")
    db.add(fam)
    db.flush()
    sub = Subfamily(family_id=fam.id, name="plat-sub", code="S20")
    db.add(sub)
    db.commit()
    try:
        c = client_as(tenant_a)
        fork = c.post(f"/api/subfamilies/{sub.id}/fork",
                      json={"team_id": str(tenant_a["team_id"])}).json()
        r = c.put(f"/api/subfamilies/{fork['id']}", json={"name": "Renamed sub"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "Renamed sub"
        assert body["origin_id"] == sub.id
        assert body["code"] == "S20"   # untouched field survives a partial update
    finally:
        _cleanup_platform(db, [fam.id])


def test_fork_subfamily_creates_team_copy(client_as, tenant_a, db):
    fam = ChemicalFamily(name=f"PLAT-{uuid.uuid4().hex[:6]}")
    db.add(fam)
    db.flush()
    sub = Subfamily(family_id=fam.id, name="plat-sub", code="S09")
    db.add(sub)
    db.commit()
    try:
        c = client_as(tenant_a)
        r = c.post(f"/api/subfamilies/{sub.id}/fork",
                   json={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["team_id"] == str(tenant_a["team_id"])
        assert body["origin_id"] == sub.id
        assert body["family_id"] == fam.id  # fork stays under the platform family
    finally:
        _cleanup_platform(db, [fam.id])


# ── Product still maps to a family; subfamily optional ────────────────────────

def test_product_maps_to_family_subfamily_optional(client_as, tenant_a, db):
    fam = ChemicalFamily(name=f"PLAT-{uuid.uuid4().hex[:6]}")
    db.add(fam)
    db.flush()
    sub = Subfamily(family_id=fam.id, name="sub-x")
    db.add(sub)
    db.commit()
    try:
        c = client_as(tenant_a)
        # Create with a family only — subfamily omitted.
        r = c.post(f"/api/products/?team_id={tenant_a['team_id']}",
                   json={"name": "Widget", "chemical_family_id": fam.id})
        assert r.status_code == 201, r.text
        prod = r.json()
        assert prod["chemical_family_id"] == fam.id
        assert prod["subfamily_id"] is None
        # Attach a subfamily via update.
        upd = c.put(f"/api/products/{prod['id']}", json={"subfamily_id": sub.id})
        assert upd.status_code == 200, upd.text
        assert upd.json()["subfamily_id"] == sub.id
    finally:
        # The product's family FK is RESTRICT — remove the product before the family.
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM products WHERE team_id = :t"), {"t": str(tenant_a["team_id"])})
        db.commit()
        _cleanup_platform(db, [fam.id])
