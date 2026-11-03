"""Recipe-line audit (Jira 208): one status per recipe line, with the reason.

Acceptance criterion (Alexis, 2 Nov 2026): every recipe line points at a real
index with data where possible. Each status the audit can give is built here
on its own catalogue recipe and asserted by line, so the tests never depend on
the loaded catalog. The summary test checks the arithmetic: one status per
line, so the counts add up to the lines.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.formula_template import (
    FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexMonthlyValue, TypeCode
from app.services.recipe_line_audit import (
    AMBIGUOUS, CODE_MISMATCH, FIXED, FIXED_NO_INDEX, NO_SERIES, OK, OTHER_REGION,
    SERIES_EMPTY, STALE, STATUSES, SUB_RECIPE, audit_recipe_lines, summarise,
)

AS_OF = date(2026, 11, 2)


# ── Fixtures ─────────────────────────────────────────────────────────────────

class _World:
    """Everything one test builds, so cleanup can remove exactly that."""

    def __init__(self, db, tenant):
        self.db, self.tenant = db, tenant
        self.series, self.codes, self.templates = [], [], []

    def series_(self, *, region=None, months=((2026, 9),)):
        s = CommodityIndex(name=f"ra-{uuid.uuid4().hex[:8]}", scrape_enabled=False,
                           source_region=region)
        self.db.add(s)
        self.db.commit()
        for year, month in months:
            self.db.add(IndexMonthlyValue(commodity_id=s.id, year=year, month=month,
                                          value=100, kind="actual"))
        self.db.commit()
        self.series.append(s)
        return s

    def code(self, series, *, resolution="resolved"):
        tc = TypeCode(code=f"RA-{uuid.uuid4().hex[:8]}", resolution=resolution,
                      resolves_to_id=series.id if series else None)
        self.db.add(tc)
        self.db.commit()
        self.codes.append(tc)
        return tc

    def recipe(self, region="Europe"):
        tpl = FormulaTemplate(
            team_id=None, created_by=self.tenant["user_id"],
            name=f"ra-{uuid.uuid4().hex[:6]}", code=f"RA-{uuid.uuid4().hex[:6]}",
            expression=None,
        )
        self.db.add(tpl)
        self.db.commit()
        self.db.add(FormulaRegionCoverage(template_id=tpl.id, region=region, base_price=1000))
        self.db.commit()
        self.templates.append(tpl)
        return tpl

    def line(self, tpl, name, *, component_type="index", series=None, code=None,
             weight=10, region="Europe", cost_category=None, input_template=None):
        self.db.add(FormulaTemplateComponent(
            template_id=tpl.id, region=region, name=name, component_type=component_type,
            commodity_id=series.id if series else None,
            type_code_id=code.id if code else None,
            input_template_id=input_template.id if input_template else None,
            weight_pct=weight, cost_category=cost_category,
        ))
        self.db.commit()

    def audit(self):
        ids = {str(t.id) for t in self.templates}
        return {l.line_name: l for l in audit_recipe_lines(self.db, as_of=AS_OF)
                if l.template_id in ids}

    def cleanup(self):
        db = self.db
        db.rollback()
        bypass_rls_var.set(True)
        # Every line first: a sub-recipe line references another template.
        for t in self.templates:
            db.execute(text("DELETE FROM formula_template_components WHERE template_id = :i"),
                       {"i": str(t.id)})
        for t in self.templates:
            db.execute(text("DELETE FROM formula_region_coverage WHERE template_id = :i"),
                       {"i": str(t.id)})
            db.execute(text("DELETE FROM formula_templates WHERE id = :i"), {"i": str(t.id)})
        for tc in self.codes:
            db.execute(text("DELETE FROM type_codes WHERE id = :i"), {"i": tc.id})
        for s in self.series:
            db.execute(text("DELETE FROM index_monthly_values WHERE commodity_id = :i"), {"i": s.id})
            db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": s.id})
        db.commit()


@pytest.fixture
def world(db, tenant_a):
    w = _World(db, tenant_a)
    try:
        yield w
    finally:
        w.cleanup()


# ── One status per case ──────────────────────────────────────────────────────

def test_a_line_on_its_own_regions_current_series_is_ok(world):
    s = world.series_(region="EU")
    tpl = world.recipe()
    world.line(tpl, "gas", series=s, code=world.code(s))
    line = world.audit()["gas"]
    assert line.status == OK
    assert line.series_region == "Europe" and line.series_last_actual == "2026-09"


def test_a_global_series_or_no_stated_region_is_never_other_region(world):
    glob, unstated = world.series_(region="GL"), world.series_(region=None)
    tpl = world.recipe()
    world.line(tpl, "brent", series=glob, code=world.code(glob))
    world.line(tpl, "ppi", series=unstated, code=world.code(unstated))
    audit = world.audit()
    assert audit["brent"].status == OK and audit["ppi"].status == OK


def test_a_europe_line_on_a_north_america_series_is_other_region(world):
    s = world.series_(region="NA")
    tpl = world.recipe()
    world.line(tpl, "methanol", series=s, code=world.code(s))
    line = world.audit()["methanol"]
    assert line.status == OTHER_REGION
    assert "Europe line" in line.reason and "NA series" in line.reason


def test_an_apac_line_on_a_china_series_counts_as_its_own_region(world):
    s = world.series_(region="CN")
    tpl = world.recipe(region="APAC")
    world.line(tpl, "power", series=s, code=world.code(s), region="APAC")
    assert world.audit()["power"].status == OK


def test_an_old_series_is_stale(world):
    s = world.series_(region="EU", months=((2025, 6),))
    tpl = world.recipe()
    world.line(tpl, "sulphur", series=s, code=world.code(s))
    line = world.audit()["sulphur"]
    assert line.status == STALE and "2025-06" in line.reason


def test_a_series_with_no_values_is_series_empty(world):
    s = world.series_(region="EU", months=())
    tpl = world.recipe()
    world.line(tpl, "tio2", series=s, code=world.code(s))
    assert world.audit()["tio2"].status == SERIES_EMPTY


def test_type_code_resolution_states_are_reported(world):
    s = world.series_(region="EU")
    tpl = world.recipe()
    world.line(tpl, "ambiguous", code=world.code(None, resolution="ambiguous"))
    world.line(tpl, "no series", series=s, code=world.code(s, resolution="no_series"))
    audit = world.audit()
    assert audit["ambiguous"].status == AMBIGUOUS
    assert audit["no series"].status == NO_SERIES


def test_a_line_whose_series_disagrees_with_its_code_is_code_mismatch(world):
    a, b = world.series_(region="EU"), world.series_(region="EU")
    tpl = world.recipe()
    world.line(tpl, "mismatch", series=a, code=world.code(b))
    assert world.audit()["mismatch"].status == CODE_MISMATCH


def test_fixed_lines_split_margin_from_materials_with_no_index(world):
    tpl = world.recipe()
    world.line(tpl, "margin", component_type="fixed", cost_category="margin", weight=15)
    world.line(tpl, "pinene", component_type="fixed", cost_category="feedstock", weight=60)
    audit = world.audit()
    assert audit["margin"].status == FIXED
    assert audit["pinene"].status == FIXED_NO_INDEX


def test_a_sub_recipe_line_is_sub_recipe(world):
    inner, outer = world.recipe(), world.recipe()
    world.line(outer, "inner recipe", component_type="formula", input_template=inner)
    assert world.audit()["inner recipe"].status == SUB_RECIPE


def test_proxy_status_is_carried_not_turned_into_a_status(world):
    s = world.series_(region="EU")
    tpl = world.recipe()
    world.line(tpl, "gas", series=s, code=world.code(s))
    line = world.audit()["gas"]
    assert line.proxy_status is None and line.status == OK


def test_team_recipes_are_not_audited(world, db):
    s = world.series_(region="EU")
    tpl = world.recipe()
    tpl.team_id = world.tenant["team_id"]
    db.commit()
    world.line(tpl, "team line", series=s, code=world.code(s))
    assert "team line" not in world.audit()


# ── Summary arithmetic ───────────────────────────────────────────────────────

def test_summary_counts_every_line_once_and_scores_weight(world, db):
    ok_s, na_s = world.series_(region="EU"), world.series_(region="NA")
    tpl = world.recipe()
    world.line(tpl, "ok", series=ok_s, code=world.code(ok_s), weight=60)
    world.line(tpl, "other", series=na_s, code=world.code(na_s), weight=20)
    world.line(tpl, "margin", component_type="fixed", cost_category="margin", weight=20)

    lines = list(world.audit().values())
    s = summarise(db, lines, as_of=AS_OF)
    assert set(s.by_status) == set(STATUSES)
    assert sum(row["lines"] for row in s.by_status.values()) == len(lines) == 3
    # margin is not an index line, so 60 of the 80 scored points meet it
    assert s.meets_pct_of_index_weight == pytest.approx(75.0)
    assert s.by_status[OTHER_REGION]["examples"][0]["line_name"] == "other"


# ── API ──────────────────────────────────────────────────────────────────────

def test_api_requires_authentication(client):
    assert client.get("/api/recipe-audit").status_code == 401
    assert client.get("/api/recipe-audit/lines").status_code == 401


def test_api_summary_and_filtered_lines(world, tenant_a, client_as):
    s = world.series_(region="NA")
    tpl = world.recipe()
    world.line(tpl, "methanol", series=s, code=world.code(s), weight=80)
    c = client_as(tenant_a)

    summary = c.get("/api/recipe-audit", params={"as_of": AS_OF.isoformat()})
    assert summary.status_code == 200
    assert set(summary.json()["by_status"]) == set(STATUSES)

    r = c.get("/api/recipe-audit/lines",
              params={"template_code": tpl.code, "as_of": AS_OF.isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1 and body["lines"][0]["status"] == OTHER_REGION

    assert c.get("/api/recipe-audit/lines", params={"status": "nope"}).status_code == 422
