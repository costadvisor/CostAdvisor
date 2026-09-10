"""Direct DB-level RLS tests. Proves Postgres policies filter correctly
regardless of app-layer checks, so a future query bug can't leak data."""
from __future__ import annotations

import uuid

import pytest

from sqlalchemy import text

from datetime import datetime, timedelta, timezone

from app.database import SessionLocal, bypass_rls_var, current_user_id_var
from app.models.product import Product
from app.models.supplier import Supplier
from app.models.custom_fx_rate import CustomFxRate
from app.models.formula_template import FormulaTemplate
from app.models.invite import TeamInvite
from app.models.rbac import Role, TeamMemberRole
from app.models.index_data import TeamProviderCredential


def _as_user(user_id):
    """Fresh RLS-scoped session acting as the given user (policies on)."""
    s = SessionLocal()
    bypass_rls_var.set(False)
    current_user_id_var.set(str(user_id))
    return s


def _make_product(db, team_id, created_by, name) -> Product:
    p = Product(
        id=uuid.uuid4(),
        team_id=team_id,
        created_by=created_by,
        name=name,
    )
    db.add(p)
    db.commit()
    return p


def test_no_guc_returns_zero_rows(tenant_a, db):
    _make_product(db, tenant_a["team_id"], tenant_a["user_id"], "a-widget")

    s = SessionLocal()
    current_user_id_var.set(None)
    bypass_rls_var.set(False)
    try:
        assert s.query(Product).count() == 0
    finally:
        s.close()


def test_user_sees_only_their_team(tenant_a, tenant_b, db):
    _make_product(db, tenant_a["team_id"], tenant_a["user_id"], "a-widget")
    _make_product(db, tenant_b["team_id"], tenant_b["user_id"], "b-widget")

    s = SessionLocal()
    bypass_rls_var.set(False)
    current_user_id_var.set(str(tenant_a["user_id"]))
    try:
        names = {p.name for p in s.query(Product).all()}
        assert names == {"a-widget"}
    finally:
        s.close()


def test_bypass_sees_everything(tenant_a, tenant_b, db):
    _make_product(db, tenant_a["team_id"], tenant_a["user_id"], "a-widget")
    _make_product(db, tenant_b["team_id"], tenant_b["user_id"], "b-widget")

    s = SessionLocal()
    bypass_rls_var.set(True)
    try:
        names = {p.name for p in s.query(Product).all()}
        assert {"a-widget", "b-widget"}.issubset(names)
    finally:
        s.close()


# ── Coverage across other tenant-scoped tables (Scrum 10) ─────────────────────

def test_supplier_rls_isolation(tenant_a, tenant_b, db):
    db.add(Supplier(team_id=tenant_a["team_id"], name="A-supplier"))
    db.add(Supplier(team_id=tenant_b["team_id"], name="B-supplier"))
    db.commit()
    s = _as_user(tenant_a["user_id"])
    try:
        names = {x.name for x in s.query(Supplier).all()}
        assert "A-supplier" in names and "B-supplier" not in names
    finally:
        s.close()


def test_custom_fx_rate_rls_isolation(tenant_a, tenant_b, db):
    db.add(CustomFxRate(team_id=tenant_a["team_id"], from_currency="AAA", to_currency="EUR",
                        year=2026, quarter=1, value_type="fixed", rate=1.11))
    db.add(CustomFxRate(team_id=tenant_b["team_id"], from_currency="BBB", to_currency="EUR",
                        year=2026, quarter=1, value_type="fixed", rate=2.22))
    db.commit()
    s = _as_user(tenant_a["user_id"])
    try:
        froms = {x.from_currency for x in s.query(CustomFxRate).all()}
        assert "AAA" in froms and "BBB" not in froms
    finally:
        s.close()


def test_formula_template_team_isolation_and_platform_visible(tenant_a, tenant_b, db):
    plat_name = f"PLATFORM-{uuid.uuid4().hex[:6]}"
    db.add(FormulaTemplate(team_id=tenant_a["team_id"], created_by=tenant_a["user_id"], name="A-tmpl", expression="X"))
    db.add(FormulaTemplate(team_id=tenant_b["team_id"], created_by=tenant_b["user_id"], name="B-tmpl", expression="Y"))
    db.add(FormulaTemplate(team_id=None, created_by=tenant_a["user_id"], name=plat_name, expression="Z"))
    db.commit()
    s = _as_user(tenant_a["user_id"])
    try:
        names = {t.name for t in s.query(FormulaTemplate).all()}
        assert "A-tmpl" in names          # own team visible
        assert "B-tmpl" not in names       # other team isolated
        assert plat_name in names          # platform (team_id IS NULL) visible to all
    finally:
        s.close()
        # team_id IS NULL row isn't covered by the team CASCADE and its created_by
        # FK would block the user teardown — remove it explicitly.
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM formula_templates WHERE name = :n"), {"n": plat_name})
        db.commit()


# ── Scrum 10: close the RLS gap on roles / team_member_roles / team_invites ────

def test_team_invite_rls_isolation(tenant_a, tenant_b, db):
    db.add(TeamInvite(team_id=tenant_a["team_id"], invited_email="a@x.com",
                      invited_by_id=tenant_a["user_id"],
                      expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
    db.add(TeamInvite(team_id=tenant_b["team_id"], invited_email="b@x.com",
                      invited_by_id=tenant_b["user_id"],
                      expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
    db.commit()
    s = _as_user(tenant_a["user_id"])
    try:
        emails = {i.invited_email for i in s.query(TeamInvite).all()}
        assert "a@x.com" in emails and "b@x.com" not in emails
    finally:
        s.close()


def test_team_member_role_rls_isolation(tenant_a, tenant_b, db):
    role_a = Role(team_id=tenant_a["team_id"], name=f"RoleA-{uuid.uuid4().hex[:6]}")
    role_b = Role(team_id=tenant_b["team_id"], name=f"RoleB-{uuid.uuid4().hex[:6]}")
    db.add_all([role_a, role_b])
    db.flush()
    db.add(TeamMemberRole(user_id=tenant_a["user_id"], team_id=tenant_a["team_id"], role_id=role_a.id))
    db.add(TeamMemberRole(user_id=tenant_b["user_id"], team_id=tenant_b["team_id"], role_id=role_b.id))
    db.commit()
    s = _as_user(tenant_a["user_id"])
    try:
        team_ids = {tmr.team_id for tmr in s.query(TeamMemberRole).all()}
        assert tenant_a["team_id"] in team_ids and tenant_b["team_id"] not in team_ids
    finally:
        s.close()


# ── Scrum 26: team_provider_credentials RLS ────────────────────────────────

def test_team_provider_credential_rls_isolation(tenant_a, tenant_b, db):
    db.add(TeamProviderCredential(
        team_id=tenant_a["team_id"], provider="fastmarkets",
        credential_encrypted="ciphertext-a", created_by=tenant_a["user_id"],
    ))
    db.add(TeamProviderCredential(
        team_id=tenant_b["team_id"], provider="fastmarkets",
        credential_encrypted="ciphertext-b", created_by=tenant_b["user_id"],
    ))
    db.commit()
    s = _as_user(tenant_a["user_id"])
    try:
        rows = s.query(TeamProviderCredential).filter(TeamProviderCredential.provider == "fastmarkets").all()
        team_ids = {r.team_id for r in rows}
        assert tenant_a["team_id"] in team_ids and tenant_b["team_id"] not in team_ids
        # Never leaks the ciphertext of another team's row either.
        assert not any(r.credential_encrypted == "ciphertext-b" for r in rows)
    finally:
        s.close()
        bypass_rls_var.set(True)
        db.query(TeamProviderCredential).filter(
            TeamProviderCredential.team_id.in_([tenant_a["team_id"], tenant_b["team_id"]])
        ).delete(synchronize_session=False)
        db.commit()


def test_role_team_isolation_and_platform_visible(tenant_a, tenant_b, db):
    plat_name = f"PLATFORM-ROLE-{uuid.uuid4().hex[:6]}"
    db.add(Role(team_id=tenant_a["team_id"], name=f"A-role-{uuid.uuid4().hex[:6]}"))
    db.add(Role(team_id=tenant_b["team_id"], name=f"B-role-{uuid.uuid4().hex[:6]}"))
    db.add(Role(team_id=None, name=plat_name))
    db.commit()
    s = _as_user(tenant_a["user_id"])
    try:
        names = {r.name for r in s.query(Role).all()}
        assert plat_name in names  # platform (team_id IS NULL) visible to all
        assert not any(n.startswith("B-role-") for n in names)  # other team isolated
    finally:
        s.close()
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM roles WHERE name = :n"), {"n": plat_name})
        db.commit()
