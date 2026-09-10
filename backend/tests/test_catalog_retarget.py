"""Catalog retarget (Wave 3, SCRUM-74/3b).

Integration tests against the real drop — skipped without it.

The guarantees under test are mostly about what the loader must NOT do:
overwrite an expert sign-off, wipe a value the drop does not state, delete
recipes the drop never claimed to own, or let two variants of a formula
overwrite each other. Row counts are not asserted; the drop's own README is
explicit they move.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, text

from app.database import bypass_rls_var
from app.models.formula_template import (
    FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_layer import TypeCode
from app.services.drop import drop_available, read_table
from app.services.drop.authority import find_margin_line, resolve_margin
from app.services.drop.catalog_loader import REGION_MAP, load_catalog

needs_drop = pytest.mark.skipif(
    not drop_available(), reason="costadvisor-data drop not present in this checkout"
)

pytestmark = needs_drop


def _ensure_loaded(db):
    bypass_rls_var.set(True)
    report = load_catalog(db)
    db.commit() if report.changed else db.rollback()
    return report


def _drop_template_codes() -> set[str]:
    return {r["formula_id"] for r in read_table("formulas")}


# ── The loader's contract ────────────────────────────────────────────────────

def test_second_run_changes_nothing(db):
    _ensure_loaded(db)
    again = load_catalog(db)
    db.rollback()
    assert again.changed == 0, again.render()


def test_the_flat_cohort_is_skipped_with_a_reason(db):
    """17 combos carry their region inside the formula_id and have no platform
    template. Reported by name, never invented."""
    _ensure_loaded(db)
    report = load_catalog(db)
    db.rollback()

    skipped = report.table("formula_region_coverage").skipped
    assert len(skipped) == 17
    for key, why in skipped:
        assert "no platform template" in why
        assert "·" in key, "the flat cohort bakes its region into the id"


def test_regions_are_mapped_not_passed_through(db):
    """The drop's codes are its own vocabulary; ours are the `regions` table."""
    _ensure_loaded(db)
    drop_codes = {c["region"] for c in read_table("combos")}
    assert drop_codes <= set(REGION_MAP), "every drop region needs a mapping"

    # Nothing landed under a raw drop code.
    loaded = {r[0] for r in db.query(FormulaRegionCoverage.region).distinct()}
    assert "EU" not in loaded and "CN" not in loaded and "GL" not in loaded
    assert {"Europe", "China", "GLOBAL"} & loaded


# ── What must survive a reload ───────────────────────────────────────────────

def test_an_expert_sign_off_survives(db):
    """The guarantee the previous seeder gave and this one must keep: coverage
    is upserted, and review state is not in the field set at all."""
    _ensure_loaded(db)
    codes = _drop_template_codes()
    coverage = (
        db.query(FormulaRegionCoverage)
        .join(FormulaTemplate, FormulaTemplate.id == FormulaRegionCoverage.template_id)
        .filter(FormulaTemplate.code.in_(codes))
        .first()
    )
    original = (coverage.reviewed_by, coverage.reviewed_at, coverage.needs_review)
    coverage.reviewed_by = "expert@test.local"
    coverage.reviewed_at = func.now()
    coverage.needs_review = False
    coverage.provenance = "human_approved"
    db.commit()
    try:
        load_catalog(db)
        db.flush()
        db.expire_all()
        after = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.id == coverage.id
        ).one()
        assert after.reviewed_by == "expert@test.local"
        assert after.reviewed_at is not None
        assert after.provenance == "human_approved"
    finally:
        db.rollback()
        db.expire_all()
        row = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.id == coverage.id
        ).one()
        row.reviewed_by, row.reviewed_at, row.needs_review = original
        row.provenance = "imported"
        db.commit()


def test_a_value_the_drop_does_not_state_is_not_wiped(db):
    """`basis_currency` is blank on every row because the decision form is
    unfilled. Writing the blank would null out a hand-set currency on the
    strength of a source nobody has completed — absent is not empty."""
    _ensure_loaded(db)
    codes = _drop_template_codes()
    coverage = (
        db.query(FormulaRegionCoverage)
        .join(FormulaTemplate, FormulaTemplate.id == FormulaRegionCoverage.template_id)
        .filter(FormulaTemplate.code.in_(codes))
        .first()
    )
    coverage.base_price = 1234.5
    coverage.currency = "EUR"
    db.commit()
    try:
        load_catalog(db)
        db.flush()
        db.expire_all()
        after = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.id == coverage.id
        ).one()
        assert after.currency == "EUR"
        assert float(after.base_price) == 1234.5
    finally:
        db.rollback()
        db.expire_all()
        row = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.id == coverage.id
        ).one()
        row.base_price = None
        row.currency = None
        db.commit()


def test_templates_the_drop_does_not_cover_are_untouched(db):
    """The drop covers some of the library and is silent about the rest.
    Deleting on silence would destroy recipes another source still owns."""
    _ensure_loaded(db)
    codes = _drop_template_codes()

    others = (
        db.query(FormulaTemplate.id)
        .filter(FormulaTemplate.team_id.is_(None), FormulaTemplate.code.isnot(None))
        .filter(~FormulaTemplate.code.in_(codes))
        .subquery()
    )
    before_cov = db.query(func.count(FormulaRegionCoverage.id)).filter(
        FormulaRegionCoverage.template_id.in_(db.query(others.c.id))
    ).scalar()
    before_lines = db.query(func.count(FormulaTemplateComponent.id)).filter(
        FormulaTemplateComponent.template_id.in_(db.query(others.c.id))
    ).scalar()
    assert before_cov > 0 and before_lines > 0, "expected uncovered templates to have data"

    load_catalog(db)
    db.flush()
    after_cov = db.query(func.count(FormulaRegionCoverage.id)).filter(
        FormulaRegionCoverage.template_id.in_(db.query(others.c.id))
    ).scalar()
    after_lines = db.query(func.count(FormulaTemplateComponent.id)).filter(
        FormulaTemplateComponent.template_id.in_(db.query(others.c.id))
    ).scalar()
    db.rollback()

    assert (after_cov, after_lines) == (before_cov, before_lines)


def test_template_level_lines_are_left_alone(db):
    """Region-NULL lines are the set the API authors. The drop knows nothing
    about them, so the orphan sweep must not reach them."""
    _ensure_loaded(db)
    code = sorted(_drop_template_codes())[0]
    template = db.query(FormulaTemplate).filter(
        FormulaTemplate.code == code, FormulaTemplate.team_id.is_(None)
    ).first()

    api_line = FormulaTemplateComponent(
        template_id=template.id, region=None, name="API authored",
        component_type="fixed", weight_pct=100,
    )
    db.add(api_line)
    db.commit()
    try:
        load_catalog(db)
        db.flush()
        assert db.query(FormulaTemplateComponent).filter(
            FormulaTemplateComponent.id == api_line.id
        ).one_or_none() is not None
        db.rollback()
    finally:
        db.rollback()
        db.execute(text("DELETE FROM formula_template_components WHERE id = :i"),
                   {"i": str(api_line.id)})
        db.commit()


def test_a_stray_region_line_is_swept(db):
    """A region-tagged set the drop does not mention is removed, not left to
    shadow the real one — the failure mode that let the pre-variant line sets
    survive invisibly."""
    _ensure_loaded(db)
    code = sorted(_drop_template_codes())[0]
    template = db.query(FormulaTemplate).filter(
        FormulaTemplate.code == code, FormulaTemplate.team_id.is_(None)
    ).first()

    stray = FormulaTemplateComponent(
        template_id=template.id, region="Oceania", name="Stray",
        component_type="fixed", weight_pct=100,
    )
    db.add(stray)
    db.commit()
    try:
        report = load_catalog(db)
        db.flush()
        assert report.table("formula_template_components").deleted >= 1
        assert db.query(FormulaTemplateComponent).filter(
            FormulaTemplateComponent.id == stray.id
        ).one_or_none() is None
    finally:
        db.rollback()
        db.execute(text("DELETE FROM formula_template_components WHERE id = :i"),
                   {"i": str(stray.id)})
        db.commit()


# ── The variant dimension ────────────────────────────────────────────────────

def test_variant_combos_keep_separate_recipes(db):
    """Two variants of a formula are different recipes. Keyed on
    (template, region) alone they overwrote each other on every run — the
    loader reported 20 created and 40 deleted in perpetuity."""
    _ensure_loaded(db)
    variants = (
        db.query(FormulaRegionCoverage)
        .filter(FormulaRegionCoverage.variant != "")
        .all()
    )
    assert variants, "expected the drop's variant combos"

    for coverage in variants:
        lines = db.query(func.count(FormulaTemplateComponent.id)).filter(
            FormulaTemplateComponent.template_id == coverage.template_id,
            FormulaTemplateComponent.region == coverage.region,
            FormulaTemplateComponent.variant == coverage.variant,
        ).scalar()
        assert lines > 0, f"{coverage.variant} has no lines of its own"

    # And no orphan variant='' set shadowing them.
    for coverage in variants:
        shadow = db.query(func.count(FormulaTemplateComponent.id)).filter(
            FormulaTemplateComponent.template_id == coverage.template_id,
            FormulaTemplateComponent.region == coverage.region,
            FormulaTemplateComponent.variant == "",
        ).scalar()
        assert shadow == 0


def test_the_uniqueness_key_includes_variant(db, tenant_a):
    """Two variants may share (template, region); an exact duplicate may not.
    `variant` is NOT NULL DEFAULT '' precisely so the second half holds —
    Postgres treats every NULL as distinct."""
    from sqlalchemy.exc import IntegrityError

    tpl = FormulaTemplate(
        team_id=tenant_a["team_id"], created_by=tenant_a["user_id"],
        name=f"v-{uuid.uuid4().hex[:6]}", expression=None,
    )
    db.add(tpl)
    db.commit()
    try:
        db.add_all([
            FormulaRegionCoverage(template_id=tpl.id, region="Europe", variant="treated"),
            FormulaRegionCoverage(template_id=tpl.id, region="Europe", variant="untreated"),
        ])
        db.commit()

        db.add(FormulaRegionCoverage(template_id=tpl.id, region="Europe", variant="treated"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.rollback()
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM formula_templates WHERE id = :i"), {"i": str(tpl.id)})
        db.commit()


# ── The authority rules, as loaded ───────────────────────────────────────────

def test_margin_is_loaded_from_the_line_not_the_header(db):
    """Verified against a combo where the two actually disagree — the header
    is stale, and the line is right because the weights close at exactly 100."""
    _ensure_loaded(db)
    lines_by_combo: dict[str, list[dict]] = {}
    for line in read_table("combo_lines"):
        lines_by_combo.setdefault(line["combo_id"], []).append(line)

    templates = {
        t.code: t.id
        for t in db.query(FormulaTemplate).filter(FormulaTemplate.team_id.is_(None))
    }

    checked = 0
    for combo in read_table("combos"):
        resolved = resolve_margin(combo, lines_by_combo.get(combo["combo_id"], []))
        if not resolved.disagrees:
            continue
        template_id = templates.get(combo["formula_id"])
        region = REGION_MAP.get(combo["region"])
        if template_id is None or region is None:
            continue
        coverage = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.template_id == template_id,
            FormulaRegionCoverage.region == region,
            FormulaRegionCoverage.variant == (combo.get("variant") or ""),
        ).one_or_none()
        if coverage is None:
            continue
        assert float(coverage.margin_pct) == resolved.line_value
        assert float(coverage.margin_pct) != resolved.header_value
        checked += 1
        if checked >= 5:
            break
    assert checked > 0, "expected loaded combos where header and line disagree"


def test_both_proxy_readings_are_stored(db):
    """Neither source is authoritative, so the line's reading lands on the
    component and the registry's on the type code. Picking one would move a
    large slice of the library's proxy exposure silently."""
    _ensure_loaded(db)
    disagreeing = (
        db.query(FormulaTemplateComponent, TypeCode)
        .join(TypeCode, TypeCode.id == FormulaTemplateComponent.type_code_id)
        .filter(FormulaTemplateComponent.line_proxy_status.isnot(None))
        .filter(FormulaTemplateComponent.line_proxy_status != TypeCode.proxy_status)
        .first()
    )
    assert disagreeing is not None, "expected the known proxy_status conflict to survive"
    component, type_code = disagreeing
    assert component.line_proxy_status != type_code.proxy_status
    # Both legible from one row.
    assert component.line_proxy_status in {"direct", "proxy", "unclassified"}
    assert type_code.proxy_status in {"direct", "proxy", "unclassified"}


def test_unclassified_survives_where_the_boolean_cannot_hold_it(db):
    """`is_proxy` folds three source values into two. The string column is why
    `unclassified` is not silently reported as "not a proxy"."""
    _ensure_loaded(db)
    unclassified = db.query(FormulaTemplateComponent).filter(
        FormulaTemplateComponent.line_proxy_status == "unclassified"
    ).first()
    assert unclassified is not None
    assert unclassified.is_proxy is False   # the lossy view
    assert unclassified.line_proxy_status == "unclassified"  # the full one


def test_proxy_density_tier_is_separate_from_coverage_tier(db):
    """Two different measurements — "how weak is the weakest input" and "how
    much of this recipe leans on stand-ins". One column could hold only one."""
    _ensure_loaded(db)
    tiers = {
        r[0] for r in db.query(FormulaRegionCoverage.proxy_density_tier).distinct()
        if r[0] is not None
    }
    assert tiers <= {"P1", "P2", "P3"} and tiers


# ── The payoff: unit 4's diagnosis now has real data ─────────────────────────

def test_combo_diagnosis_now_returns_real_answers(db):
    """Before the retarget, every combo reported "no lines carry a type-code
    link yet". The link is what makes the resolution chain reach a recipe."""
    from app.services.resolution import diagnose_combo

    _ensure_loaded(db)
    linked = (
        db.query(FormulaRegionCoverage)
        .join(
            FormulaTemplateComponent,
            (FormulaTemplateComponent.template_id == FormulaRegionCoverage.template_id)
            & (FormulaTemplateComponent.region == FormulaRegionCoverage.region),
        )
        .filter(FormulaTemplateComponent.type_code_id.isnot(None))
        .first()
    )
    assert linked is not None

    d = diagnose_combo(db, linked.template_id, linked.region)
    assert d.type_coded_lines > 0
    assert d.reason != "no lines carry a type-code link yet — not analysable"


def test_some_combos_are_blocked_and_name_their_lines(db):
    """The unpriceable combos in the drop should now explain themselves —
    naming the line and the specific reason, not a bare flag."""
    from app.services.resolution import diagnose_combo

    _ensure_loaded(db)
    blocked = None
    for coverage in db.query(FormulaRegionCoverage).limit(400):
        d = diagnose_combo(db, coverage.template_id, coverage.region)
        if d.blocking_lines:
            blocked = d
            break

    assert blocked is not None, "expected at least one unpriceable combo"
    for line in blocked.blocking_lines:
        assert line.line_name
        assert line.reason in {"no_series", "ambiguous", "resolved_but_no_history"}
        assert line.type_code and line.type_code in line.detail
    assert blocked.blocked_weight_pct > 0
