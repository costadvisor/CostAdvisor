"""Scrum 17 follow-up: commodity_indexes -> real taxonomy FK (family_id/
subfamily_id), derived from recipe usage, plus GET /api/indexes/usage."""
import uuid

import pytest

from app.models.chemical_family import ChemicalFamily
from app.models.subfamily import Subfamily
from app.models.index_data import CommodityIndex
from app.models.formula_template import FormulaTemplate, FormulaTemplateComponent
from app.services.index_family_mapping import map_index_families


@pytest.fixture
def taxonomy_and_commodity(db, user_factory):
    """A platform family+subfamily, two platform templates in that family
    (one with a subfamily, one without), and a commodity used by both -
    plus a second commodity with zero recipe usage. Cleaned up explicitly
    (platform rows, no team CASCADE)."""
    suf = uuid.uuid4().hex[:6]
    created_by = user_factory()["user_id"]
    fam = ChemicalFamily(name=f"Fam-{suf}")
    db.add(fam)
    db.flush()
    sub = Subfamily(family_id=fam.id, name=f"Sub-{suf}")
    db.add(sub)
    db.flush()

    used = CommodityIndex(name=f"Used-{suf}", retrieval_status="free")
    unused = CommodityIndex(name=f"Unused-{suf}", retrieval_status="free")
    db.add_all([used, unused])
    db.flush()

    t1 = FormulaTemplate(team_id=None, created_by=created_by, name=f"T1-{suf}", expression=None,
                          family_id=fam.id, subfamily_id=sub.id)
    t2 = FormulaTemplate(team_id=None, created_by=created_by, name=f"T2-{suf}", expression=None,
                          family_id=fam.id, subfamily_id=None)
    db.add_all([t1, t2])
    db.flush()
    db.add_all([
        FormulaTemplateComponent(template_id=t1.id, name="line1",
                                  component_type="index", commodity_id=used.id, weight_pct=60),
        FormulaTemplateComponent(template_id=t2.id, name="line2",
                                  component_type="index", commodity_id=used.id, weight_pct=40),
    ])
    db.commit()

    yield used, unused, fam, sub

    db.query(FormulaTemplateComponent).filter(
        FormulaTemplateComponent.template_id.in_([t1.id, t2.id])
    ).delete(synchronize_session=False)
    db.query(FormulaTemplate).filter(FormulaTemplate.id.in_([t1.id, t2.id])).delete(synchronize_session=False)
    db.query(CommodityIndex).filter(CommodityIndex.id.in_([used.id, unused.id])).delete(synchronize_session=False)
    db.query(Subfamily).filter(Subfamily.id == sub.id).delete(synchronize_session=False)
    db.query(ChemicalFamily).filter(ChemicalFamily.id == fam.id).delete(synchronize_session=False)
    db.commit()


def test_mapping_pass_uses_most_common_family_and_never_fabricates(db, taxonomy_and_commodity):
    used, unused, fam, sub = taxonomy_and_commodity

    report = map_index_families(db, apply=True)
    db.commit()
    db.refresh(used)
    db.refresh(unused)

    assert used.family_id == fam.id
    # subfamily only carried by t1 (2 of the commodity's usages disagree:
    # t1 has sub set, t2 doesn't) -> most-common among agreeing-family rows
    # is still sub.id since t2's None doesn't outvote it under the family filter.
    assert used.subfamily_id == sub.id
    assert unused.family_id is None
    assert unused.subfamily_id is None
    assert report.mapped >= 1
    assert report.no_usage >= 1


def test_mapping_pass_is_idempotent(db, taxonomy_and_commodity):
    used, unused, fam, sub = taxonomy_and_commodity
    map_index_families(db, apply=True)
    db.commit()

    report2 = map_index_families(db, apply=True)
    db.commit()
    assert report2.mapped == 0
    assert report2.unchanged >= 1


def test_dry_run_writes_nothing(db, taxonomy_and_commodity):
    used, unused, fam, sub = taxonomy_and_commodity
    map_index_families(db, apply=False)
    db.rollback()
    db.refresh(used)
    assert used.family_id is None


def test_usage_endpoint_returns_mapped_commodity(db, taxonomy_and_commodity, client_as, user_factory):
    used, unused, fam, sub = taxonomy_and_commodity
    map_index_families(db, apply=True)
    db.commit()

    u = user_factory()
    r = client_as(u).get("/api/indexes/usage")
    assert r.status_code == 200, r.text
    rows = {row["commodity_id"]: row for row in r.json()}
    assert used.id in rows
    assert rows[used.id]["family_id"] == fam.id
    assert rows[used.id]["family_name"] == fam.name
    assert rows[used.id]["subfamily_id"] == sub.id
    # unmapped commodity must never appear with a fabricated family
    assert unused.id not in rows


def test_usage_endpoint_requires_authentication(client):
    r = client.get("/api/indexes/usage")
    assert r.status_code == 401
