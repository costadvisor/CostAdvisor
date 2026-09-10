"""Three-layer index model (Wave 3, SCRUM-74 / DB-5 + DB-6).

Asserts the structural guarantees the layer exists to provide:

* a type-code's full chain to its series is reachable in one query;
* several cards can share one series (keying by series would lose cards);
* the three resolution states stay distinguishable, and only `ambiguous`
  may lack a target;
* monthly is the stored grain and quarterly derives from it;
* the widened constraints accept the values that used to be rejected;
* none of it disturbs the existing costing path.

Row counts are never asserted — the drop's own README is explicit that they
move while the shape does not.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import bypass_rls_var
from app.models.formula_template import FormulaTemplate, FormulaTemplateComponent
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexCard, IndexMonthlyValue, TypeCode


def _mk_series(db, key: str, **kwargs) -> CommodityIndex:
    ci = CommodityIndex(
        name=f"S-{key}", commodity_key=key, unit="index", scrape_enabled=False,
        value_kind="index_level", base_period="2023-01", **kwargs
    )
    db.add(ci)
    db.commit()
    return ci


def _mk_code(db, code: str, series: CommodityIndex | None, resolution="resolved", **kwargs) -> TypeCode:
    tc = TypeCode(
        code=code,
        resolves_to_id=series.id if series else None,
        resolution=resolution,
        **kwargs,
    )
    db.add(tc)
    db.commit()
    return tc


def _cleanup(db, series_ids=(), code_ids=(), template_ids=()):
    bypass_rls_var.set(True)
    for tid in template_ids:
        db.execute(text("DELETE FROM formula_templates WHERE id = :id"), {"id": str(tid)})
    for cid in code_ids:
        db.execute(text("DELETE FROM type_codes WHERE id = :id"), {"id": cid})
    for sid in series_ids:
        db.execute(text("DELETE FROM index_monthly_values WHERE commodity_id = :id"), {"id": sid})
        db.execute(text("DELETE FROM index_cards WHERE commodity_id = :id"), {"id": sid})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :id"), {"id": sid})
    db.commit()


# ── The resolution chain ─────────────────────────────────────────────────────

def test_type_code_chain_to_series_is_one_query(db):
    """DB-5's headline requirement: a code's full chain to its series is
    retrievable in a single join, not reassembled in memory."""
    key = f"brent-{uuid.uuid4().hex[:6]}"
    series = _mk_series(db, key)
    code = _mk_code(db, f"BR-{uuid.uuid4().hex[:6]}", series, label="Brent crude")
    try:
        row = (
            db.query(TypeCode.code, CommodityIndex.commodity_key, CommodityIndex.value_kind)
            .join(CommodityIndex, CommodityIndex.id == TypeCode.resolves_to_id)
            .filter(TypeCode.id == code.id)
            .one()
        )
        assert row.commodity_key == key
        assert row.value_kind == "index_level"
    finally:
        _cleanup(db, [series.id], [code.id])


def test_many_codes_resolve_to_one_series(db):
    """The concentration the layer exists to expose — 60 codes reach Brent in
    the real data. Reverse lookup must return them all."""
    series = _mk_series(db, f"cpo-{uuid.uuid4().hex[:6]}")
    codes = [_mk_code(db, f"C{i}-{uuid.uuid4().hex[:6]}", series) for i in range(3)]
    try:
        found = db.query(TypeCode).filter(TypeCode.resolves_to_id == series.id).all()
        assert len(found) == 3
    finally:
        _cleanup(db, [series.id], [c.id for c in codes])


def test_no_series_still_names_its_target(db):
    """`no_series` means the target has no NUMBERS, not that there is no
    target — so it keeps a real FK. Collapsing it with `ambiguous` loses the
    difference between "we know what this needs and can't price it" and "we
    don't know what this is"."""
    series = _mk_series(db, f"elec-{uuid.uuid4().hex[:6]}")
    code = _mk_code(db, f"NS-{uuid.uuid4().hex[:6]}", series, resolution="no_series")
    try:
        assert code.resolves_to_id == series.id
        assert not code.is_priceable
    finally:
        _cleanup(db, [series.id], [code.id])


def test_only_ambiguous_may_lack_a_target(db):
    """The DB refuses a targetless code in any other state, so a genuine load
    failure cannot pass as a known one."""
    ambiguous = _mk_code(db, f"AMB-{uuid.uuid4().hex[:6]}", None, resolution="ambiguous")
    try:
        assert ambiguous.resolves_to_id is None
    finally:
        _cleanup(db, code_ids=[ambiguous.id])

    with pytest.raises(IntegrityError):
        _mk_code(db, f"BAD-{uuid.uuid4().hex[:6]}", None, resolution="no_series")
    db.rollback()


def test_resolution_vocabulary_is_enforced(db):
    with pytest.raises(IntegrityError):
        _mk_code(db, f"X-{uuid.uuid4().hex[:6]}", None, resolution="probably")
    db.rollback()


# ── The card layer ───────────────────────────────────────────────────────────

def test_several_cards_share_one_series(db):
    """A card is not a series: 132 cards sit over 121 series in the drop, and
    keying the app by series would silently lose 11 of them."""
    series = _mk_series(db, f"brent-{uuid.uuid4().hex[:6]}")
    slug = f"crude-{uuid.uuid4().hex[:6]}"
    cards = [
        IndexCard(feed_key=f"{slug}|{region}", feed_slug=slug,
                  commodity_id=series.id, region=region, is_default_region=True)
        for region in ("EU", "NA", "Global")
    ]
    db.add_all(cards)
    db.commit()
    try:
        found = db.query(IndexCard).filter(IndexCard.commodity_id == series.id).all()
        assert len(found) == 3
        # is_default_region is deliberately NOT unique per slug — 18 slugs in
        # the drop carry several defaults, one of them four.
        assert sum(1 for c in found if c.is_default_region) == 3
    finally:
        _cleanup(db, [series.id])


def test_card_region_accepts_the_non_regions(db):
    """`multi` and `Global` are not regions, and the stub cards have none at
    all — which is why this column is not an FK to regions.code."""
    series = _mk_series(db, f"eth-{uuid.uuid4().hex[:6]}")
    slug = f"eth-{uuid.uuid4().hex[:6]}"
    db.add_all([
        IndexCard(feed_key=f"{slug}|multi", feed_slug=slug, commodity_id=series.id, region="multi"),
        IndexCard(feed_key=slug, feed_slug=slug, commodity_id=series.id, region=None),
    ])
    db.commit()
    try:
        regions = {c.region for c in db.query(IndexCard).filter(IndexCard.commodity_id == series.id)}
        assert regions == {"multi", None}
    finally:
        _cleanup(db, [series.id])


# ── The monthly grain ────────────────────────────────────────────────────────

def test_quarterly_derives_from_monthly(db):
    """Monthly is stored; quarterly is computed. Storing both would be two
    sources of one truth — and the drop's quarterly files reproduce exactly
    from its monthly ones."""
    series = _mk_series(db, f"m-{uuid.uuid4().hex[:6]}")
    db.add_all([
        IndexMonthlyValue(commodity_id=series.id, year=2026, month=m, value=100 + m, kind="actual")
        for m in (1, 2, 3)
    ])
    db.commit()
    try:
        rows = db.query(IndexMonthlyValue).filter(IndexMonthlyValue.commodity_id == series.id).all()
        assert {r.quarter for r in rows} == {1}
        assert sum(float(r.value) for r in rows) / 3 == 102.0
    finally:
        _cleanup(db, [series.id])


def test_actual_and_forecast_stay_separable(db):
    """The source README is explicit that the two must never land in the same
    average, so `kind` is NOT NULL and every aggregate can filter on it."""
    series = _mk_series(db, f"f-{uuid.uuid4().hex[:6]}")
    db.add_all([
        IndexMonthlyValue(commodity_id=series.id, year=2026, month=6, value=100, kind="actual"),
        IndexMonthlyValue(commodity_id=series.id, year=2026, month=7, value=180, kind="forecast"),
    ])
    db.commit()
    try:
        actuals = db.query(IndexMonthlyValue).filter(
            IndexMonthlyValue.commodity_id == series.id,
            IndexMonthlyValue.kind == "actual",
        ).all()
        assert [float(r.value) for r in actuals] == [100.0]
    finally:
        _cleanup(db, [series.id])


def test_month_and_kind_are_constrained(db):
    series = _mk_series(db, f"c-{uuid.uuid4().hex[:6]}")
    try:
        db.add(IndexMonthlyValue(commodity_id=series.id, year=2026, month=13, value=1, kind="actual"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(IndexMonthlyValue(commodity_id=series.id, year=2026, month=1, value=1, kind="guess"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        _cleanup(db, [series.id])


# ── The cost-line link ───────────────────────────────────────────────────────

def test_component_can_name_a_type_code_without_losing_commodity_id(db, tenant_a):
    """Additive: the new link sits beside the existing one, so the costing
    engine — which resolves via commodity_id — is untouched."""
    series = _mk_series(db, f"link-{uuid.uuid4().hex[:6]}")
    code = _mk_code(db, f"LK-{uuid.uuid4().hex[:6]}", series)
    template = FormulaTemplate(
        team_id=tenant_a["team_id"], created_by=tenant_a["user_id"],
        name=f"tpl-{uuid.uuid4().hex[:6]}", expression=None,
    )
    db.add(template)
    db.commit()
    try:
        db.add(FormulaTemplateComponent(
            template_id=template.id, name="Feedstock", component_type="index",
            commodity_id=series.id, type_code_id=code.id, weight_pct=100,
        ))
        db.commit()

        line = db.query(FormulaTemplateComponent).filter(
            FormulaTemplateComponent.template_id == template.id
        ).one()
        assert line.commodity_id == series.id   # the costing path still resolves
        assert line.type_code_id == code.id     # and the chain is now joinable
    finally:
        _cleanup(db, [series.id], [code.id], [template.id])


def test_existing_components_need_no_type_code(db, tenant_a):
    """Every hand-built formula predates the drop and has no type code — the
    column has to be optional or the migration breaks them."""
    series = _mk_series(db, f"old-{uuid.uuid4().hex[:6]}")
    template = FormulaTemplate(
        team_id=tenant_a["team_id"], created_by=tenant_a["user_id"],
        name=f"tpl-{uuid.uuid4().hex[:6]}", expression=None,
    )
    db.add(template)
    db.commit()
    try:
        db.add(FormulaTemplateComponent(
            template_id=template.id, name="Legacy", component_type="index",
            commodity_id=series.id, weight_pct=100,
        ))
        db.commit()
        line = db.query(FormulaTemplateComponent).filter(
            FormulaTemplateComponent.template_id == template.id
        ).one()
        assert line.type_code_id is None
    finally:
        _cleanup(db, [series.id], template_ids=[template.id])


# ── The widened constraints ──────────────────────────────────────────────────

def test_widened_columns_accept_the_values_that_used_to_be_rejected(db):
    """Both of these are real strings from the drop that the shipped column
    widths refused — a load-stopping failure with no obvious cause."""
    agency = "ICIS (directional commentary only — subscription required for full data)"
    cadence = "Quarterly (NA/EU) · Annual (CN/IN/MEA/LA/APAC)"
    assert len(agency) > 64 and len(cadence) > 16

    series = _mk_series(db, f"w-{uuid.uuid4().hex[:6]}", provider=agency, frequency=cadence)
    try:
        fetched = db.query(CommodityIndex).filter(CommodityIndex.id == series.id).one()
        assert fetched.provider == agency
        assert fetched.frequency == cadence
    finally:
        _cleanup(db, [series.id])


def test_frequency_vocabulary_covers_the_drops_cadences():
    """The constant is what validation checks against; leaving it narrow made
    otherwise-valid rows unloadable."""
    from app.constants.index_metadata import ACCESS_TIERS, FREQUENCIES

    for cadence in ("Semi-annual", "Unknown", "Daily/Monthly", "Daily/Weekly news"):
        assert cadence in FREQUENCIES
    assert "Proxy" in ACCESS_TIERS


def test_commodity_key_is_unique_but_optional(db):
    """Pre-drop series were never part of that vocabulary, so the key has to
    be nullable — while still refusing two series claiming the same key."""
    key = f"dup-{uuid.uuid4().hex[:6]}"
    first = _mk_series(db, key)
    try:
        db.add(CommodityIndex(name=f"other-{uuid.uuid4().hex[:6]}", commodity_key=key))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        # Two keyless series coexist happily.
        a = CommodityIndex(name=f"n1-{uuid.uuid4().hex[:6]}")
        b = CommodityIndex(name=f"n2-{uuid.uuid4().hex[:6]}")
        db.add_all([a, b])
        db.commit()
        _cleanup(db, [a.id, b.id])
    finally:
        _cleanup(db, [first.id])
