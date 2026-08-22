"""SEED-1 catalog loader (Scrum 59).

Covers the done-when criteria:
- Running the loader twice changes no row counts (idempotent upsert by stable key).
- Updating one source value updates only that row.
- Taxonomy (22/91/257) + the 158 feeds load; retired/orphan rows are rejected.
- Join-validation shouts on bad references before anything is written.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

import seed_catalog as sc


# ── Pure parsing ──────────────────────────────────────────────────────────────

def test_parse_family_cell():
    assert sc.parse_family_cell("F01 Oleochemicals") == ("F01", "Oleochemicals")
    assert sc.parse_family_cell("F12 Base Chemicals & Intermediates") == (
        "F12", "Base Chemicals & Intermediates")
    with pytest.raises(ValueError):
        sc.parse_family_cell("Oleochemicals")  # no code prefix


def test_resolve_workbook_tolerates_handoff_suffix(tmp_path, monkeypatch):
    # Handoff copies arrive as "seed-data-reference (1).xlsx" — the resolver
    # must pick those up rather than tripping on the exact name.
    monkeypatch.setattr(sc, "DEFAULT_DIR", tmp_path)
    suffixed = tmp_path / "seed-data-reference (1).xlsx"
    suffixed.write_bytes(b"stub")
    assert sc.resolve_workbook() == suffixed
    exact = tmp_path / "seed-data-reference.xlsx"
    exact.write_bytes(b"stub")
    assert sc.resolve_workbook() == exact  # exact name wins when present


# ── Pre-import join-validation ────────────────────────────────────────────────

def _minimal_parsed(**overrides):
    parsed = {
        "families": {"F01": "Oleochemicals"},
        "subfamilies": [{"family_code": "F01", "name": "Fatty acids", "formula_count": 1}],
        "formulas": [{"code": "OLE-A", "name": "A", "family_code": "F01",
                      "form": "SAT", "coverage_tier": "free",
                      "data_confidence": "CONF-HIGH", "region_count": 1}],
        "feeds": [{"index_id": "IDX-X", "used_by": ["OLE-A"], "used_by_count": 1}],
    }
    parsed.update(overrides)
    return parsed


def test_validate_clean_minimal_source():
    errors, _ = sc.validate(_minimal_parsed())
    assert errors == []


def test_validate_flags_feed_referencing_unknown_formula():
    parsed = _minimal_parsed(
        feeds=[{"index_id": "IDX-X", "used_by": ["OLE-A", "GHOST-1"], "used_by_count": 2}])
    errors, _ = sc.validate(parsed)
    assert any("GHOST-1" in e and "unknown formula" in e for e in errors)


def test_validate_flags_formula_no_feed_prices():
    parsed = _minimal_parsed(formulas=_minimal_parsed()["formulas"] + [
        {"code": "OLE-B", "name": "B", "family_code": "F01", "form": "LIQ",
         "coverage_tier": "free", "data_confidence": "CONF-HIGH", "region_count": 1}])
    errors, _ = sc.validate(parsed)
    assert any("OLE-B" in e and "not priced" in e for e in errors)


def test_validate_flags_retired_orphan_index():
    parsed = _minimal_parsed(
        feeds=[{"index_id": "IDX-X", "used_by": ["OLE-A"], "used_by_count": 1},
               {"index_id": "IDX-CPO-CN", "used_by": [], "used_by_count": 0}])
    errors, _ = sc.validate(parsed)
    assert any("IDX-CPO-CN" in e and "Retired" in e for e in errors)


def test_validate_flags_unknown_family_and_duplicates():
    base = _minimal_parsed()
    errors, _ = sc.validate(_minimal_parsed(
        formulas=[dict(base["formulas"][0], family_code="F99")]))
    assert any("unknown family F99" in e for e in errors)
    errors, _ = sc.validate(_minimal_parsed(
        formulas=base["formulas"] + [dict(base["formulas"][0])]))
    assert any("Duplicate formula id" in e for e in errors)


def test_real_workbook_join_validates_clean():
    parsed = sc.parse_workbook(sc.resolve_workbook())
    errors, _ = sc.validate(parsed)
    assert errors == []
    # 2026-07 refreshed workbook. NB the Read-me tab still shows the old
    # 22/91/257/158 totals (not regenerated) — the data sheets are authoritative.
    assert len(parsed["families"]) == 22
    assert len(parsed["subfamilies"]) == 143
    assert len(parsed["formulas"]) == 367
    assert len(parsed["feeds"]) == 187


# ── End-to-end idempotency against the dev DB ─────────────────────────────────

def _catalog_counts(db):
    return db.execute(text("""SELECT
        (SELECT count(*) FROM chemical_families WHERE team_id IS NULL AND code IS NOT NULL),
        (SELECT count(*) FROM subfamilies WHERE team_id IS NULL),
        (SELECT count(*) FROM formula_templates WHERE team_id IS NULL AND code IS NOT NULL),
        (SELECT count(*) FROM commodity_indexes WHERE retrieval_status IS NOT NULL)
    """)).fetchone()


def test_load_twice_changes_no_row_counts(db):
    xlsx = sc.resolve_workbook()
    sc.run(db, xlsx, dry_run=False, verbose=False)
    db.commit()
    before = _catalog_counts(db)

    report = sc.run(db, xlsx, dry_run=False, verbose=False)
    db.commit()
    assert _catalog_counts(db) == before
    for key in ("families", "subfamilies", "formulas", "indexes"):
        tally = report[key]
        assert tally.created == [] and tally.updated == [], key


def test_update_one_source_value_updates_only_that_row(db):
    xlsx = sc.resolve_workbook()
    sc.run(db, xlsx, dry_run=False, verbose=False)
    db.commit()

    db.execute(text(
        "UPDATE chemical_families SET name = 'Surfactants-TYPO' WHERE code = 'F02' AND team_id IS NULL"))
    db.commit()
    report = sc.run(db, xlsx, dry_run=False, verbose=False)
    db.commit()

    assert len(report["families"].updated) == 1
    assert "F02" in report["families"].updated[0]
    for key in ("subfamilies", "formulas", "indexes"):
        assert report[key].created == [] and report[key].updated == [], key
    restored = db.execute(text(
        "SELECT name FROM chemical_families WHERE code = 'F02' AND team_id IS NULL")).scalar()
    assert restored == "Surfactants"


def test_formula_shells_carry_taxonomy_and_meta(db):
    sc.run(db, sc.resolve_workbook(), dry_run=False, verbose=False)
    db.commit()
    row = db.execute(text("""
        SELECT t.name, cf.code, t.catalog_meta->>'data_confidence',
               t.catalog_meta->>'form', t.catalog_meta->>'coverage_tier', t.expression
        FROM formula_templates t JOIN chemical_families cf ON cf.id = t.family_id
        WHERE t.code = 'OLE-FAC-SAT' AND t.team_id IS NULL""")).fetchone()
    assert row is not None
    name, fam_code, confidence, form, coverage, expression = row
    assert name == "Fatty acids saturated C16/C18"
    assert fam_code == "F01"
    # 2026-07 sheet dropped the Data-confidence column and reshaped Form/Coverage:
    # form is now a physical descriptor, coverage a derived P-tier.
    assert confidence is None
    assert form == "Liquid/flake"
    assert coverage == "P3 (proxy-heavy some)"
    assert expression is None  # weighted components are SEED-2, not a fake expression


def test_dry_run_writes_nothing(db):
    before = _catalog_counts(db)
    db.execute(text("DELETE FROM formula_templates WHERE code = 'ZZZ-TEST-DRY'"))
    db.commit()
    sc.run(db, sc.resolve_workbook(), dry_run=True, verbose=False)
    db.rollback()
    assert _catalog_counts(db) == before
