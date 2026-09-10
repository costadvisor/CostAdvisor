"""Resolution + concentration query API (Wave 3, SCRUM-74).

The acceptance criteria, as tests:

* a type-code's full chain, including one resolving through a proxy and one
  whose resolution is `no_series`;
* the reverse lookup on a series, with each code's cost-weight share;
* an unpriceable combo naming the blocking lines and the reason per line;
* `resolved` / `no_series` / `ambiguous` never collapsed.

The chain/concentration tests run against the loaded drop and skip without it.
The combo-diagnosis tests build their own fixtures, so they run either way —
which matters, because the catalog's type-code link is populated by the
retarget unit rather than by this one.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, text

from app.database import bypass_rls_var
from app.models.formula_template import (
    FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexMonthlyValue, TypeCode
from app.services.drop import drop_available
from app.services.drop.index_loader import load_index_layer
from app.services.resolution import (
    BLOCKER_AMBIGUOUS, BLOCKER_NO_HISTORY, BLOCKER_NO_SERIES, diagnose_combo,
)

needs_drop = pytest.mark.skipif(
    not drop_available(), reason="costadvisor-data drop not present in this checkout"
)


def _ensure_loaded(db):
    bypass_rls_var.set(True)
    report = load_index_layer(db)
    db.commit() if report.changed else db.rollback()


# ── Q1: the chain for one type code ──────────────────────────────────────────

@needs_drop
def test_chain_for_a_resolved_code(db, tenant_a, client_as):
    _ensure_loaded(db)
    code = db.query(TypeCode).filter(TypeCode.resolution == "resolved").first().code

    r = client_as(tenant_a).get(f"/api/resolution/type-codes/{code}")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["resolution"] == "resolved"
    assert body["series"] is not None
    assert body["series"]["commodity_key"]
    # Names WHICH proxy reading this is — the recipe line carries a second,
    # disagreeing one, so an unlabelled value would be ambiguous.
    assert body["proxy_status_source"] == "type_code_registry"
    assert body["history"]["actual_points"] >= 0


@needs_drop
def test_chain_for_a_proxy_backed_code(db, tenant_a, client_as):
    """An acceptance criterion in its own right: the chain has to work for a
    code reached through a stand-in, and say so."""
    _ensure_loaded(db)
    tc = db.query(TypeCode).filter(TypeCode.proxy_status == "proxy").first()
    assert tc is not None, "expected proxy-backed codes in the drop"

    body = client_as(tenant_a).get(f"/api/resolution/type-codes/{tc.code}").json()
    assert body["proxy_status"] == "proxy"
    assert body["series"] is not None


@needs_drop
def test_chain_for_a_no_series_code_keeps_its_target(db, tenant_a, client_as):
    """`no_series` means the target has no NUMBERS — so the chain still names
    the series it wanted, and the blocker says which problem it is."""
    _ensure_loaded(db)
    tc = db.query(TypeCode).filter(TypeCode.resolution == "no_series").first()
    assert tc is not None

    body = client_as(tenant_a).get(f"/api/resolution/type-codes/{tc.code}").json()
    assert body["resolution"] == "no_series"
    assert body["series"] is not None, "no_series still names its intended series"
    assert body["priceable"] is False
    assert body["blocker"] == BLOCKER_NO_SERIES


@needs_drop
def test_chain_for_an_ambiguous_code_has_no_series(db, tenant_a, client_as):
    """The state that must never be folded into `no_series`: nothing to point
    at, and a different fix (somebody decides what the code means)."""
    _ensure_loaded(db)
    tc = db.query(TypeCode).filter(TypeCode.resolution == "ambiguous").first()
    assert tc is not None

    body = client_as(tenant_a).get(f"/api/resolution/type-codes/{tc.code}").json()
    assert body["resolution"] == "ambiguous"
    assert body["series"] is None
    assert body["blocker"] == BLOCKER_AMBIGUOUS


@needs_drop
def test_the_three_states_stay_distinguishable(db, tenant_a, client_as):
    """Asserted across the whole library, not just one sample of each."""
    _ensure_loaded(db)
    seen = set()
    for tc in db.query(TypeCode).all():
        seen.add(tc.resolution)
    assert seen == {"resolved", "no_series", "ambiguous"}


def test_unknown_type_code_is_404(db, tenant_a, client_as):
    r = client_as(tenant_a).get("/api/resolution/type-codes/NOPE-NOT-A-CODE")
    assert r.status_code == 404


# ── Q2 + Q4: the dependents of one series ────────────────────────────────────

@needs_drop
def test_reverse_lookup_returns_codes_with_weight_share(db, tenant_a, client_as):
    """The core reverse question. Uses whichever series is most concentrated,
    so the assertion holds as the drop's numbers move."""
    _ensure_loaded(db)
    top = (
        db.query(CommodityIndex.commodity_key)
        .join(TypeCode, TypeCode.resolves_to_id == CommodityIndex.id)
        .group_by(CommodityIndex.commodity_key)
        .order_by(func.count(TypeCode.id).desc())
        .first()
    )

    body = client_as(tenant_a).get(f"/api/resolution/series/{top.commodity_key}").json()
    assert body["totals"]["type_code_count"] > 1
    assert body["type_codes"], "expected the codes resolving here"

    shares = [c["weight_share_of_series_pct"] for c in body["type_codes"] if c["weight_share_of_series_pct"]]
    assert shares and abs(sum(shares) - 100) < 1.0, "per-code shares should account for the series"
    assert body["totals"]["weight_share_of_library_pct"] > 0


@needs_drop
def test_a_series_reports_the_cards_that_display_it(db, tenant_a, client_as):
    """A card is not a series — the chain has to fan out, or a consumer keying
    by series loses cards."""
    _ensure_loaded(db)
    key = (
        db.query(CommodityIndex.commodity_key)
        .filter(CommodityIndex.commodity_key.isnot(None))
        .first()
    ).commodity_key
    body = client_as(tenant_a).get(f"/api/resolution/series/{key}").json()
    assert isinstance(body["cards"], list)


def test_unknown_series_is_404(db, tenant_a, client_as):
    r = client_as(tenant_a).get("/api/resolution/series/not-a-real-series")
    assert r.status_code == 404


# ── The library-wide view ────────────────────────────────────────────────────

@needs_drop
def test_concentration_surfaces_one_series_wearing_many_labels(db, tenant_a, client_as):
    """The finding that motivated the layer. Asserted as a relationship, since
    the drop's own figures will move."""
    _ensure_loaded(db)
    body = client_as(tenant_a).get("/api/resolution/concentration").json()

    assert body["library_total_weight"] > 0
    top = body["series"][0]
    assert top["type_code_count"] > 1
    assert top["weight_share_of_library_pct"] > 10, (
        f"top series carries only {top['weight_share_of_library_pct']}% — "
        "expected a materially concentrated library"
    )
    # Ranked descending, so a caller can trust the first row is the worst.
    shares = [s["weight_share_of_library_pct"] for s in body["series"]]
    assert shares == sorted(shares, reverse=True)


@needs_drop
def test_concentration_respects_limit(db, tenant_a, client_as):
    _ensure_loaded(db)
    body = client_as(tenant_a).get("/api/resolution/concentration?limit=3").json()
    assert len(body["series"]) <= 3


@needs_drop
def test_unpriceable_is_grouped_by_reason_not_totalled(db, tenant_a, client_as):
    """Three reasons, three different actions — buy a feed, decide what a code
    means, run a scrape. One combined count would hide which is which."""
    _ensure_loaded(db)
    body = client_as(tenant_a).get("/api/resolution/unpriceable").json()

    assert set(body["blockers"]) == {
        BLOCKER_NO_SERIES, BLOCKER_AMBIGUOUS, BLOCKER_NO_HISTORY,
    }
    no_series = body["blockers"][BLOCKER_NO_SERIES]
    assert no_series["code_count"] > 0
    # Weight is what makes the sourcing decision rankable (SCRUM-80's backlog).
    assert no_series["source_total_weight"] > 0
    assert no_series["weight_share_of_library_pct"] > 0
    # Each entry carries enough to act on.
    entry = no_series["codes"][0]
    assert "code" in entry and "source_total_weight" in entry


# ── Q3: why can't this combo be costed ───────────────────────────────────────
#
# Self-contained fixtures: the catalog's type-code link is populated by the
# retarget unit, so these build their own linked lines rather than depending on
# data that is not there yet.

def _series(db, key, *, with_history: bool) -> CommodityIndex:
    """A fixture series, deliberately WITHOUT `commodity_key`.

    `commodity_key` is the drop's namespace: setting it here would make a test
    fixture indistinguishable from loaded data, so the loader would try to
    reconcile it and the library-wide invariant tests would scan it. (That is
    not hypothetical — an earlier version of this helper set it, and left
    behind rows that failed `test_base_period_only_where_there_is_history` on
    a later run.) Nothing in the diagnosis path needs the key; it resolves by
    `commodity_id`.
    """
    ci = CommodityIndex(name=key, scrape_enabled=False)
    db.add(ci)
    db.commit()
    if with_history:
        db.add(IndexMonthlyValue(commodity_id=ci.id, year=2026, month=1, value=100, kind="actual"))
        db.commit()
    return ci


def _code(db, code, series, resolution="resolved") -> TypeCode:
    tc = TypeCode(
        code=code, resolution=resolution,
        resolves_to_id=series.id if series else None,
    )
    db.add(tc)
    db.commit()
    return tc


def _combo(db, tenant, region="Europe"):
    tpl = FormulaTemplate(
        team_id=tenant["team_id"], created_by=tenant["user_id"],
        name=f"tpl-{uuid.uuid4().hex[:6]}", code=f"C-{uuid.uuid4().hex[:6]}", expression=None,
    )
    db.add(tpl)
    db.commit()
    db.add(FormulaRegionCoverage(template_id=tpl.id, region=region, base_price=1000))
    db.commit()
    return tpl


def _cleanup(db, template_ids=(), code_ids=(), series_ids=()):
    # Roll back first: a test that failed mid-transaction leaves the session
    # unusable, and then cleanup itself fails and the rows leak into the next
    # run. Learned the hard way.
    db.rollback()
    bypass_rls_var.set(True)
    for tid in template_ids:
        db.execute(text("DELETE FROM formula_templates WHERE id = :i"), {"i": str(tid)})
    for cid in code_ids:
        db.execute(text("DELETE FROM type_codes WHERE id = :i"), {"i": cid})
    for sid in series_ids:
        db.execute(text("DELETE FROM index_monthly_values WHERE commodity_id = :i"), {"i": sid})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": sid})
    db.commit()


def test_a_healthy_combo_is_priceable(db, tenant_a):
    good = _series(db, f"ok-{uuid.uuid4().hex[:6]}", with_history=True)
    code = _code(db, f"OK-{uuid.uuid4().hex[:6]}", good)
    tpl = _combo(db, tenant_a)
    try:
        db.add(FormulaTemplateComponent(
            template_id=tpl.id, region="Europe", name="Feedstock",
            component_type="index", commodity_id=good.id, type_code_id=code.id,
            weight_pct=90,
        ))
        db.add(FormulaTemplateComponent(
            template_id=tpl.id, region="Europe", name="Supplier margin",
            component_type="fixed", weight_pct=10,
        ))
        db.commit()

        d = diagnose_combo(db, tpl.id, "Europe")
        assert d.priceable
        assert d.blocking_lines == []
        assert d.type_coded_lines == 1   # the fixed line has nothing to resolve
    finally:
        _cleanup(db, [tpl.id], [code.id], [good.id])


def test_each_blocking_line_is_named_with_its_own_reason(db, tenant_a):
    """The acceptance criterion: the specific lines AND the specific reason
    per line — never collapsed into one 'unpriceable'.

    Both `commodity_id` and `type_code_id` are set on each line, which is what
    the catalog retarget will produce: `combo_lines` carries `type_code` *and*
    `commodity_key`, so the line records both what the source named and where
    it resolves. (`ck_ftc_target_coherence` also requires it — see the
    ambiguous-line test below for the case that does not fit.)
    """
    ok = _series(db, f"ok-{uuid.uuid4().hex[:6]}", with_history=True)
    dry = _series(db, f"dry-{uuid.uuid4().hex[:6]}", with_history=False)

    good = _code(db, f"G-{uuid.uuid4().hex[:6]}", ok)
    no_series = _code(db, f"NS-{uuid.uuid4().hex[:6]}", dry, resolution="no_series")
    no_history = _code(db, f"NH-{uuid.uuid4().hex[:6]}", dry)  # resolved, but dry

    tpl = _combo(db, tenant_a)
    try:
        for name, code, series, weight in [
            ("Healthy feedstock", good, ok, 50),
            ("Unbought feed", no_series, dry, 30),
            ("Awaiting scrape", no_history, dry, 20),
        ]:
            db.add(FormulaTemplateComponent(
                template_id=tpl.id, region="Europe", name=name,
                component_type="index", commodity_id=series.id,
                type_code_id=code.id, weight_pct=weight,
            ))
        db.commit()

        d = diagnose_combo(db, tpl.id, "Europe")
        assert not d.priceable
        by_reason = {b.reason: b for b in d.blocking_lines}
        assert set(by_reason) == {BLOCKER_NO_SERIES, BLOCKER_NO_HISTORY}

        # Named lines, not just counts.
        assert by_reason[BLOCKER_NO_SERIES].line_name == "Unbought feed"
        assert by_reason[BLOCKER_NO_HISTORY].line_name == "Awaiting scrape"
        # Each detail names the offending code, so it is actionable.
        assert no_series.code in by_reason[BLOCKER_NO_SERIES].detail

        # And how much of the recipe is blocked — the healthy 50% excluded.
        assert d.blocked_weight_pct == 50.0
        assert d.reason and "50" in d.reason
    finally:
        _cleanup(db, [tpl.id], [good.id, no_series.id, no_history.id], [ok.id, dry.id])


def test_an_ambiguous_line_can_be_stored_and_diagnosed(db, tenant_a):
    """An `ambiguous` type code resolves to nothing, so a cost line naming one
    has no `commodity_id` to record.

    `ck_ftc_target_coherence` originally required every `index` line to carry a
    commodity, which made those lines unstorable — this test asserted exactly
    that, as a finding handed to the catalog retarget. Scrum 74/3b relaxed the
    constraint (an index line may be identified by a commodity OR a type code),
    so the assertion is now the other way round: the row goes in, and the
    diagnosis names it.

    The real drop has 25 such lines across three parent-feed codes
    (`natural-gas`, `crude-oil`, `acrylic-acid`). Without the relaxation a load
    had to drop them and misreport every recipe containing them.
    """
    ambiguous = _code(db, f"AM-{uuid.uuid4().hex[:6]}", None, resolution="ambiguous")
    tpl = _combo(db, tenant_a)
    try:
        db.add(FormulaTemplateComponent(
            template_id=tpl.id, region="Europe", name="Undecided code",
            component_type="index", type_code_id=ambiguous.id, weight_pct=100,
        ))
        db.commit()

        d = diagnose_combo(db, tpl.id, "Europe")
        assert not d.priceable
        assert [b.reason for b in d.blocking_lines] == [BLOCKER_AMBIGUOUS]
        assert d.blocking_lines[0].line_name == "Undecided code"
        assert d.blocking_lines[0].type_code == ambiguous.code
    finally:
        _cleanup(db, [tpl.id], [ambiguous.id])


def test_lines_without_a_type_code_read_as_not_analysable(db, tenant_a):
    """The honest distinction: an empty blocker list must not look like "all
    fine" when it actually means "nothing linked yet". The catalog retarget
    populates that link."""
    series = _series(db, f"leg-{uuid.uuid4().hex[:6]}", with_history=True)
    tpl = _combo(db, tenant_a)
    try:
        db.add(FormulaTemplateComponent(
            template_id=tpl.id, region="Europe", name="Legacy line",
            component_type="index", commodity_id=series.id, weight_pct=100,
        ))
        db.commit()

        d = diagnose_combo(db, tpl.id, "Europe")
        assert d.total_lines == 1
        assert d.type_coded_lines == 0
        assert d.priceable is False
        assert "not analysable" in d.reason
    finally:
        _cleanup(db, [tpl.id], series_ids=[series.id])


def test_missing_coverage_and_missing_lines_are_distinct_answers(db, tenant_a):
    tpl = _combo(db, tenant_a)
    try:
        # Coverage exists for Europe but not APAC.
        no_cov = diagnose_combo(db, tpl.id, "APAC")
        assert no_cov.coverage_exists is False
        assert "no coverage row" in no_cov.reason

        empty = diagnose_combo(db, tpl.id, "Europe")
        assert empty.coverage_exists is True
        assert empty.reason == "combo has no cost lines"
    finally:
        _cleanup(db, [tpl.id])


def test_unknown_template_is_reported_not_crashed(db, tenant_a):
    d = diagnose_combo(db, uuid.uuid4(), "Europe")
    assert d.coverage_exists is False
    assert d.reason == "formula template not found"


def test_combo_endpoint_returns_the_diagnosis(db, tenant_a, client_as):
    tpl = _combo(db, tenant_a)
    try:
        r = client_as(tenant_a).get(f"/api/resolution/combos/{tpl.id}/Europe")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["template_id"] == str(tpl.id)
        assert body["coverage_exists"] is True
        assert body["priceable"] is False
    finally:
        _cleanup(db, [tpl.id])


# ── The surface is platform-grain ────────────────────────────────────────────

def test_endpoints_need_no_team_id(db, tenant_a, client_as):
    """These answer "what does the platform library depend on", so unlike
    /api/indexes/{id}/impact they take no team_id and return no tenant rows."""
    for path in ("/api/resolution/concentration", "/api/resolution/unpriceable"):
        r = client_as(tenant_a).get(path)
        assert r.status_code == 200, f"{path}: {r.text}"


def test_authentication_is_still_required(client):
    for path in ("/api/resolution/concentration", "/api/resolution/type-codes/X"):
        assert client.get(path).status_code == 401
