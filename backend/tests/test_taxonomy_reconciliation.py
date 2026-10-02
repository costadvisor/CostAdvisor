"""Phase 2 item 1 (post-Wave-3 roadmap) — taxonomy reconciliation decision
payload on the Scrum 27b sheet-roundtrip mechanism. Mirrors
test_dimensions.py's own decision-payload tests (registration shape, applying
creates the mapping, export lists only the undecided) rather than depending on
the real drop file's exact current contents — the drop CSV is monkeypatched to
a small, controlled fixture so the mismatch counts are deterministic here.
"""
import csv
import uuid

import pytest

from app.models.chemical_family import ChemicalFamily
from app.models.subfamily import Subfamily
from app.models.taxonomy_reconciliation import TaxonomyReconciliation
from app.services.sheet_roundtrip import get_spec
import app.services.sheet_roundtrip.taxonomy_reconciliation as tr_module


@pytest.fixture
def drop_csv(tmp_path, monkeypatch):
    """A small, controlled families.csv standing in for the real drop file,
    so tests never depend on its live contents changing out from under them."""
    path = tmp_path / "families.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["family", "subfamily", "n_formulas", "n_combos"])
        w.writerow(["Drop-Only Family", "Drop-Only Sub", "2", "5"])
        w.writerow(["Matched Family", "Drop-Only Sub Under Matched", "1", "3"])
        w.writerow(["Matched Family", "Matched Sub", "1", "2"])
    monkeypatch.setattr(tr_module, "_FAMILIES_CSV", path)
    return path


@pytest.fixture
def taxonomy_setup(db):
    """A platform family+subfamily that the drop's 'Matched Family'/'Matched
    Sub' rows should resolve onto by name."""
    suf = uuid.uuid4().hex[:6]
    fam = ChemicalFamily(name="Matched Family")
    db.add(fam)
    db.flush()
    sub = Subfamily(family_id=fam.id, name="Matched Sub")
    db.add(sub)
    db.flush()
    db.commit()
    yield fam, sub
    db.query(TaxonomyReconciliation).filter(
        TaxonomyReconciliation.drop_family.in_(["Drop-Only Family", "Matched Family"])
    ).delete(synchronize_session=False)
    db.query(Subfamily).filter(Subfamily.id == sub.id).delete(synchronize_session=False)
    db.query(ChemicalFamily).filter(ChemicalFamily.id == fam.id).delete(synchronize_session=False)
    db.commit()


def test_the_decision_payload_is_registered_on_the_shipped_mechanism():
    spec = get_spec("taxonomy_reconciliation")
    assert spec.permission_key == "content.edit"
    assert [c.name for c in spec.key_columns] == ["level", "drop_family", "drop_subfamily"]
    assert [c.name for c in spec.editable_columns] == ["maps_to_family", "maps_to_subfamily"]
    assert {c.name for c in spec.readonly_columns} == {"n_formulas", "n_combos"}


def test_export_lists_only_real_mismatches_ranked_by_impact(db, drop_csv, taxonomy_setup):
    spec = get_spec("taxonomy_reconciliation")
    rows = spec.query_rows(db, spec.filter_schema())

    keys = {(r["level"], r["drop_family"], r["drop_subfamily"]) for r in rows}
    # The genuinely unmatched family and its subfamily line.
    assert ("family", "Drop-Only Family", "") in keys
    assert ("subfamily", "Drop-Only Family", "Drop-Only Sub") in keys
    # A subfamily mismatch under an otherwise-matched family.
    assert ("subfamily", "Matched Family", "Drop-Only Sub Under Matched") in keys
    # The family that DOES match, and the subfamily pair that DOES match,
    # never appear — they're not mismatches.
    assert ("family", "Matched Family", "") not in keys
    assert ("subfamily", "Matched Family", "Matched Sub") not in keys

    # Undecided rows always export blank decisions — the diff is the answer.
    assert all(r["maps_to_family"] == "" for r in rows)
    assert all(r["maps_to_subfamily"] == "" for r in rows)

    # Ranked by real impact (n_combos desc), so an analyst works the top first.
    combos = [r["n_combos"] for r in rows]
    assert combos == sorted(combos, reverse=True)


def test_level_filter_narrows_to_one_granularity(db, drop_csv, taxonomy_setup):
    spec = get_spec("taxonomy_reconciliation")
    fam_rows = spec.query_rows(db, spec.filter_schema(level="family"))
    assert all(r["level"] == "family" for r in fam_rows)
    sub_rows = spec.query_rows(db, spec.filter_schema(level="subfamily"))
    assert all(r["level"] == "subfamily" for r in sub_rows)


def test_applying_maps_to_family_creates_the_alias_never_the_platform_row(db, drop_csv, taxonomy_setup):
    fam, sub = taxonomy_setup
    spec = get_spec("taxonomy_reconciliation")
    row_key = {"level": "family", "drop_family": "Drop-Only Family", "drop_subfamily": ""}

    assert spec.get_current_value(db, row_key, "maps_to_family") == ""

    # A name with no platform row is refused, not silently created.
    with pytest.raises(ValueError):
        spec.apply_change(db, row_key, "maps_to_family", "No Such Platform Family")
    assert db.query(ChemicalFamily).filter(ChemicalFamily.name == "No Such Platform Family").first() is None

    spec.apply_change(db, row_key, "maps_to_family", "Matched Family")
    db.commit()
    assert spec.get_current_value(db, row_key, "maps_to_family") == "Matched Family"

    # The platform row itself is never renamed or duplicated.
    assert db.query(ChemicalFamily).filter(ChemicalFamily.name == "Matched Family").count() == 1

    # Blank is a partially-filled sheet, not an error to reject at parse time —
    # but nothing to apply either.
    with pytest.raises(ValueError):
        spec.apply_change(db, row_key, "maps_to_family", "")


def test_subfamily_decision_requires_family_decided_first(db, drop_csv, taxonomy_setup):
    spec = get_spec("taxonomy_reconciliation")
    row_key = {"level": "subfamily", "drop_family": "Matched Family",
               "drop_subfamily": "Drop-Only Sub Under Matched"}

    # No family decision recorded yet for this row -> refused.
    with pytest.raises(ValueError):
        spec.apply_change(db, row_key, "maps_to_subfamily", "Matched Sub")

    spec.apply_change(db, row_key, "maps_to_family", "Matched Family")
    db.flush()
    spec.apply_change(db, row_key, "maps_to_subfamily", "Matched Sub")
    db.commit()

    assert spec.get_current_value(db, row_key, "maps_to_family") == "Matched Family"
    assert spec.get_current_value(db, row_key, "maps_to_subfamily") == "Matched Sub"

    # A subfamily name that exists, but not under the decided family, is refused.
    other_row_key = {"level": "subfamily", "drop_family": "Drop-Only Family",
                      "drop_subfamily": "Drop-Only Sub"}
    with pytest.raises(ValueError):
        spec.apply_change(db, other_row_key, "maps_to_subfamily", "Matched Sub")


def test_maps_to_subfamily_rejected_on_a_family_level_row(db, drop_csv, taxonomy_setup):
    spec = get_spec("taxonomy_reconciliation")
    row_key = {"level": "family", "drop_family": "Drop-Only Family", "drop_subfamily": ""}
    with pytest.raises(ValueError):
        spec.apply_change(db, row_key, "maps_to_subfamily", "Matched Sub")


def test_decided_rows_disappear_from_the_default_export(db, drop_csv, taxonomy_setup):
    spec = get_spec("taxonomy_reconciliation")
    row_key = {"level": "family", "drop_family": "Drop-Only Family", "drop_subfamily": ""}
    spec.apply_change(db, row_key, "maps_to_family", "Matched Family")
    db.commit()

    rows = spec.query_rows(db, spec.filter_schema())
    keys = {(r["level"], r["drop_family"], r["drop_subfamily"]) for r in rows}
    assert ("family", "Drop-Only Family", "") not in keys

    # Still visible with include_decided=True, showing the decision made.
    rows_incl = spec.query_rows(db, spec.filter_schema(include_decided=True))
    decided_row = next(r for r in rows_incl if (r["level"], r["drop_family"]) == ("family", "Drop-Only Family"))
    assert decided_row["maps_to_family"] == "Matched Family"
