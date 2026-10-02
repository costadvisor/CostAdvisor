"""Supplier trust & margin grading (Scrum 32).

Covers:
- The score formula itself (pure function, hand-computed regression pin).
- Sufficient product-grain history persists a real score/grade/inputs.
- Insufficient history (no subfamily to pool into) -> insufficient_data,
  never a fabricated low score.
- Subfamily pooling: two individually-short products pooled together cross
  the threshold; a third, individually-sufficient sibling keeps its own row.
- Recompute upserts in place (same row identity, no duplicates).
- The `resolution: "raw_supplier_name"` flag is always present (no producer/
  alias canonicalisation entity exists in this repo).
- Permission gate (owner/admin only, matches /benchmark) and RLS.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.chemical_family import ChemicalFamily
from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.price_data import ActualPrice
from app.models.product import Product
from app.models.subfamily import Subfamily
from app.models.supplier import Supplier
from app.models.supplier_trust import SupplierTrustScore
from app.models.team import TeamMembership
from app.models.producer import Producer, ProducerAlias
from app.services.producers import get_or_create_producer, upsert_alias
from app.services.supplier_trust import (
    _score_from_history, team_producer_map,
)


def _mk_product(db, team_id, created_by, name, subfamily_id=None) -> Product:
    p = Product(team_id=team_id, created_by=created_by, name=name, unit="kg", subfamily_id=subfamily_id)
    db.add(p)
    db.commit()
    return p


def _mk_supplier(db, team_id, name) -> Supplier:
    s = Supplier(team_id=team_id, name=name)
    db.add(s)
    db.commit()
    return s


def _mk_flat_cost_model(db, team_id, created_by, product_id, supplier_id, base_price=100.0) -> CostModel:
    """A cost model whose should-cost is a constant `base_price` at every
    period (one fixed-weight component, no margin) — keeps the gap-%
    arithmetic exact and easy to hand-verify in tests."""
    cm = CostModel(team_id=team_id, product_id=product_id, supplier_id=supplier_id,
                    region="Europe", currency="USD", created_by=created_by)
    db.add(cm)
    db.flush()
    fv = FormulaVersion(cost_model_id=cm.id, base_price=base_price, base_year=2024, base_quarter=1,
                         margin_type="pct", margin_value=0, formula_type="simple")
    db.add(fv)
    db.flush()
    db.add(FormulaComponent(formula_version_id=fv.id, label="Fixed", commodity_id=None,
                             weight=1.0, component_type="fixed"))
    db.commit()
    return cm


def _add_prices(db, cost_model_id, uploaded_by, prices: list[float], start_year=2024, start_quarter=1):
    y, q = start_year, start_quarter
    for price in prices:
        db.add(ActualPrice(cost_model_id=cost_model_id, uploaded_by=uploaded_by, year=y, quarter=q, price=price))
        q += 1
        if q > 4:
            q = 1
            y += 1
    db.commit()


def _cleanup(db, cost_model_ids=(), product_ids=(), supplier_ids=(), subfamily_ids=(), family_ids=()):
    bypass_rls_var.set(True)
    for cmid in cost_model_ids:
        db.execute(text("DELETE FROM cost_models WHERE id = :id"), {"id": str(cmid)})
    for sid in supplier_ids:
        db.execute(text("DELETE FROM supplier_trust_scores WHERE supplier_id = :id"), {"id": sid})
        db.execute(text("DELETE FROM suppliers WHERE id = :id"), {"id": sid})
    for pid in product_ids:
        db.execute(text("DELETE FROM products WHERE id = :id"), {"id": str(pid)})
    for sfid in subfamily_ids:
        db.execute(text("DELETE FROM subfamilies WHERE id = :id"), {"id": sfid})
    for fid in family_ids:
        db.execute(text("DELETE FROM chemical_families WHERE id = :id"), {"id": fid})
    db.commit()


# ── Pure score formula ──────────────────────────────────────────────────────

def test_score_formula_regression():
    # gap% = [10, 15, 20, 25] -> perfectly linear worsening drift, slope=5.
    history = [(2024, 1, 10.0), (2024, 2, 15.0), (2024, 3, 20.0), (2024, 4, 25.0)]
    result = _score_from_history(history)
    assert result["inputs"]["avg_gap_pct"] == 17.5
    assert result["inputs"]["slope_pct_per_quarter"] == 5.0
    assert result["score"] == 69.1
    assert result["grade"] == "C"


def test_score_formula_flat_and_improving_gets_full_drift_credit():
    history = [(2024, 1, 5.0), (2024, 2, 4.0), (2024, 3, 3.0), (2024, 4, 2.0)]  # improving
    result = _score_from_history(history)
    assert result["inputs"]["drift_score"] == 100.0


# ── API / persistence ────────────────────────────────────────────────────────

def test_sufficient_history_persists_real_score(db, tenant_a, client_as):
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "P1")
    supplier = _mk_supplier(db, tenant_a["team_id"], "Acme Chemicals")
    cm = _mk_flat_cost_model(db, tenant_a["team_id"], tenant_a["user_id"], product.id, supplier.id)
    _add_prices(db, cm.id, tenant_a["user_id"], [110, 115, 120, 125])
    c = client_as(tenant_a)
    try:
        r = c.post(f"/api/suppliers/{supplier.id}/trust-score/compute",
                   params={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        row = rows[0]
        assert row["grain"] == "product"
        assert row["product_id"] == str(product.id)
        assert row["insufficient_data"] is False
        assert row["score"] == 69.1
        assert row["grade"] == "C"
        assert row["inputs"]["n_quarters"] == 4
    finally:
        _cleanup(db, [cm.id], [product.id], [supplier.id])


def test_insufficient_history_no_subfamily(db, tenant_a, client_as):
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "P-thin")
    supplier = _mk_supplier(db, tenant_a["team_id"], "Thin Data Co")
    cm = _mk_flat_cost_model(db, tenant_a["team_id"], tenant_a["user_id"], product.id, supplier.id)
    _add_prices(db, cm.id, tenant_a["user_id"], [110, 120])  # only 2 quarters, below MIN_QUARTERS
    c = client_as(tenant_a)
    try:
        r = c.post(f"/api/suppliers/{supplier.id}/trust-score/compute",
                   params={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 200, r.text
        row = r.json()[0]
        assert row["insufficient_data"] is True
        assert row["score"] is None
        assert row["grade"] is None
    finally:
        _cleanup(db, [cm.id], [product.id], [supplier.id])


def test_subfamily_pooling_and_sufficient_sibling_kept_separate(db, tenant_a, client_as):
    family = ChemicalFamily(name=f"Fam-{uuid.uuid4().hex[:8]}")
    db.add(family)
    db.flush()
    sub = Subfamily(family_id=family.id, name=f"Sub-{uuid.uuid4().hex[:8]}")
    db.add(sub)
    db.commit()

    supplier = _mk_supplier(db, tenant_a["team_id"], "Pooled Supplier")
    p_thin_1 = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "Thin1", subfamily_id=sub.id)
    p_thin_2 = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "Thin2", subfamily_id=sub.id)
    p_rich = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "Rich", subfamily_id=sub.id)

    cm1 = _mk_flat_cost_model(db, tenant_a["team_id"], tenant_a["user_id"], p_thin_1.id, supplier.id)
    _add_prices(db, cm1.id, tenant_a["user_id"], [105, 110])  # 2 quarters
    cm2 = _mk_flat_cost_model(db, tenant_a["team_id"], tenant_a["user_id"], p_thin_2.id, supplier.id)
    _add_prices(db, cm2.id, tenant_a["user_id"], [108, 112])  # 2 quarters -> pooled with cm1 = 4 quarters total
    cm3 = _mk_flat_cost_model(db, tenant_a["team_id"], tenant_a["user_id"], p_rich.id, supplier.id)
    _add_prices(db, cm3.id, tenant_a["user_id"], [101, 102, 103, 104])  # sufficient on its own

    c = client_as(tenant_a)
    try:
        r = c.post(f"/api/suppliers/{supplier.id}/trust-score/compute",
                   params={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 200, r.text
        rows = r.json()
        subfamily_rows = [row for row in rows if row["grain"] == "subfamily"]
        assert len(subfamily_rows) == 1
        assert subfamily_rows[0]["subfamily_id"] == sub.id
        assert subfamily_rows[0]["insufficient_data"] is False
        assert subfamily_rows[0]["inputs"]["n_quarters"] == 4

        product_rows = {row["product_id"]: row for row in rows if row["grain"] == "product"}
        assert str(p_rich.id) in product_rows
        assert product_rows[str(p_rich.id)]["insufficient_data"] is False
        # The two thin products were pooled, not left as their own insufficient rows.
        assert str(p_thin_1.id) not in product_rows
        assert str(p_thin_2.id) not in product_rows
    finally:
        _cleanup(db, [cm1.id, cm2.id, cm3.id], [p_thin_1.id, p_thin_2.id, p_rich.id],
                 [supplier.id], [sub.id], [family.id])


def test_recompute_upserts_in_place(db, tenant_a, client_as):
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "P-recompute")
    supplier = _mk_supplier(db, tenant_a["team_id"], "Recompute Co")
    cm = _mk_flat_cost_model(db, tenant_a["team_id"], tenant_a["user_id"], product.id, supplier.id)
    _add_prices(db, cm.id, tenant_a["user_id"], [110, 115, 120, 125])
    c = client_as(tenant_a)
    try:
        r1 = c.post(f"/api/suppliers/{supplier.id}/trust-score/compute",
                    params={"team_id": str(tenant_a["team_id"])})
        row_id = r1.json()[0]["id"]

        r2 = c.post(f"/api/suppliers/{supplier.id}/trust-score/compute",
                    params={"team_id": str(tenant_a["team_id"])})
        assert len(r2.json()) == 1
        assert r2.json()[0]["id"] == row_id  # same row, not a duplicate

        count = db.query(SupplierTrustScore).filter(SupplierTrustScore.supplier_id == supplier.id).count()
        assert count == 1
    finally:
        _cleanup(db, [cm.id], [product.id], [supplier.id])


def test_resolution_flag_present(db, tenant_a, client_as):
    supplier = _mk_supplier(db, tenant_a["team_id"], "Flagged Co")
    c = client_as(tenant_a)
    try:
        r = c.get("/api/suppliers/trust-scores", params={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 200, r.text
        assert r.json()["resolution"] == "raw_supplier_name"
    finally:
        _cleanup(db, supplier_ids=[supplier.id])


def test_resolution_flag_present_on_single_supplier_endpoints(db, tenant_a, client_as):
    """The all-suppliers listing wraps its response with a top-level
    `resolution` flag, but the single-supplier compute/get endpoints return a
    bare list of score rows with no wrapper to carry it — so each row itself
    must carry the flag, or a caller hitting one supplier at a time never
    sees the disclosure at all."""
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "P-single")
    supplier = _mk_supplier(db, tenant_a["team_id"], "Single Lookup Co")
    cm = _mk_flat_cost_model(db, tenant_a["team_id"], tenant_a["user_id"], product.id, supplier.id)
    _add_prices(db, cm.id, tenant_a["user_id"], [110, 115, 120, 125])
    c = client_as(tenant_a)
    try:
        r = c.post(f"/api/suppliers/{supplier.id}/trust-score/compute",
                   params={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["resolution"] == "raw_supplier_name"

        r2 = c.get(f"/api/suppliers/{supplier.id}/trust-score", params={"team_id": str(tenant_a["team_id"])})
        assert r2.status_code == 200, r2.text
        rows2 = r2.json()
        assert len(rows2) == 1
        assert rows2[0]["resolution"] == "raw_supplier_name"
    finally:
        _cleanup(db, [cm.id], [product.id], [supplier.id])


def test_permission_and_rls(db, tenant_a, tenant_b, user_factory, client_as):
    supplier = _mk_supplier(db, tenant_a["team_id"], "Gated Co")
    member = user_factory()
    db.add(TeamMembership(user_id=member["user_id"], team_id=tenant_a["team_id"], role="member"))
    db.commit()
    try:
        r = client_as(member).post(f"/api/suppliers/{supplier.id}/trust-score/compute",
                                    params={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 403

        r = client_as(member).get("/api/suppliers/trust-scores", params={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 403

        # tenant_b has no membership on tenant_a's team at all.
        r = client_as(tenant_b).get(f"/api/suppliers/{supplier.id}/trust-score",
                                     params={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 403
    finally:
        _cleanup(db, supplier_ids=[supplier.id])


# ── Producer canonicalisation (A4) ──────────────────────────────────────────
#
# Scrum 31 shipped scoring off raw `Supplier.name` and disclosed the gap on
# every response. Its stated blocker — a canonical producer master — shipped as
# unit 8, so the re-point is what these cover, along with the three cases where
# the raw supplier row must stay the unit of scoring.

def _producer(db, name: str, *spellings: str) -> Producer:
    """A canonical company plus the alias rows that spell it.

    Built through the service's own helpers rather than by hand: they own the
    normalisation and the `normalized` / `match_key` split, and a fixture that
    hand-rolls those is testing a shape production never writes.
    """
    pr, _ = get_or_create_producer(db, name, source="test")
    for spelling in (name, *spellings):
        upsert_alias(db, pr, spelling, source="test")
    db.commit()
    return pr


def _drop_producer(db, producer_id):
    bypass_rls_var.set(True)
    db.execute(text("DELETE FROM producer_aliases WHERE producer_id = :i"),
               {"i": str(producer_id)})
    db.execute(text("DELETE FROM producers WHERE id = :i"), {"i": str(producer_id)})
    db.commit()


def test_two_spellings_of_one_company_are_scored_together(db, tenant_a, client_as):
    """The defect this closes: a team holding the same company under two
    supplier rows scored it as two unrelated suppliers, each on half the
    history."""
    second = f"BASF SE-{uuid.uuid4().hex[:6]}"
    pr = _producer(db, f"BASF-{uuid.uuid4().hex[:6]}", second)
    s1 = _mk_supplier(db, tenant_a["team_id"], pr.name)
    s2 = _mk_supplier(db, tenant_a["team_id"], second)
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "P-pool")
    cm1 = _mk_flat_cost_model(db, tenant_a["team_id"], tenant_a["user_id"], product.id, s1.id)
    cm2 = _mk_flat_cost_model(db, tenant_a["team_id"], tenant_a["user_id"], product.id, s2.id)
    # Two quarters each: neither row reaches MIN_QUARTERS alone, both do pooled.
    _add_prices(db, cm1.id, tenant_a["user_id"], [110, 115])
    _add_prices(db, cm2.id, tenant_a["user_id"], [120, 125], start_year=2025)
    c = client_as(tenant_a)
    try:
        r = c.post(f"/api/suppliers/{s1.id}/trust-score/compute",
                   params={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 200, r.text
        [row] = r.json()
        assert row["insufficient_data"] is False, (
            "pooled history should clear the threshold neither row clears alone")
        assert row["inputs"]["n_quarters"] == 4
        assert row["resolution"] == "producer"
        assert row["inputs"]["producer_id"] == str(pr.id)
        assert sorted(row["inputs"]["pooled_supplier_ids"]) == sorted([s1.id, s2.id])

        # The sibling reports the same company's score, not its own half.
        r2 = c.post(f"/api/suppliers/{s2.id}/trust-score/compute",
                    params={"team_id": str(tenant_a["team_id"])})
        assert r2.json()[0]["score"] == row["score"]
    finally:
        _cleanup(db, [cm1.id, cm2.id], [product.id], [s1.id, s2.id])
        _drop_producer(db, pr.id)


def test_one_spelling_is_not_relabelled_as_canonicalised(db, tenant_a, client_as):
    """Nothing was pooled, so nothing was resolved. Claiming `producer` here
    would overstate what happened on the great majority of rows and make the
    flag useless for telling the two situations apart."""
    pr = _producer(db, f"Solo-{uuid.uuid4().hex[:6]}")
    supplier = _mk_supplier(db, tenant_a["team_id"], pr.name)
    product = _mk_product(db, tenant_a["team_id"], tenant_a["user_id"], "P-solo")
    cm = _mk_flat_cost_model(db, tenant_a["team_id"], tenant_a["user_id"], product.id, supplier.id)
    _add_prices(db, cm.id, tenant_a["user_id"], [110, 115, 120, 125])
    c = client_as(tenant_a)
    try:
        r = c.post(f"/api/suppliers/{supplier.id}/trust-score/compute",
                   params={"team_id": str(tenant_a["team_id"])})
        [row] = r.json()
        assert row["resolution"] == "raw_supplier_name"
        assert "pooled_supplier_ids" not in row["inputs"]
    finally:
        _cleanup(db, [cm.id], [product.id], [supplier.id])
        _drop_producer(db, pr.id)


def test_a_name_asserting_several_companies_is_not_canonicalised(db, tenant_a):
    """`resolve_raw_name` returns a list because 40 raw names in the drop name
    more than one company. A supplier row naming two cannot have its quoting
    behaviour attributed to either, and taking the first would be a guess
    presented as a fact — so it is absent from the map entirely."""
    a = _producer(db, f"Alpha-{uuid.uuid4().hex[:6]}")
    b = _producer(db, f"Beta-{uuid.uuid4().hex[:6]}")
    supplier = _mk_supplier(db, tenant_a["team_id"], f"{a.name} / {b.name}")
    try:
        assert supplier.id not in team_producer_map(db, tenant_a["team_id"])
    finally:
        _cleanup(db, supplier_ids=[supplier.id])
        _drop_producer(db, a.id)
        _drop_producer(db, b.id)


def test_an_unmapped_name_stays_at_raw_grain_and_mints_nothing(db, tenant_a):
    """Resolution runs with `create=False`, so a recompute can never mutate the
    shared producer master. Without that, any team's supplier spellings would
    pollute a platform table as a side effect of scoring."""
    before = db.query(Producer).count()
    supplier = _mk_supplier(db, tenant_a["team_id"],
                            f"Never-Heard-Of-{uuid.uuid4().hex[:8]}")
    try:
        assert supplier.id not in team_producer_map(db, tenant_a["team_id"])
        assert db.query(Producer).count() == before, "scoring must not mint producers"
    finally:
        _cleanup(db, supplier_ids=[supplier.id])


def test_canonicalisation_never_crosses_a_team(db, tenant_a, tenant_b):
    """`Producer` is a platform table and `Supplier.team_id` is NOT NULL. Two
    teams buying from the same company must not pool histories — that would put
    one tenant's negotiating record into another's score, which is the worst bug
    this module could ship."""
    pr = _producer(db, f"Shared-{uuid.uuid4().hex[:6]}")
    s_a = _mk_supplier(db, tenant_a["team_id"], pr.name)
    s_b = _mk_supplier(db, tenant_b["team_id"], pr.name)
    try:
        map_a = team_producer_map(db, tenant_a["team_id"])
        map_b = team_producer_map(db, tenant_b["team_id"])
        assert s_b.id not in map_a
        assert s_a.id not in map_b
        # Both resolve to the same company — the isolation is the team filter,
        # not a failure to resolve.
        assert map_a.get(s_a.id) == map_b.get(s_b.id) == pr.id
    finally:
        _cleanup(db, supplier_ids=[s_a.id, s_b.id])
        _drop_producer(db, pr.id)
