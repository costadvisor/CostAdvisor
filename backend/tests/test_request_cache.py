"""Request-scoped memo for index lookups (`data_resolver.index_lookup_cache`).

What the memo promises, one test each:
* outside a block nothing changes — every lookup reads the database;
* inside a block a repeated lookup is answered without SQL;
* nothing survives the block, also when the block raises;
* a nested block shares the outer memo and leaves it in place;
* the composite cycle guard is part of the key;
* the portfolio endpoints answer the same JSON with and without it;
* the forward lock/hold verdict compares with today's should-cost (the value
  every other portfolio view calls "current"), not the base price.
"""
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text

from app.database import engine
from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.index_data import CommodityIndex, IndexValue
from app.models.product import Product
from app.schemas.costing import EvolutionRequest
from app.services import costing_engine as CE
from app.services import data_resolver as DR
from app.services.data_resolver import (
    get_single_index_value, get_single_index_value_detailed, index_lookup_cache,
    index_lookup_cache_active,
)

REGION = "Europe"
HISTORY = [(2024, 1, 100), (2024, 2, 102), (2024, 3, 105), (2024, 4, 103)]


@contextmanager
def count_statements():
    n = [0]

    def before(*_args, **_kwargs):
        n[0] += 1

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield n
    finally:
        event.remove(engine, "before_cursor_execute", before)


class _MemoOff:
    """Stands in for the ContextVar: never open, so every lookup reads the DB."""

    def get(self):
        return None

    def set(self, _value):
        return None

    def reset(self, _token):
        pass


@pytest.fixture
def series(db):
    """A quarterly series 2024Q1..Q4 and a composite (2 × series) over it."""
    suffix = uuid.uuid4().hex[:8]
    base = CommodityIndex(name=f"Memo-{suffix}", currency="USD", unit="t")
    db.add(base)
    db.flush()
    db.add_all([IndexValue(commodity_id=base.id, region=REGION, year=y, quarter=q, value=v)
                for y, q, v in HISTORY])
    comp = CommodityIndex(
        name=f"Memo-composite-{suffix}", currency="USD", unit="t",
        composite_expression="a * 2",
        composite_variables={"a": {"type": "index", "commodity_id": base.id, "region": REGION}},
    )
    db.add(comp)
    db.commit()
    ids = SimpleNamespace(base=base.id, composite=comp.id)
    yield ids
    db.rollback()
    db.query(IndexValue).filter(IndexValue.commodity_id == ids.base).delete(synchronize_session=False)
    db.query(CommodityIndex).filter(CommodityIndex.id.in_([ids.base, ids.composite])).delete(
        synchronize_session=False)
    db.commit()


@pytest.fixture
def model(tenant_a, db, series):
    """One single-line cost model on the series: base 2024Q1 at 100, margin 0."""
    product = Product(id=uuid.uuid4(), team_id=tenant_a["team_id"],
                      created_by=tenant_a["user_id"], name="Memo test product", unit="kg")
    db.add(product)
    db.flush()
    cm = CostModel(id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=product.id,
                   created_by=tenant_a["user_id"], region=REGION, currency="USD")
    db.add(cm)
    db.flush()
    fv = FormulaVersion(cost_model_id=cm.id, base_price=100, base_year=2024, base_quarter=1,
                        formula_type="simple", margin_type="pct", margin_value=0)
    db.add(fv)
    db.flush()
    db.add(FormulaComponent(formula_version_id=fv.id, label="Feedstock",
                            commodity_id=series.base, weight=1.0))
    db.commit()
    cm_id = cm.id
    yield cm
    db.rollback()
    db.execute(text("DELETE FROM cost_models WHERE id = :id"), {"id": str(cm_id)})
    db.commit()


_TEAM = uuid.UUID(int=0)   # no team: no override or fixed source applies


def _lookup(db, series, quarter=3):
    return get_single_index_value(db, _TEAM, series.base, REGION, 2024, quarter)


# ── The memo itself ──────────────────────────────────────────────────────────

def test_outside_a_block_every_lookup_reads_the_database(db, series):
    assert not index_lookup_cache_active()
    with count_statements() as first:
        assert _lookup(db, series) == 105.0
    with count_statements() as second:
        assert _lookup(db, series) == 105.0
    assert first[0] > 0 and second[0] == first[0]


def test_inside_a_block_a_repeat_is_answered_without_sql(db, series):
    with index_lookup_cache():
        assert index_lookup_cache_active()
        with count_statements() as first:
            assert _lookup(db, series) == 105.0
        with count_statements() as repeat:
            assert _lookup(db, series) == 105.0
            assert get_single_index_value_detailed(
                db, _TEAM, series.base, REGION, 2024, 3) == (105.0, "scraped_region")
        with count_statements() as other_quarter:
            assert _lookup(db, series, quarter=4) == 103.0
    assert first[0] > 0
    assert repeat[0] == 0
    assert other_quarter[0] > 0
    assert not index_lookup_cache_active()


def test_nothing_survives_the_block(db, series):
    with index_lookup_cache():
        assert _lookup(db, series) == 105.0
    db.query(IndexValue).filter(IndexValue.commodity_id == series.base, IndexValue.year == 2024,
                                IndexValue.quarter == 3).update({"value": 999})
    db.commit()
    assert _lookup(db, series) == 999.0
    with index_lookup_cache():
        assert _lookup(db, series) == 999.0


def test_memo_is_dropped_when_the_block_raises(db, series):
    with pytest.raises(RuntimeError):
        with index_lookup_cache():
            _lookup(db, series)
            raise RuntimeError("boom")
    assert not index_lookup_cache_active()
    with count_statements() as n:
        _lookup(db, series)
    assert n[0] > 0


def test_nested_block_shares_the_outer_memo_and_leaves_it_open(db, series):
    with index_lookup_cache():
        _lookup(db, series)
        with index_lookup_cache():
            with count_statements() as inner:
                _lookup(db, series)
        assert index_lookup_cache_active()
        with count_statements() as after_inner:
            _lookup(db, series)
    assert inner[0] == 0 and after_inner[0] == 0
    assert not index_lookup_cache_active()


def test_composite_cycle_guard_is_part_of_the_key(db, series):
    """Inside its own chain a composite resolves to nothing (cycle guard); a
    call from outside the chain must still get the real value, whichever of
    the two the memo saw first."""
    args = (db, _TEAM, series.composite, REGION, 2024, 3)
    with index_lookup_cache():
        assert get_single_index_value_detailed(*args, _resolving={series.composite}) == (None, None)
        assert get_single_index_value_detailed(*args) == (210.0, "composite")
    with index_lookup_cache():
        assert get_single_index_value_detailed(*args) == (210.0, "composite")
        assert get_single_index_value_detailed(*args, _resolving={series.composite}) == (None, None)


def test_evolution_resolves_each_distinct_lookup_once(db, model, monkeypatch):
    """The costing engine opens the memo itself: within one evolution no
    (commodity, region, quarter) is resolved twice."""
    seen = []
    real = DR._resolve_index_value_detailed

    def spy(*args, **kwargs):
        seen.append(args[1:] + (frozenset(kwargs.get("_resolving") or ()),))
        return real(*args, **kwargs)

    monkeypatch.setattr(DR, "_resolve_index_value_detailed", spy)
    cm = db.get(CostModel, model.id)
    evo = CE.calculate_evolution(db, cm, EvolutionRequest(cost_model_id=cm.id))
    assert [p.theoretical for p in evo.periods] == [100.0, 102.0, 105.0, 103.0]
    assert seen and len(seen) == len(set(seen))
    assert not index_lookup_cache_active()


def test_unsaved_formula_versions_are_not_memoised(db, model):
    """Two versions with no id yet must not share a memo entry."""
    cm = db.get(CostModel, model.id)
    a = FormulaVersion(base_price=1, base_year=2024, base_quarter=1)
    a.components = [FormulaComponent(label="A", weight=1.0)]
    b = FormulaVersion(base_price=1, base_year=2024, base_quarter=1)
    b.components = [FormulaComponent(label="B", weight=1.0)]
    with index_lookup_cache():
        assert [ln.label for ln in CE.get_effective_lines(db, a, cm)[0]] == ["A"]
        assert [ln.label for ln in CE.get_effective_lines(db, b, cm)[0]] == ["B"]


# ── The portfolio endpoints ──────────────────────────────────────────────────

def _portfolio_json(c, team_id, cm_id) -> dict:
    out = {}
    for name, url in (
        ("summary", "/api/portfolio/summary"),
        ("matrix", "/api/portfolio/priority-matrix"),
        ("buy", "/api/portfolio/buy-windows"),
    ):
        r = c.get(url, params={"team_id": str(team_id)})
        assert r.status_code == 200, r.text
        out[name] = r.content
    for name, url in (("buy_one", f"/api/portfolio/buy-windows/{cm_id}"),
                      ("verdict", f"/api/portfolio/buy-windows/{cm_id}/verdict")):
        r = c.get(url)
        assert r.status_code == 200, r.text
        out[name] = r.content
    return out


def test_portfolio_endpoints_answer_the_same_with_and_without_the_memo(
        client_as, tenant_a, model, monkeypatch):
    c = client_as(tenant_a)
    with_memo = _portfolio_json(c, tenant_a["team_id"], model.id)
    monkeypatch.setattr(DR, "_index_memo", _MemoOff())
    without = _portfolio_json(c, tenant_a["team_id"], model.id)
    assert with_memo == without


def test_verdict_current_is_todays_should_cost(client_as, tenant_a, model):
    """Base 2024Q1 = 100 at price 100; the series' latest value (2024Q4 = 103)
    carries forward to today. The verdict's "current" must be 103 — what the
    summary shows — not the base price 100."""
    c = client_as(tenant_a)
    summary = c.get("/api/portfolio/summary", params={"team_id": str(tenant_a["team_id"])}).json()
    row = next(m for m in summary["models"] if m["cost_model_id"] == str(model.id))
    verdict = c.get(f"/api/portfolio/buy-windows/{model.id}/verdict").json()
    assert row["current_should_cost"] == pytest.approx(103.0)
    assert verdict["current_should_cost"] == pytest.approx(row["current_should_cost"])
    assert verdict["verdict"] == "insufficient"   # no projection stored for the series
