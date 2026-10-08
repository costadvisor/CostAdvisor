"""The supply taxonomy on the team side (design §2.2, §2.8).

The platform taxonomy (family › sub-family › product line) is loaded from the
content drop. It is platform-only: no team forks, no row-level security, so
every team reads the same tree. A team product reaches it in one of three
ways, the first that gives a line winning (`services/effective_lines.py`):

1. its linked catalogue template (a team fork stands for its origin);
2. the template behind one of its cost models;
3. its manual line, which only a custom product keeps.

`ProductOut` serves the result as `family`, `subfamily`, `product_line` and
`taxonomy_source`, and the formulas API serves a template's place the same
way. These tests use hand-made taxonomy rows with made-up names, not drop
content.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.database import SessionLocal, bypass_rls_var, current_user_id_var
from app.models.chemical_family import ChemicalFamily
from app.models.cost_model import CostModel, FormulaVersion
from app.models.formula_template import FormulaRegionCoverage, FormulaTemplate
from app.models.product import Product
from app.models.product_line import ProductLine
from app.models.subfamily import Subfamily


def _as_user(user_id):
    """Fresh RLS-scoped session acting as the given user (policies on)."""
    s = SessionLocal()
    bypass_rls_var.set(False)
    current_user_id_var.set(str(user_id))
    return s


@pytest.fixture
def spine(db, tenant_a):
    """Two made-up families: one with a sub-family, a line on it, a retired
    line, a template on the line and a combo for it; the other with a line
    that has no sub-family, and an off-axis template (family, no line)."""
    suffix = uuid.uuid4().hex[:8]
    fam = ChemicalFamily(name=f"Fam-{suffix}")
    fam2 = ChemicalFamily(name=f"Fam2-{suffix}")
    db.add_all([fam, fam2])
    db.flush()
    sub = Subfamily(family_id=fam.id, name=f"Sub-{suffix}")
    db.add(sub)
    db.flush()
    line = ProductLine(family_id=fam.id, subfamily_id=sub.id, platform=f"PLAT-T1-{suffix}",
                       line_key=f"{fam.name}|||Line-{suffix}", name=f"Line-{suffix}")
    line2 = ProductLine(family_id=fam2.id, platform=f"PLAT-T2-{suffix}",
                        line_key=f"{fam2.name}|||Line2-{suffix}", name=f"Line2-{suffix}")
    retired = ProductLine(family_id=fam.id, subfamily_id=sub.id, platform=f"PLAT-T3-{suffix}",
                          line_key=f"{fam.name}|||Gone-{suffix}", name=f"Gone-{suffix}",
                          retired_at=datetime.now(timezone.utc))
    db.add_all([line, line2, retired])
    db.flush()
    tpl = FormulaTemplate(team_id=None, created_by=tenant_a["user_id"], name=f"Tpl-{suffix}",
                          code=f"TST-{suffix}", family_id=fam.id, product_line_id=line.id,
                          card_kind="product", supply_status="live")
    off = FormulaTemplate(team_id=None, created_by=tenant_a["user_id"], name=f"Off-{suffix}",
                          code=f"TSO-{suffix}", family_id=fam2.id, product_line_id=None,
                          card_kind="product", supply_status="not_audited")
    db.add_all([tpl, off])
    db.flush()
    cov = FormulaRegionCoverage(template_id=tpl.id, region="Europe", base_price=100,
                                currency="EUR", base_year=2024, base_quarter=1)
    db.add(cov)
    db.commit()

    yield {"family": fam, "family2": fam2, "sub": sub, "line": line, "line2": line2,
           "retired": retired, "template": tpl, "off_axis": off, "coverage": cov}

    db.rollback()
    bypass_rls_var.set(True)
    tpl_ids = {"a": str(tpl.id), "b": str(off.id)}
    line_ids = {"a": line.id, "b": line2.id, "c": retired.id}
    # Team rows that point here: forks, products and their cost models (the
    # tenant's own teardown removes the rest of its team).
    db.execute(text("DELETE FROM cost_models WHERE product_id IN (SELECT id FROM products "
                    "WHERE formula_template_id IN (:a, :b))"), tpl_ids)
    db.execute(text("DELETE FROM products WHERE formula_template_id IN (:a, :b)"), tpl_ids)
    db.execute(text("DELETE FROM cost_models WHERE product_id IN (SELECT id FROM products "
                    "WHERE product_line_id IN (:a, :b, :c))"), line_ids)
    db.execute(text("DELETE FROM products WHERE product_line_id IN (:a, :b, :c)"), line_ids)
    db.execute(text("DELETE FROM formula_templates WHERE origin_id IN (:a, :b)"), tpl_ids)
    db.execute(text("DELETE FROM formula_templates WHERE id IN (:a, :b)"), tpl_ids)
    db.execute(text("DELETE FROM product_lines WHERE id IN (:a, :b, :c)"), line_ids)
    db.execute(text("DELETE FROM subfamilies WHERE id = :i"), {"i": sub.id})
    db.execute(text("DELETE FROM chemical_families WHERE id IN (:a, :b)"),
               {"a": fam.id, "b": fam2.id})
    db.commit()


def _ref(row) -> dict:
    return {"id": row.id, "name": row.name}


def _create(client, tenant, **body):
    payload = {"name": f"P-{uuid.uuid4().hex[:6]}", "unit": "kg", **body}
    return client.post(f"/api/products/?team_id={tenant['team_id']}", json=payload)


# ── The platform tree ────────────────────────────────────────────────────────

def test_the_platform_taxonomy_has_no_team_side():
    """No team forks on any tier: the team columns are gone."""
    for model in (ChemicalFamily, Subfamily, ProductLine):
        assert "team_id" not in model.__table__.c, model.__name__
        assert "origin_id" not in model.__table__.c, model.__name__
    for column in ("chemical_family_id", "subfamily_id"):
        assert column not in Product.__table__.c


def test_every_team_reads_the_platform_tree(spine, tenant_a, tenant_b):
    """Platform rows with no row-level security: a member of any team reads
    them through an RLS-scoped session."""
    for tenant in (tenant_a, tenant_b):
        s = _as_user(tenant["user_id"])
        try:
            assert s.get(ChemicalFamily, spine["family"].id) is not None
            assert s.get(Subfamily, spine["sub"].id) is not None
            assert s.get(ProductLine, spine["line"].id) is not None
        finally:
            s.close()
            bypass_rls_var.set(True)


# ── Products ─────────────────────────────────────────────────────────────────

def test_a_custom_product_keeps_its_manual_line(client_as, tenant_a, spine):
    r = _create(client_as(tenant_a), tenant_a, product_line_id=spine["line"].id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["product_line_id"] == spine["line"].id
    assert body["product_line"] == _ref(spine["line"])
    assert body["subfamily"] == _ref(spine["sub"])
    assert body["family"] == _ref(spine["family"])
    assert body["taxonomy_source"] == "manual"
    # The legacy columns are gone from the contract.
    assert "chemical_family_id" not in body and "subfamily_id" not in body


def test_a_line_with_no_sub_family_reads_null_safe(client_as, tenant_a, spine):
    body = _create(client_as(tenant_a), tenant_a, product_line_id=spine["line2"].id).json()
    assert body["product_line"] == _ref(spine["line2"])
    assert body["subfamily"] is None
    assert body["family"] == _ref(spine["family2"])


def test_a_linked_template_gives_the_line_and_clears_the_manual_one(client_as, tenant_a, spine):
    """The manual line is for custom products only: a template that gives a
    line wins, so a stored manual line would only drift from it."""
    r = _create(client_as(tenant_a), tenant_a, formula_template_id=str(spine["template"].id),
                product_line_id=spine["line2"].id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["product_line_id"] is None
    assert body["product_line"] == _ref(spine["line"])
    assert body["family"] == _ref(spine["family"])
    assert body["taxonomy_source"] == "template"
    assert body["formula_template_code"] == spine["template"].code


def test_a_template_with_no_line_gives_its_family(client_as, tenant_a, spine):
    """An off-axis template: the family shows, the line reads "not yet
    published" (null), and the source is still the template."""
    body = _create(client_as(tenant_a), tenant_a,
                   formula_template_id=str(spine["off_axis"].id)).json()
    assert body["family"] == _ref(spine["family2"])
    assert body["product_line"] is None and body["subfamily"] is None
    assert body["taxonomy_source"] == "template"


def test_update_links_and_unlinks_both_ways(client_as, tenant_a, spine):
    c = client_as(tenant_a)
    pid = _create(c, tenant_a, product_line_id=spine["line2"].id).json()["id"]
    url = f"/api/products/{pid}"

    # Linking a template that gives a line clears the manual one.
    body = c.put(url, json={"formula_template_id": str(spine["template"].id)}).json()
    assert body["product_line_id"] is None
    assert body["product_line"] == _ref(spine["line"])
    assert body["taxonomy_source"] == "template"

    # A manual line cannot be set while that template gives one.
    body = c.put(url, json={"product_line_id": spine["line2"].id}).json()
    assert body["product_line_id"] is None and body["taxonomy_source"] == "template"

    # Explicit null unlinks the template: nothing left.
    body = c.put(url, json={"formula_template_id": None}).json()
    assert body["formula_template_id"] is None
    assert body["product_line"] is None and body["family"] is None
    assert body["taxonomy_source"] is None

    # A custom product takes a manual line, and explicit null clears it.
    body = c.put(url, json={"product_line_id": spine["line2"].id}).json()
    assert body["product_line"] == _ref(spine["line2"])
    assert body["taxonomy_source"] == "manual"
    body = c.put(url, json={"product_line_id": None}).json()
    assert body["product_line_id"] is None and body["product_line"] is None

    # An absent field leaves the link alone.
    c.put(url, json={"product_line_id": spine["line2"].id})
    body = c.put(url, json={"name": "renamed"}).json()
    assert body["product_line_id"] == spine["line2"].id


def test_an_unknown_or_retired_line_is_refused(client_as, tenant_a, spine):
    c = client_as(tenant_a)
    for line_id in (-1, spine["retired"].id):
        r = _create(c, tenant_a, product_line_id=line_id)
        assert r.status_code == 400, r.text
    pid = _create(c, tenant_a).json()["id"]
    r = c.put(f"/api/products/{pid}", json={"product_line_id": spine["retired"].id})
    assert r.status_code == 400, r.text


def test_the_cost_model_path(db, client_as, tenant_a, spine):
    """A product with no template, tracked through a cost model priced from a
    catalogue combo, sits on that combo's template's line."""
    pid = _create(client_as(tenant_a), tenant_a).json()["id"]
    bypass_rls_var.set(True)
    cm = CostModel(team_id=tenant_a["team_id"], product_id=uuid.UUID(pid),
                   created_by=tenant_a["user_id"], region="Europe", currency="EUR")
    db.add(cm)
    db.flush()
    db.add(FormulaVersion(cost_model_id=cm.id, base_price=100, base_year=2024, base_quarter=1,
                          source_coverage_id=spine["coverage"].id))
    db.commit()
    body = client_as(tenant_a).get(f"/api/products/{pid}").json()
    assert body["taxonomy_source"] == "cost_model"
    assert body["product_line"] == _ref(spine["line"])
    listed = client_as(tenant_a).get(f"/api/products/?team_id={tenant_a['team_id']}").json()
    assert next(p for p in listed if p["id"] == pid)["product_line"] == _ref(spine["line"])


def test_another_team_cannot_read_the_product(client_as, tenant_a, tenant_b, spine):
    pid = _create(client_as(tenant_a), tenant_a, product_line_id=spine["line"].id).json()["id"]
    assert client_as(tenant_b).get(f"/api/products/{pid}").status_code in (403, 404)


# ── Formulas ─────────────────────────────────────────────────────────────────

def test_a_template_serves_its_place_in_the_tree(client_as, tenant_a, spine):
    t = client_as(tenant_a).get(f"/api/formulas/{spine['template'].id}",
                                params={"team_id": str(tenant_a["team_id"])}).json()
    assert t["product_line_id"] == spine["line"].id
    assert t["product_line"] == _ref(spine["line"])
    assert t["subfamily"] == _ref(spine["sub"])
    assert t["family"] == _ref(spine["family"])
    assert t["family_name"] == spine["family"].name
    assert t["status"]["code"] == "live"
    assert "subfamily_id" not in t and "subfamily_name" not in t


def test_a_fork_inherits_the_line(db, client_as, tenant_a, spine):
    c = client_as(tenant_a)
    r = c.post(f"/api/formulas/{spine['template'].id}/fork",
               json={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 201, r.text
    fork = r.json()
    assert fork["product_line"] == _ref(spine["line"])
    assert fork["family"] == _ref(spine["family"])
    assert db.get(FormulaTemplate, uuid.UUID(fork["id"])).product_line_id == spine["line"].id

    # A product linked to the fork sits where the fork sits.
    body = _create(c, tenant_a, formula_template_id=fork["id"]).json()
    assert body["product_line"] == _ref(spine["line"])
    assert body["taxonomy_source"] == "template"


def test_a_fork_without_a_line_of_its_own_takes_its_origins(db, client_as, tenant_a, spine):
    """A fork made before forks carried the line still reads its origin's."""
    bypass_rls_var.set(True)
    fork = FormulaTemplate(team_id=tenant_a["team_id"], origin_id=spine["template"].id,
                           created_by=tenant_a["user_id"], name="old fork",
                           code=spine["template"].code)
    db.add(fork)
    db.commit()
    t = client_as(tenant_a).get(f"/api/formulas/{fork.id}",
                                params={"team_id": str(tenant_a["team_id"])}).json()
    assert t["product_line"] == _ref(spine["line"])
    assert t["family"] == _ref(spine["family"])
    body = _create(client_as(tenant_a), tenant_a, formula_template_id=str(fork.id)).json()
    assert body["product_line"] == _ref(spine["line"])
