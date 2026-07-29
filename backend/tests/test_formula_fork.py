"""Catalog: fork a platform formula template into an editable team copy."""
import uuid

import pytest

from app.models.formula_template import FormulaTemplate, FormulaTemplateComponent, FormulaRegionCoverage


@pytest.fixture
def platform_formula(db, tenant_a):
    """A platform (team_id NULL) template with one component + one coverage row."""
    t = FormulaTemplate(team_id=None, created_by=tenant_a["user_id"], name=f"PlatFormula-{uuid.uuid4().hex[:6]}",
                        expression=None)
    db.add(t)
    db.flush()
    db.add(FormulaTemplateComponent(template_id=t.id, name="Base", component_type="fixed",
                                    weight_pct=100, sort_order=0))
    db.add(FormulaRegionCoverage(template_id=t.id, region="Europe", base_price=1000, currency="USD",
                                 margin_pct=8, base_year=2024, base_quarter=1))
    db.commit()
    yield t
    for m in (FormulaRegionCoverage, FormulaTemplateComponent):
        db.query(m).filter(m.template_id == t.id).delete(synchronize_session=False)
    # also remove any team forks pointing back at this platform row
    forks = db.query(FormulaTemplate).filter(FormulaTemplate.origin_id == t.id).all()
    for f in forks:
        for m in (FormulaRegionCoverage, FormulaTemplateComponent):
            db.query(m).filter(m.template_id == f.id).delete(synchronize_session=False)
    db.query(FormulaTemplate).filter(FormulaTemplate.origin_id == t.id).delete(synchronize_session=False)
    db.query(FormulaTemplate).filter(FormulaTemplate.id == t.id).delete(synchronize_session=False)
    db.commit()


def test_fork_copies_recipe_and_coverage(client_as, tenant_a, platform_formula, db):
    c = client_as(tenant_a)
    r = c.post(f"/api/formulas/{platform_formula.id}/fork", json={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 201, r.text
    fork = r.json()
    assert fork["origin_id"] == str(platform_formula.id)
    assert fork["team_id"] == str(tenant_a["team_id"])
    # recipe + coverage copied
    tp = {"team_id": str(tenant_a["team_id"])}
    comps = c.get(f"/api/formulas/{fork['id']}/components", params=tp).json()
    assert len(comps) == 1 and comps[0]["name"] == "Base"
    cov = c.get(f"/api/formulas/{fork['id']}/coverage", params=tp).json()
    assert len(cov) == 1 and cov[0]["region"] == "Europe" and float(cov[0]["base_price"]) == 1000.0
    # the fork is editable (team template)
    upd = c.put(f"/api/formulas/{fork['id']}", json={"name": "My Tuned Formula"})
    assert upd.status_code == 200 and upd.json()["name"] == "My Tuned Formula"


def test_duplicate_fork_conflicts(client_as, tenant_a, platform_formula):
    c = client_as(tenant_a)
    assert c.post(f"/api/formulas/{platform_formula.id}/fork", json={"team_id": str(tenant_a["team_id"])}).status_code == 201
    assert c.post(f"/api/formulas/{platform_formula.id}/fork", json={"team_id": str(tenant_a["team_id"])}).status_code == 409


def test_cannot_fork_a_team_template(client_as, tenant_a, platform_formula):
    c = client_as(tenant_a)
    fork_id = c.post(f"/api/formulas/{platform_formula.id}/fork", json={"team_id": str(tenant_a["team_id"])}).json()["id"]
    # forking the team fork itself is rejected (only platform rows are forkable)
    r = c.post(f"/api/formulas/{fork_id}/fork", json={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 400


def test_non_member_forbidden(client_as, tenant_a, tenant_b, platform_formula):
    r = client_as(tenant_b).post(f"/api/formulas/{platform_formula.id}/fork", json={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 403
