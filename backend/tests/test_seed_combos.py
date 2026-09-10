"""SEED-2 combo loader (Scrum 60).

Covers the done-when criteria:
- All 676 combos load as components; per-formula counts match tier-lookup.
- Sample should-costs come out within tolerance of expected (resolver
  reproduces the source weight totals, chained combos included).
- CONF-LOW rows are flagged; correction_plan_log rides as review metadata.
- The lines_html parser has tests against the current markup — and against
  the markup shifting shape, which it has done before.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

import seed_combos as sc
from app.services.formula_resolver import flatten_components, resolve_coverage


# ── Parser: current markup ────────────────────────────────────────────────────

# Verbatim shape from the 2026-06-30 drop (db_formula_combinations.html).
CURRENT_MARKUP = (
    '<div class="cl"><div class="wt-num">+62.1%</div>'
    '<div class="wt-bar-bg"><div style="width:62px;height:5px;"></div></div>'
    '<div class="cl-label">CPO feedstock [CONF-HIGH]</div>'
    '<span class="cl-idx idx-proxy">CPO-MY</span></div>'
    '<div class="cl"><div class="wt-num">+8%</div>'
    '<div class="wt-bar-bg"><div style="width:8px;height:5px;"></div></div>'
    '<div class="cl-label">Industrial electricity</div>'
    '<span class="cl-idx idx-direct">ELEC-EU</span></div>'
    '<div class="cl"><div class="wt-num">+9%</div>'
    '<div class="wt-bar-bg"><div style="width:9px;height:5px;"></div></div>'
    '<div class="cl-label">Supplier margin</div>'
    '<span class="cl-idx idx-fixed">fixed</span></div>'
)

EXPECTED_LINES = [
    {"weight_pct": 62.1, "label": "CPO feedstock", "code": "CPO-MY", "kind": "proxy"},
    {"weight_pct": 8.0, "label": "Industrial electricity", "code": "ELEC-EU", "kind": "direct"},
    {"weight_pct": 9.0, "label": "Supplier margin", "code": "fixed", "kind": "fixed"},
]


def test_parser_current_markup():
    lines, problems = sc.parse_lines_html(CURRENT_MARKUP)
    assert problems == []
    assert lines == EXPECTED_LINES


def test_parser_strips_confidence_bracket_and_sign():
    lines, _ = sc.parse_lines_html(
        '<div class="cl"><div class="wt-num">-3.5%</div>'
        '<div class="cl-label">By-product credit [CONF-MED]</div>'
        '<span class="cl-idx idx-direct">BENZ-EU</span></div>')
    assert lines == [{"weight_pct": -3.5, "label": "By-product credit",
                      "code": "BENZ-EU", "kind": "direct"}]


# ── Parser: the markup shifting shape (it has before) ────────────────────────

def test_parser_survives_reformatted_markup():
    """Tag names, attribute order, extra classes/wrappers, and whitespace all
    changed — the class tokens are the only stable contract."""
    shifted = '''
      <section class="row cl extra">
        <span data-x="1" class="big wt-num">  +62.1% </span>
        <p class="cl-label main">CPO feedstock [CONF-HIGH]</p>
        <em><b class="idx-proxy cl-idx">CPO-MY</b></em>
      </section>
      <section class="cl">
        <span class="wt-num">+8%</span>
        <p class="cl-label">Industrial electricity</p>
        <b class="cl-idx idx-direct">ELEC-EU</b>
      </section>
      <section class="cl">
        <span class="wt-num">+9%</span>
        <p class="cl-label">Supplier margin</p>
        <b class="cl-idx idx-fixed">fixed</b>
      </section>'''
    lines, problems = sc.parse_lines_html(shifted)
    assert problems == []
    assert lines == EXPECTED_LINES


def test_parser_survives_missing_line_container():
    """Even if the .cl wrapper disappears, a new wt-num starts a new line."""
    flat = ('<span class="wt-num">+60%</span><i class="cl-label">A</i>'
            '<b class="cl-idx idx-direct">X-EU</b>'
            '<span class="wt-num">+40%</span><i class="cl-label">B</i>'
            '<b class="cl-idx idx-fixed">fixed</b>')
    lines, problems = sc.parse_lines_html(flat)
    assert problems == []
    assert [(l["label"], l["weight_pct"], l["kind"]) for l in lines] == [
        ("A", 60.0, "direct"), ("B", 40.0, "fixed")]


def test_parser_reports_problems():
    lines, problems = sc.parse_lines_html(
        '<div class="cl"><div class="wt-num">lots</div>'
        '<div class="cl-label">Bad weight</div>'
        '<span class="cl-idx idx-direct">X</span></div>')
    assert lines == [] and any("unparseable weight" in p for p in problems)
    lines, problems = sc.parse_lines_html(
        '<div class="cl"><div class="wt-num">+50%</div>'
        '<div class="cl-label">No idx tag</div></div>')
    assert lines == [] and any("missing/unknown index tag" in p for p in problems)
    lines, problems = sc.parse_lines_html("<div>nothing here</div>")
    assert lines == [] and any("no cost lines" in p for p in problems)


# ── Validation ────────────────────────────────────────────────────────────────

def _combo(fid="OLE-A", region="EU", conf="CONF-HIGH", lines_html=None, combo_id=None):
    return {
        "combo_id": combo_id or f"{fid}·{region}",
        "formula_id": fid, "family": "F01 Oleochemicals", "subfamily": "Fatty acids",
        "region": region, "margin": 9, "data_confidence": conf,
        "coverage_tier": "free", "reviewed_by": None, "reviewed_at": None,
        "lines_html": lines_html or CURRENT_MARKUP,
    }


CODES = {"CPO-MY": "Crude palm oil", "ELEC-EU": "Industrial electricity", "BENZ-EU": "Benzene"}


def test_validate_count_mismatch_vs_tier_lookup():
    tier = {"OLE-A": {"n_combos": 2, "data_confidence": "CONF-HIGH"}}
    errors, _, _ = sc.validate([_combo()], tier, {}, CODES)
    assert any("tier-lookup says 2" in e for e in errors)


def test_validate_weight_sum_tolerance():
    bad = CURRENT_MARKUP.replace("+62.1%", "+10%")  # sums to 27
    tier = {"OLE-A": {"n_combos": 1, "data_confidence": "CONF-HIGH"}}
    errors, _, _ = sc.validate([_combo(lines_html=bad)], tier, {}, CODES)
    assert any("weights sum to 27.00" in e for e in errors)


def test_validate_unknown_code_and_duplicate():
    tier = {"OLE-A": {"n_combos": 2, "data_confidence": "CONF-HIGH"}}
    dup = [_combo(), _combo(combo_id="OLE-A·EU-2")]
    errors, _, _ = sc.validate(dup, tier, {}, {"ELEC-EU": "Industrial electricity"})
    assert any("Duplicate combo" in e for e in errors)
    assert any("unknown index/formula code 'CPO-MY'" in e for e in errors)


def test_validate_chain_cycle():
    a = _combo(fid="OLE-A", lines_html=(
        '<div class="cl"><div class="wt-num">+100%</div>'
        '<div class="cl-label">B input</div><span class="cl-idx idx-proxy">OLE-B</span></div>'))
    b = _combo(fid="OLE-B", lines_html=(
        '<div class="cl"><div class="wt-num">+100%</div>'
        '<div class="cl-label">A input</div><span class="cl-idx idx-proxy">OLE-A</span></div>'))
    tier = {"OLE-A": {"n_combos": 1, "data_confidence": "CONF-HIGH"},
            "OLE-B": {"n_combos": 1, "data_confidence": "CONF-HIGH"}}
    errors, _, _ = sc.validate([a, b], tier, {}, CODES)
    assert any("Circular formula chain" in e for e in errors)


# ── End-to-end against the real drop + dev DB ─────────────────────────────────

# ── Superseded by the catalog retarget (Scrum 74/3b) ─────────────────────────
#
# `seed_combos` loads the older 257-formula/676-combo drop into
# formula_region_coverage and formula_template_components. The 2026-07 drop now
# owns those tables (app/services/drop/catalog_loader.py), and two loaders
# cannot both be authoritative for the same rows — run either one and it
# overwrites the other's recipes for the 340 overlapping templates.
#
# This is the Phase-0 "retarget or retire" decision, resolved as retire: the
# seeder and its parser logic stay (the parser tests below still exercise real
# markup handling, and the module documents how the previous drop was shaped),
# but the tests that WRITE to the shared catalog tables are skipped so the two
# loaders stop fighting over the same rows on every suite run.
_SUPERSEDED = pytest.mark.skip(
    reason="seed_combos superseded by the 2026-07 catalog retarget "
           "(app/services/drop/catalog_loader.py) — both own the same tables"
)

@_SUPERSEDED
def test_load_completeness_and_idempotency(db):
    report = sc.run(db, dry_run=False, verbose=False)
    db.commit()
    assert report["combos"] == 676 and report["lines"] == 3806
    assert report["conf_low"] == 99

    # Second run: nothing changes.
    report2 = sc.run(db, dry_run=False, verbose=False)
    db.commit()
    for key in ("coverage", "components", "subfamily"):
        assert report2[key].created == 0 and report2[key].updated == 0, key

    # Per-formula counts match tier-lookup, and the whole thing totals 676.
    import json
    tier = json.loads(sc.resolve_file("formula_tier_lookup", ".json").read_text(encoding="utf-8"))
    rows = dict(db.execute(text("""
        SELECT t.code, count(*) FROM formula_region_coverage fc
        JOIN formula_templates t ON t.id = fc.template_id
        WHERE t.code IS NOT NULL GROUP BY t.code""")).fetchall())
    assert sum(rows.values()) == 676
    assert all(rows.get(fid, 0) == meta["n_combos"] for fid, meta in tier.items())


@_SUPERSEDED
def test_conf_low_flagged_and_correction_log_attached(db):
    sc.run(db, dry_run=False, verbose=False)
    db.commit()
    # Every CONF-LOW combo loads flagged for review — unless a human has since
    # signed it off in-app (the seeder preserves those sign-offs, so a real
    # review legitimately drops the flagged count below 99). The stable
    # invariant: all 99 CONF-LOW are either still flagged or already reviewed,
    # and nothing outside CONF-LOW is ever flagged.
    conf_low = db.execute(text(
        "SELECT count(*) FROM formula_region_coverage WHERE data_confidence = 'CONF-LOW'")).scalar()
    flagged = db.execute(text(
        "SELECT count(*) FROM formula_region_coverage WHERE needs_review")).scalar()
    reviewed_low = db.execute(text(
        "SELECT count(*) FROM formula_region_coverage "
        "WHERE data_confidence = 'CONF-LOW' AND reviewed_at IS NOT NULL")).scalar()
    non_low_flagged = db.execute(text(
        "SELECT count(*) FROM formula_region_coverage "
        "WHERE needs_review AND data_confidence <> 'CONF-LOW'")).scalar()
    assert conf_low == 99
    assert flagged + reviewed_low == 99
    assert non_low_flagged == 0
    # Every OLE-FAC-SAT combo carries its correction-plan entry for the reviewer.
    metas = db.execute(text("""
        SELECT fc.review_metadata FROM formula_region_coverage fc
        JOIN formula_templates t ON t.id = fc.template_id
        WHERE t.code = 'OLE-FAC-SAT'""")).fetchall()
    assert len(metas) == 6
    for (meta,) in metas:
        assert meta["correction_plan"]["action"] == "add_line"
        assert "source_combo_id" in meta


@_SUPERSEDED
def test_sample_should_costs_within_tolerance(db):
    """The resolver must reproduce the source recipe: effective weights sum to
    the source total (~100) and the margin matches the combo."""
    sc.run(db, dry_run=False, verbose=False)
    db.commit()

    def template_id(code):
        return uuid.UUID(db.execute(text(
            "SELECT id::text FROM formula_templates WHERE code = :c AND team_id IS NULL"),
            {"c": code}).scalar())

    # Plain combo
    tid = template_id("OLE-FAC-SAT")
    lines = flatten_components(db, tid, region="Europe")
    assert abs(sum(l["effective_weight_pct"] for l in lines) - 100.0) <= 0.75
    assert all(l["line_region"] == "Europe" for l in lines)
    cov, resolved = resolve_coverage(db, tid, "Europe")
    assert resolved == "Europe" and float(cov.margin_pct) == 9.0
    assert cov.data_confidence == "CONF-HIGH" and cov.needs_review is False

    # Chained combo: FOD-LEC-PWD pulls SOL-ACE-LIQ in as an input — nested
    # lines expand at depth 1 with multiplicatively scaled weights.
    tid2 = template_id("FOD-LEC-PWD")
    lines2 = flatten_components(db, tid2, region="Europe")
    assert {l["depth"] for l in lines2} == {0, 1}
    assert abs(sum(l["effective_weight_pct"] for l in lines2) - 100.0) <= 0.75

    # Subregion fallback: NWE resolves to the Europe line set.
    lines3 = flatten_components(db, tid, region="NWE")
    assert {l["line_region"] for l in lines3} == {"Europe"}


@_SUPERSEDED
def test_api_replace_leaves_seeded_region_lines_alone(db, tenant_a, client_as):
    """The API manages only the region-NULL set; a seeded per-region recipe
    must survive an API replace."""
    from app.models.formula_template import FormulaTemplate, FormulaTemplateComponent
    t = FormulaTemplate(team_id=tenant_a["team_id"], created_by=tenant_a["user_id"],
                        name="seeded-region-guard", expression=None)
    db.add(t)
    db.flush()
    db.add(FormulaTemplateComponent(template_id=t.id, region="Europe", name="seeded",
                                    component_type="fixed", weight_pct=100, sort_order=0))
    tid = t.id
    db.commit()

    r = client_as(tenant_a).put(f"/api/formulas/{tid}/components", json={"components": [
        {"name": "api line", "component_type": "fixed", "weight_pct": 100}]})
    assert r.status_code == 200

    rows = db.execute(text(
        "SELECT name, region FROM formula_template_components WHERE template_id = :t ORDER BY region NULLS FIRST"),
        {"t": str(tid)}).fetchall()
    assert ("api line", None) in rows and ("seeded", "Europe") in rows


@_SUPERSEDED
def test_reseed_preserves_expert_review(db):
    """A human sign-off (reviewed_at set) must survive a seed re-run — the
    source carries no review state, so it must never clobber one."""
    sc.run(db, dry_run=False, verbose=False)
    db.commit()
    combo = db.execute(text("""
        SELECT fc.id::text FROM formula_region_coverage fc
        JOIN formula_templates t ON t.id = fc.template_id
        WHERE fc.needs_review LIMIT 1""")).scalar()
    try:
        db.execute(text("""
            UPDATE formula_region_coverage
            SET needs_review = false, reviewed_by = 'expert@test', reviewed_at = now()
            WHERE id = :id"""), {"id": combo})
        db.commit()

        sc.run(db, dry_run=False, verbose=False)
        db.commit()
        row = db.execute(text("""
            SELECT needs_review, reviewed_by FROM formula_region_coverage
            WHERE id = :id"""), {"id": combo}).fetchone()
        assert row[0] is False and row[1] == "expert@test"
    finally:
        # Restore the seeded state so other tests see the canonical 99 flags.
        db.execute(text("""
            UPDATE formula_region_coverage
            SET needs_review = true, reviewed_by = NULL, reviewed_at = NULL
            WHERE id = :id"""), {"id": combo})
        db.commit()


def test_ensure_regions_idempotent(db):
    assert sc.ensure_regions(db, dry_run=False) == 0  # already created by the load
    india = db.execute(text(
        "SELECT p.code FROM regions r JOIN regions p ON p.id = r.parent_id WHERE r.code = 'India'")).scalar()
    assert india == "Asia"
