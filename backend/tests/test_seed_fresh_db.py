"""The seeds on a migrated database (design §6 WP-8; full plan 3c.4).

* `app/seed.py` adds only what is missing: migrations already insert most
  regions and some commodity rows, and a second run changes nothing. It never
  creates tables (the schema is Alembic's).
* `seed_all.py --stages` runs a chosen subset, in dependency order.
* The demo seeds resolve commodity indexes **by name**, and refuse, before
  writing anything, when one is missing or belongs to a content series. The
  staminachem seed is also the "Load example data" button, which must work on
  a loaded database and must not add a chemical family.

The full fresh-database path (reset, migrate, `seed_all.py --stages 1`, the
content load) is what `scripts/ops/rebuild_env.py build` runs; these tests
check the seeds' own rules on the test database and clean up what they add.
"""
from __future__ import annotations

import pytest

import seed_all
import seed_jacobi
import seed_staminachem
from app import seed as app_seed
from app.database import bypass_rls_var
from app.models.chemical_family import ChemicalFamily
from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.index_data import CommodityIndex, IndexValue
from app.models.product import Product
from app.models.region import Region
from app.models.scenario import CostScenario
from app.models.supplier import Supplier
from tests import content_drop_expect as expect


# ── app/seed.py ──────────────────────────────────────────────────────────────

def test_the_reference_seed_adds_only_what_is_missing(db):
    bypass_rls_var.set(True)
    regions_before = {r.code for r in db.query(Region)}
    scenarios_before = {s.id for s in db.query(CostScenario).filter(CostScenario.team_id.is_(None))}
    try:
        for _ in range(2):
            # As `seed_all.py --stages 1` runs it: no request user, and no
            # bypass set by the caller (the seed sets its own).
            bypass_rls_var.set(False)
            app_seed.seed()
            app_seed.seed_update()
        bypass_rls_var.set(True)
        db.expire_all()
        regions = {r.code: r for r in db.query(Region)}
        assert {code for code, _name, _parent in app_seed.REGIONS_SEED} <= set(regions)
        for code, _name, parent in app_seed.REGIONS_SEED:
            if parent:
                assert regions[code].parent_id == regions[parent].id, code
        system = [s.name for s in db.query(CostScenario).filter(
            CostScenario.is_system.is_(True), CostScenario.team_id.is_(None))]
        # One of each, however many runs.
        assert sorted(system) == sorted(app_seed.SCENARIOS_DATA)
        names = {n for (n,) in db.query(CommodityIndex.name)}
        assert set(app_seed.INDEXES_DATA) <= names
    finally:
        db.rollback()
        bypass_rls_var.set(True)
        for s in db.query(CostScenario).filter(CostScenario.team_id.is_(None)):
            if s.id not in scenarios_before:
                db.delete(s)
        for r in db.query(Region).filter(Region.code.notin_(regions_before)):
            db.delete(r)
        db.commit()


def test_the_reference_seed_creates_no_tables():
    """The schema comes from Alembic; a seed that created missing model tables
    would do so without their row-level security."""
    import inspect

    assert "create_all" not in inspect.getsource(app_seed)


def test_the_stage_selector():
    assert seed_all.parse_stages("1") == [1]
    assert seed_all.parse_stages("3,1") == [1, 3]
    assert seed_all.parse_stages(None) == sorted(seed_all.STAGES)
    for bad in ("9", "x", ","):
        with pytest.raises(SystemExit):
            seed_all.parse_stages(bad)


# ── "Load example data" (seed_staminachem) ──────────────────────────────────

@pytest.fixture
def index_values_kept(db):
    """The demo upserts quarterly values on its reference indexes. Put back
    what was there and remove what it added."""
    bypass_rls_var.set(True)
    names = [n for n, _values in seed_staminachem.INDEX_BACKFILLS]
    ids = [i for (i,) in db.query(CommodityIndex.id).filter(CommodityIndex.name.in_(names))]
    before = {(v.commodity_id, v.region, v.year, v.quarter): v.value
              for v in db.query(IndexValue).filter(IndexValue.commodity_id.in_(ids))}
    yield
    db.rollback()
    bypass_rls_var.set(True)
    for v in db.query(IndexValue).filter(IndexValue.commodity_id.in_(ids)):
        key = (v.commodity_id, v.region, v.year, v.quarter)
        if key not in before:
            db.delete(v)
        elif v.value != before[key]:
            v.value = before[key]
    db.commit()


def _team_rows(db, team_id) -> dict:
    bypass_rls_var.set(True)
    return {"products": db.query(Product).filter(Product.team_id == team_id).count(),
            "suppliers": db.query(Supplier).filter(Supplier.team_id == team_id).count(),
            "cost_models": db.query(CostModel).filter(CostModel.team_id == team_id).count()}


def test_load_example_data_works_on_the_loaded_database(
        db, content_loaded, user_factory, client_as, index_values_kept):
    owner = user_factory()
    c = client_as(owner)
    url = f"/api/teams/{owner['team_id']}/load-example-data"
    r = c.post(url)
    assert r.status_code == 200, r.text
    first = _team_rows(db, owner["team_id"])
    assert first["products"] > 0 and first["cost_models"] > 0

    # Idempotent: a second click adds nothing.
    assert c.post(url).status_code == 200
    assert _team_rows(db, owner["team_id"]) == first

    bypass_rls_var.set(True)
    # The platform taxonomy is the drop's alone: the demo adds no family, and
    # its products carry no line.
    assert db.query(ChemicalFamily).count() == len(expect.families())
    products = db.query(Product).filter(Product.team_id == owner["team_id"]).all()
    assert all(p.product_line_id is None for p in products)

    # Every index the demo's cost models read is a reference index, by name.
    used = {
        cid for (cid,) in db.query(FormulaComponent.commodity_id)
        .join(FormulaVersion, FormulaVersion.id == FormulaComponent.formula_version_id)
        .join(CostModel, CostModel.id == FormulaVersion.cost_model_id)
        .filter(CostModel.team_id == owner["team_id"], FormulaComponent.commodity_id.isnot(None))
    }
    rows = db.query(CommodityIndex).filter(CommodityIndex.id.in_(used)).all()
    assert {r.name for r in rows} <= set(seed_staminachem.commodity_names())
    assert all(r.commodity_key is None for r in rows)


def test_load_example_data_refuses_without_its_reference_indexes(
        db, monkeypatch, user_factory, client_as):
    monkeypatch.setattr(seed_staminachem, "INDEX_BACKFILLS",
                        seed_staminachem.INDEX_BACKFILLS + [("No such index (test)", [])])
    owner = user_factory()
    r = client_as(owner).post(f"/api/teams/{owner['team_id']}/load-example-data")
    assert r.status_code == 409, r.text
    assert "No such index (test)" in r.json()["detail"]
    # Refused before any write.
    assert _team_rows(db, owner["team_id"]) == {"products": 0, "suppliers": 0, "cost_models": 0}


def test_the_example_data_never_writes_to_a_content_series(db, content_loaded, monkeypatch):
    bypass_rls_var.set(True)
    series = db.query(CommodityIndex.name).filter(
        CommodityIndex.commodity_key.isnot(None)).order_by(CommodityIndex.name).first()
    assert series is not None, "the loaded database has no content series"
    monkeypatch.setattr(seed_staminachem, "INDEX_BACKFILLS", [(series.name, [])])
    with db.get_bind().connect() as conn:
        with pytest.raises(seed_staminachem.MissingReferenceData, match="content load"):
            seed_staminachem.resolve_commodities(conn)


# ── seed_jacobi ──────────────────────────────────────────────────────────────

def test_the_jacobi_seed_refuses_before_writing():
    created = {row[0] for row in seed_jacobi.NEW_COMMODITIES}
    fine = {name: None for name in created}
    seed_jacobi.check_commodities(fine)  # every name is created or exists

    used = sorted({n for cm in seed_jacobi.COST_MODELS for _l, n, _w in cm["components"] if n})
    # An index a cost model reads, owned by the content load.
    with pytest.raises(seed_jacobi.MissingReferenceData, match="content load"):
        seed_jacobi.check_commodities({**fine, used[0]: "made-up-series-key"})


def test_the_jacobi_seed_refuses_a_missing_index(monkeypatch):
    monkeypatch.setattr(seed_jacobi, "COST_MODELS", [
        {"product": "x", "components": [("Line", "No such index (test)", 1.0)]}])
    with pytest.raises(seed_jacobi.MissingReferenceData, match="missing"):
        seed_jacobi.check_commodities({})


def test_the_seeds_write_no_chemical_family():
    """Families are the platform taxonomy, loaded from the content drop."""
    import inspect

    for module in (app_seed, seed_all, seed_staminachem, seed_jacobi):
        source = inspect.getsource(module)
        assert "INSERT INTO chemical_families" not in source, module.__name__
        assert "ChemicalFamily(" not in source, module.__name__


def test_no_seed_inserts_a_hard_coded_commodity_id():
    """On a fresh database the ids follow load order; a demo value written to a
    hard-coded id lands on whatever series got it."""
    for name, _values in seed_staminachem.INDEX_BACKFILLS:
        assert isinstance(name, str), name
    for cm in seed_staminachem.COST_MODELS:
        for sup in cm["suppliers"]:
            for _label, name, _w in sup["components"]:
                assert name is None or isinstance(name, str), (cm["product"], name)
