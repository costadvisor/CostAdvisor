"""The catalogue loader (app/services/content_drop/catalogue.py).

One platform template per card (records, groups, card-only keys; shells
skipped) with its kind, supply status and product line; one coverage row per
combo, one component per cost line, graded by the existing trust assessment;
the placements' `template_id` back-filled; a second load changes nothing. The
existing engine (`intelligence.derive`) computes a card from what is stored.

Every expected figure is read from the drop at test time, mostly through the
independent reading in `tests/content_drop_expect.py`; no count, name or text
from the drop is written here. The module works in one transaction and rolls
it back. If the taxonomy or the index layer is missing, the fixture loads it
inside that same transaction.
"""
from __future__ import annotations

from collections import Counter, defaultdict

import pytest

from app.constants.trust import GRADE_MEDIUM, GRADE_SEVERITY, TRUST_GRADES
from app.database import SessionLocal, bypass_rls_var
from app.models.chemical_family import ChemicalFamily
from app.models.formula_template import (
    FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_data import CommodityIndex
from app.models.index_layer import TypeCode
from app.models.product_line import ProductLine
from app.models.taxonomy_v2 import CategoryPlacement
from app.models.user import User
from app.services.catalog_visibility import default_view_clause, listed_clause
from app.services.drop.common import REGION_MAP
from app.services.drop.report import LoadReport
from app.services.content_drop import catalogue, indexes, reader, taxonomy
from app.services.intelligence import derive
from app.services.trust import sign_off
from tests import content_drop_expect as expect
# Side-effect import: region auto-register (APAC / MEA on flush).
from app.services import regions as _region_events  # noqa: F401

# A product the recipe and engine checks follow. Its figures come from the drop.
FERRIC = "BCI-FECL3-LIQ"


def _records() -> dict:
    return reader.raw("FORMULA_COMBOS")


def _groups() -> dict:
    return reader.raw("AUTO_GROUPS")


def _combo(pid: str, region: str) -> dict:
    return next(c for c in _records()[pid]["combos"] if c["region"] == region)


def _total(report: LoadReport, table: str) -> int:
    diff = report.table(table)
    return diff.created + diff.updated + diff.unchanged


@pytest.fixture(scope="module")
def loaded():
    """One session and one transaction for the module: make sure the lines and
    type codes exist, load the catalogue twice, yield, roll back."""
    reader.drop_dir()
    token = bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        if db.query(ProductLine.id).first() is None:
            taxonomy.load(db, LoadReport(title="test prerequisite: taxonomy"))
        if db.query(TypeCode.id).first() is None:
            indexes.load(db, LoadReport(title="test prerequisite: indexes"))
        db.flush()
        first = catalogue.load(db, LoadReport(title="test catalogue 1"))
        db.flush()
        second = catalogue.load(db, LoadReport(title="test catalogue 2"))
        db.flush()
        yield db, first, second
    finally:
        db.rollback()
        db.close()
        bypass_rls_var.reset(token)


@pytest.fixture(scope="module")
def templates(loaded):
    db, _, _ = loaded
    return {t.code: t for t in db.query(FormulaTemplate).filter(
        FormulaTemplate.team_id.is_(None), FormulaTemplate.code.in_(expect.template_codes()))}


def _template(db, code: str) -> FormulaTemplate:
    return db.query(FormulaTemplate).filter(FormulaTemplate.team_id.is_(None),
                                            FormulaTemplate.code == code).one()


# ── Cards ────────────────────────────────────────────────────────────────────

def test_one_template_per_card(loaded, templates):
    db, first, _ = loaded
    assert sorted(templates) == expect.template_codes()
    assert _total(first, "formula_templates") >= len(templates)
    for code in expect.shells():
        assert db.query(FormulaTemplate).filter(FormulaTemplate.team_id.is_(None),
                                                FormulaTemplate.code == code).count() == 0


def test_kind_status_and_redirect_per_card(templates):
    want = expect.card_statuses()
    differ = [code for code, t in templates.items()
              if (t.card_kind, t.supply_status, t.supply_status_detail, t.redirect_to)
              != (want[code].kind, want[code].supply_status, want[code].detail,
                  want[code].redirect_to)]
    assert differ == []
    assert Counter(t.card_kind for t in templates.values()) == expect.kind_counts()
    assert Counter(t.supply_status for t in templates.values() if t.supply_status) == \
        expect.status_counts()


def test_listed_and_default_view(loaded):
    db, _, _ = loaded
    codes = set(expect.template_codes())
    listed = sorted(c for (c,) in db.query(FormulaTemplate.code).filter(
        FormulaTemplate.team_id.is_(None), listed_clause(FormulaTemplate)) if c in codes)
    default = sorted(c for (c,) in db.query(FormulaTemplate.code).filter(
        FormulaTemplate.team_id.is_(None), default_view_clause(FormulaTemplate)) if c in codes)
    assert listed == expect.listed_codes()
    assert default == expect.default_view_codes()


def test_every_template_sits_on_its_line_or_says_why_not(loaded, templates):
    db, _, _ = loaded
    keys = {r.id: r.line_key for r in db.query(ProductLine)}
    for code, t in templates.items():
        assert keys.get(t.product_line_id) == expect.template_line(code), code
    assert sorted(c for c, t in templates.items() if t.product_line_id is None) == \
        expect.templates_without_line()
    # A record key that is not a current line is kept for us, never served.
    off_axis = expect.off_axis_record_keys()
    for code, t in templates.items():
        assert (t.internal_meta or {}).get("record_line_key") == off_axis.get(code), code
    # The family is the record's (or the group's); a card-only key has none.
    families = {f.id: f.name for f in db.query(ChemicalFamily)}
    for code, t in templates.items():
        assert families.get(t.family_id) == expect.template_family(code), code


def test_card_only_keys_are_cards_with_no_formula(loaded, templates):
    db, _, _ = loaded
    for code in expect.card_only_codes():
        t = templates[code]
        assert t.name == code and t.family_id is None and t.product_line_id is None
        assert t.catalog_meta == {"source": catalogue.SOURCE_CARD, "regions": [],
                                  "region_count": 0, "combos": []}
        assert db.query(FormulaRegionCoverage).filter_by(template_id=t.id).count() == 0


def _keys_at_any_depth(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys_at_any_depth(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys_at_any_depth(v)


def test_catalog_meta_holds_structure_and_internal_meta_the_rest(templates):
    cards = reader.raw("CURATED_CONTENT")
    for code, t in templates.items():
        meta = t.catalog_meta or {}
        assert set(meta) <= set(catalogue.CATALOG_META_KEYS), code
        assert not any(str(k).startswith("_") for k in _keys_at_any_depth(meta)), code
        assert "route" not in {k for c in meta.get("combos") or [] for k in c}, code
        assert set(meta.get("pricing_gap") or {}) <= set(catalogue.PRICING_GAP_KEYS), code

        internal = t.internal_meta or {}
        rec = _records().get(code) or {}
        card = cards.get(code) or {}
        assert ("weights_rationale" in internal) == bool(rec.get("weights_rationale")), code
        assert ("line_moved" in internal) == bool(rec.get("line_moved")), code
        assert ("pricing_gap" in internal) == bool((rec.get("pricing_gap") or {}).get("why")), code
        assert ("combo_routes" in internal) == any(
            c.get("_route") for c in rec.get("combos") or []), code
        for name in ("grade_source", "refresh", "flags_review"):
            assert internal.get(name) == (card.get(name) or None), (code, name)
        assert t.reference_grade == (card.get("grade") or None), code
        assert t.archival_note == (card.get("_archival") or None), code


def test_product_metadata_comes_from_the_drop(templates):
    records, cas = _records(), reader.raw("CAS_LOOKUP")
    for code, t in templates.items():
        rec = records.get(code)
        if rec is None:
            continue
        combos = rec["combos"]
        assert t.name == rec["name"], code
        assert t.full_name == (rec.get("full") or None), code
        assert t.form == ((combos[0].get("form") or None) if combos else None), code
        assert t.volatile == (combos[0].get("volatile") if combos else None), code
        assert t.cas_number == (cas.get(code) or None), code
        assert t.is_group is False, code
        meta = t.catalog_meta
        assert meta["source"] == catalogue.SOURCE_RECORD
        assert meta["regions"] == list(dict.fromkeys(c["region"] for c in combos)), code
        assert meta["region_count"] == len(meta["regions"]), code
        assert meta.get("variant_overrides") == reader.raw("VARIANT_OVERRIDES").get(code), code


def test_coverage_and_cost_lines_match_the_drop(loaded, templates):
    db, first, _ = loaded
    ids = [t.id for t in templates.values()]
    assert db.query(FormulaRegionCoverage).filter(
        FormulaRegionCoverage.template_id.in_(ids)).count() == expect.coverage_count()
    assert db.query(FormulaTemplateComponent).filter(
        FormulaTemplateComponent.template_id.in_(ids),
        FormulaTemplateComponent.region.isnot(None)).count() == expect.cost_line_count()
    assert _total(first, "formula_region_coverage") == expect.coverage_count()
    assert _total(first, "formula_template_components") == expect.cost_line_count()
    assert db.query(FormulaRegionCoverage).filter(
        FormulaRegionCoverage.template_id.in_(ids),
        FormulaRegionCoverage.withdrawn_at.isnot(None)).count() == 0


def test_a_second_load_changes_nothing(loaded):
    _, _, second = loaded
    assert second.changed == 0, second.render()


def test_groups_are_cards_with_coverage_and_no_components(loaded, templates):
    db, _, _ = loaded
    groups = _groups()
    assert sorted(c for c, t in templates.items() if t.is_group) == sorted(groups)
    for code, group in groups.items():
        t = templates[code]
        combos = group["combos"]
        assert t.group_members == [reader.combo_pid(c["id"]) for c in combos]
        cov = db.query(FormulaRegionCoverage).filter_by(template_id=t.id).all()
        assert sorted(c.region for c in cov) == sorted(reader.app_region(c["region"])
                                                        for c in combos)
        # No structured lines in the drop, so nothing parsed from markup.
        assert db.query(FormulaTemplateComponent).filter_by(template_id=t.id).count() == 0
        by_region = {c.region: c for c in cov}
        for combo in combos:
            assert float(by_region[reader.app_region(combo["region"])].margin_pct) == \
                combo["margin"]
        live = {c["pid"]: c["live"] for c in t.catalog_meta["combos"]}
        assert live == {reader.combo_pid(c["id"]): reader.combo_pid(c["id"]) in _records()
                        for c in combos}


def test_absorbed_products_name_their_card(loaded, templates):
    absorbed = set(reader.raw("ABSORBED_FORMULA_IDS"))
    for code, t in templates.items():
        if t.absorbed_into:
            assert code in absorbed, code
            assert t.absorbed_into in templates, code
            assert t.catalog_meta.get("absorbed_via") in (
                catalogue.ABSORBED_VIA_GROUP, catalogue.ABSORBED_VIA_VARIANT), code
            if t.catalog_meta.get("absorbed_via") == catalogue.ABSORBED_VIA_GROUP:
                assert code in templates[t.absorbed_into].group_members, code
    members = {m for g in _groups().values() for m in (reader.combo_pid(c["id"]) for c in g["combos"])}
    for code in absorbed & members & set(_records()):
        assert templates[code].absorbed_into is not None, code


def test_ferric_chloride_eu_recipe_is_the_drops(loaded, templates):
    db, _, _ = loaded
    ferric = templates[FERRIC]
    combo = _combo(FERRIC, "EU")
    covered = reader.raw("FCOVERED")
    cov = db.query(FormulaRegionCoverage).filter_by(template_id=ferric.id, region="Europe",
                                                   variant="").one()
    assert cov.base_year == 2023 and cov.base_quarter == 1
    margin = next(w for w, _l, _t, kind in combo["lines"] if kind == "margin")
    assert float(cov.margin_pct) == margin
    assert cov.base_price is None and cov.currency is None
    rows = (db.query(FormulaTemplateComponent)
            .filter_by(template_id=ferric.id, region="Europe", variant="")
            .order_by(FormulaTemplateComponent.sort_order).all())
    keys = {c.id: c.commodity_key for c in db.query(CommodityIndex).filter(
        CommodityIndex.id.in_([r.commodity_id for r in rows if r.commodity_id]))}
    assert [(r.name, keys.get(r.commodity_id), float(r.weight_pct), r.cost_category)
            for r in rows] == [(label, covered.get(tag), float(w), kind)
                               for w, label, tag, kind in combo["lines"]]
    assert [r.sort_order for r in rows] == list(range(len(combo["lines"])))
    assert [r.component_type for r in rows] == [
        "index" if tag in covered else "fixed" for _w, _l, tag, _k in combo["lines"]]


def test_every_line_is_index_iff_its_tag_is_covered(loaded, templates):
    db, _, _ = loaded
    covered = reader.raw("FCOVERED")
    codes = {tc.id: tc for tc in db.query(TypeCode)}
    ids = {t.code: t.id for t in templates.values()}
    by_combo: dict[tuple, list] = defaultdict(list)
    for row in db.query(FormulaTemplateComponent).filter(
            FormulaTemplateComponent.template_id.in_(list(ids.values()))):
        if row.component_type == "index":
            assert row.type_code_id is not None and row.commodity_id is not None
            assert codes[row.type_code_id].resolves_to_id == row.commodity_id
            assert row.line_proxy_status == catalogue.LINE_PROXY_UNCLASSIFIED
        else:
            assert row.component_type == "fixed"
            assert row.type_code_id is None and row.commodity_id is None
            assert row.line_proxy_status is None
        assert row.is_proxy is False
        by_combo[(row.template_id, row.region, row.variant)].append(row)

    for pid, rec in _records().items():
        for combo in rec["combos"]:
            key = (ids[pid], reader.app_region(combo["region"]), combo.get("variant") or "")
            rows = sorted(by_combo[key], key=lambda r: r.sort_order)
            assert len(rows) == len(combo["lines"]), combo["id"]
            for row, (share, label, tag, kind) in zip(rows, combo["lines"]):
                assert (row.name, float(row.weight_pct), row.cost_category) == \
                    (label, float(share), kind)
                assert (row.component_type == "index") == (tag in covered)
                if tag in covered:
                    assert codes[row.type_code_id].code == tag


def test_margin_is_the_margin_line_and_a_disagreeing_header_is_kept(loaded, templates):
    db, _, _ = loaded
    cov = {(c.template_id, c.region, c.variant): c for c in db.query(FormulaRegionCoverage)}
    for pid, rec in _records().items():
        t = templates[pid]
        metas = {c["id"]: c for c in t.catalog_meta["combos"]}
        for combo in rec["combos"]:
            row = cov[(t.id, reader.app_region(combo["region"]), combo.get("variant") or "")]
            meta = metas[reader.repair_combo_id(combo["id"])]
            shares = [w for w, _l, _t, kind in combo["lines"] if kind == "margin"]
            if shares:
                assert float(row.margin_pct) == shares[0], combo["id"]
                if combo.get("margin") is not None and combo["margin"] != shares[0]:
                    assert meta["margin_header"] == combo["margin"], combo["id"]
                else:
                    assert "margin_header" not in meta, combo["id"]
            else:
                assert meta["margin_source"] in ("header", "absent"), combo["id"]


def test_escaped_combo_ids_and_regions_land_on_the_right_rows(loaded, templates):
    db, _, _ = loaded
    escaped = [(pid, c) for pid, rec in _records().items()
               for c in rec["combos"] if "\\u00b7" in c["id"]]
    for pid, combo in escaped:
        t = templates[pid]
        assert db.query(FormulaRegionCoverage).filter_by(
            template_id=t.id, region=reader.app_region(combo["region"]),
            variant=combo.get("variant") or "").count() == 1
        assert reader.repair_combo_id(combo["id"]) in {c["id"] for c in t.catalog_meta["combos"]}
    for t in templates.values():
        assert all("\\u00b7" not in c["id"] for c in t.catalog_meta.get("combos") or [])

    # Stored as the app's region codes; no drop code leaks.
    regions = {r for (r,) in db.query(FormulaRegionCoverage.region).filter(
        FormulaRegionCoverage.template_id.in_([t.id for t in templates.values()]))}
    drop_regions = {c["region"] for rec in list(_records().values()) + list(_groups().values())
                    for c in rec["combos"]}
    assert regions == {REGION_MAP[r] for r in drop_regions}


def test_variant_combos_keep_separate_recipes(loaded, templates):
    db, _, _ = loaded
    variants = [(pid, c) for pid, rec in _records().items() for c in rec["combos"]
                if c.get("variant")]
    assert variants
    for pid, combo in variants:
        rows = db.query(FormulaTemplateComponent).filter_by(
            template_id=templates[pid].id, region=reader.app_region(combo["region"]),
            variant=combo["variant"]).all()
        assert len(rows) == len(combo["lines"]), combo["id"]


def test_every_loaded_combo_is_graded(loaded, templates):
    db, first, _ = loaded
    rows = db.query(FormulaRegionCoverage, FormulaTemplate.is_group).join(
        FormulaTemplate, FormulaTemplate.id == FormulaRegionCoverage.template_id).filter(
        FormulaTemplate.id.in_([t.id for t in templates.values()])).all()
    assert len(rows) == expect.coverage_count()
    for cov, is_group in rows:
        assert cov.trust_grade in TRUST_GRADES and cov.trust_inputs is not None
        if is_group:
            assert cov.trust_grade == "unrated"
    graded = sum(first.table(f"{catalogue.TRUST_CHECK_PREFIX}{g}").unchanged
                 for g in TRUST_GRADES if first.table(f"{catalogue.TRUST_CHECK_PREFIX}{g}"))
    assert graded == expect.coverage_count()


def test_a_pricing_gap_or_margin_warning_caps_the_grade(loaded, templates):
    db, _, _ = loaded
    flagged = {pid: rec for pid, rec in _records().items()
               if isinstance(rec.get("pricing_gap"), dict) or rec.get("margin_status")}
    assert flagged
    for pid, rec in flagged.items():
        t = templates[pid]
        assert t.catalog_meta.get("pricing_gap", {}).get("status") == \
            (rec.get("pricing_gap") or {}).get("status")
        assert t.catalog_meta.get("margin_status") == rec.get("margin_status")
        for cov in db.query(FormulaRegionCoverage).filter_by(template_id=t.id):
            assert GRADE_SEVERITY[cov.trust_grade] <= GRADE_SEVERITY[GRADE_MEDIUM], pid
            assert cov.needs_review is True, pid
            reasons = {r["reason"] for r in cov.trust_inputs["reasons"]}
            if isinstance(rec.get("pricing_gap"), dict):
                assert catalogue.REASON_PRICING_GAP in reasons, pid
            if rec.get("margin_status"):
                assert catalogue.REASON_MARGIN_STATUS in reasons, pid
            # The record's own wording never reaches the stored reasons.
            why = (rec.get("pricing_gap") or {}).get("why")
            assert not why or why not in str(cov.trust_inputs), pid


def test_placements_point_at_their_template(loaded):
    db, _, _ = loaded
    ids = {t.code: t.id for t in db.query(FormulaTemplate).filter(
        FormulaTemplate.team_id.is_(None), FormulaTemplate.code.isnot(None))}
    rows = db.query(CategoryPlacement).all()
    assert len(rows) == len(expect.placements())
    for p in rows:
        assert p.template_id == ids.get(p.pid), p.pid
    # The taxonomy loader then computes the same value: no placement churn.
    again = taxonomy.load(db, LoadReport(title="taxonomy after catalogue"))
    db.flush()
    diff = again.table("category_placements")
    assert diff.created == diff.updated == diff.deleted == 0


# ── The engine ───────────────────────────────────────────────────────────────

def test_the_engine_derives_the_ferric_chloride_card(loaded, templates):
    db, _, _ = loaded
    covered = reader.raw("FCOVERED")
    combo = _combo(FERRIC, "EU")
    result = derive(db, templates[FERRIC].id, "Europe")
    assert result.evaluable, result.reason
    assert result.coverage_region == "Europe"
    assert (result.series[0]["year"], result.series[0]["quarter"]) == (2023, 1)
    assert result.series[0]["level"] == pytest.approx(100)
    assert [(c["name"], c["commodity_key"], c["weight_pct"]) for c in result.components] \
        == [(label, covered.get(tag), float(w)) for w, label, tag, _k in combo["lines"]]
    assert all(c["has_data"] for c in result.components)
    cov = db.query(FormulaRegionCoverage).filter_by(template_id=templates[FERRIC].id,
                                                   region="Europe").one()
    assert result.trust["grade"] == cov.trust_grade


def test_check_engine_counts_what_is_evaluable(loaded):
    db, _, _ = loaded
    group = next(code for code, g in sorted(_groups().items()) if g["combos"])
    n_group = len(_groups()[group]["combos"])
    n_ferric = len(_records()[FERRIC]["combos"])
    out = catalogue.check_engine(db, codes=[FERRIC, group])
    assert out["combos"] == n_ferric + n_group
    assert out["evaluable"] == n_ferric
    assert out["reasons"] == {"no weighted lines": n_group}


# ── Reload (runs last: it damages rows inside the module transaction) ────────

def test_a_reload_repairs_damage_and_keeps_what_it_does_not_own(loaded, templates):
    db, _, _ = loaded
    ferric = templates[FERRIC]
    own = {reader.app_region(c["region"]) for c in _records()[FERRIC]["combos"]}
    free = [r for r in REGION_MAP.values() if r not in own]
    assert len(free) >= 2
    stray_region, gone_region = free[0], free[1]
    eu = db.query(FormulaRegionCoverage).filter_by(template_id=ferric.id,
                                                  region="Europe").one()
    other = db.query(FormulaRegionCoverage).filter(
        FormulaRegionCoverage.template_id == ferric.id,
        FormulaRegionCoverage.region != "Europe").order_by(FormulaRegionCoverage.region).first()
    reviewer = db.query(User.id).filter(User.is_super_admin.is_(True)).first()[0]
    sign_off(db, other, reviewer)
    other.base_price = 812.5   # an admin's price: the drop states none
    other.currency = "EUR"

    ferric.name = "renamed"
    eu.margin_pct = 1
    eu_lines = len(_combo(FERRIC, "EU")["lines"])
    line = (db.query(FormulaTemplateComponent)
            .filter_by(template_id=ferric.id, region="Europe").order_by(
                FormulaTemplateComponent.sort_order).first())
    line.weight_pct = 99
    # A line set with no coverage row (deleted), a template-level (region
    # NULL) line (never the loader's), and a combo the drop does not have
    # (kept, withdrawn).
    db.add(FormulaTemplateComponent(template_id=ferric.id, region=stray_region, variant="",
                                    name="stray", component_type="fixed", weight_pct=100))
    db.add(FormulaTemplateComponent(template_id=ferric.id, region=None, variant="",
                                    name="api line", component_type="fixed", weight_pct=100))
    gone_cov = FormulaRegionCoverage(template_id=ferric.id, region=gone_region, variant="")
    db.add(gone_cov)
    db.flush()
    db.add(FormulaTemplateComponent(template_id=ferric.id, region=gone_region, variant="",
                                    name="kept", component_type="fixed", weight_pct=100))
    # A hand-made platform template (not the loader's) and a card the loader
    # wrote that the drop no longer has.
    db.add(FormulaTemplate(team_id=None, code="ZZ-HAND-MADE", name="hand made",
                           created_by=reviewer))
    db.add(FormulaTemplate(team_id=None, code="ZZ-GONE-CARD", name="gone", created_by=reviewer,
                           card_kind="product", supply_status="live",
                           catalog_meta={"source": catalogue.SOURCE_RECORD}))
    ghost = db.query(CategoryPlacement).filter_by(pid=FERRIC).first()
    if ghost is not None:
        ghost.template_id = None
    db.flush()

    repaired = catalogue.load(db, LoadReport(title="repair"))
    db.flush()
    t_diff = repaired.table("formula_templates")
    assert t_diff.updated == 2          # ferric's name, the gone card withdrawn
    assert t_diff.stale == 1            # the hand-made template
    cov_diff = repaired.table("formula_region_coverage")
    assert cov_diff.updated == 2        # EU margin, the gone combo withdrawn
    lines = repaired.table("formula_template_components")
    assert lines.deleted == eu_lines + 1 and lines.created == eu_lines
    assert repaired.table(catalogue.PLACEMENT_ROW).updated == int(ghost is not None)

    db.refresh(ferric)
    assert ferric.name == _records()[FERRIC]["name"]
    db.refresh(eu)
    assert float(eu.margin_pct) == next(
        w for w, _l, _t, kind in _combo(FERRIC, "EU")["lines"] if kind == "margin")
    assert db.query(FormulaTemplateComponent).filter_by(
        template_id=ferric.id, region=stray_region).count() == 0
    assert db.query(FormulaTemplateComponent).filter_by(
        template_id=ferric.id, region=None).count() == 1
    db.refresh(gone_cov)
    assert gone_cov.withdrawn_at is not None
    assert db.query(FormulaTemplateComponent).filter_by(
        template_id=ferric.id, region=gone_region).count() == 1
    assert _template(db, "ZZ-HAND-MADE").card_kind == "product"
    gone = _template(db, "ZZ-GONE-CARD")
    assert (gone.card_kind, gone.supply_status) == ("withdrawn", None)
    assert db.query(FormulaTemplate).filter(
        FormulaTemplate.code == "ZZ-GONE-CARD", listed_clause(FormulaTemplate)).count() == 0
    # The sign-off and the admin's price survive; the recipe they vouched for
    # did not move, so the fingerprint still matches.
    db.refresh(other)
    assert other.reviewed_at is not None and other.needs_review is False
    assert float(other.base_price) == 812.5 and other.currency == "EUR"

    settled = catalogue.load(db, LoadReport(title="after repair"))
    db.flush()
    assert settled.changed == 0, settled.render()
