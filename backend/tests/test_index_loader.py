"""Index layer loader v2 (Wave 3, SCRUM-74).

Integration tests against the real drop — skipped when
`sample_idea/costadvisor-data/` is absent.

The loader is idempotent, so `_ensure_loaded` is cheap to call repeatedly:
if the layers are already current it writes nothing. Row counts are never
asserted (the drop's README is explicit they move); what is asserted is
idempotency, the diff-report contract, the preserved distinctions, and the
queries the three-layer split exists to make possible.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func

from app.database import bypass_rls_var
from app.models.drop_issue import DropIssueRecord
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexCard, IndexMonthlyValue, TypeCode
from app.services.drop import drop_available, load_issues
from app.services.drop.index_loader import BASE_PERIOD, load_index_layer

needs_drop = pytest.mark.skipif(
    not drop_available(), reason="costadvisor-data drop not present in this checkout"
)

pytestmark = needs_drop


def _ensure_loaded(db):
    """Bring the index layers up to date. No-op when they already are."""
    bypass_rls_var.set(True)
    report = load_index_layer(db)
    if report.changed:
        db.commit()
    else:
        db.rollback()
    return report


# ── The loader's own contract ────────────────────────────────────────────────

def test_second_run_reports_no_changes(db):
    """The headline requirement: idempotent by comparison, not by
    truncate-and-reload."""
    _ensure_loaded(db)
    again = load_index_layer(db)
    db.rollback()

    assert again.changed == 0, again.render()
    # And every table accounted for as unchanged rather than simply absent.
    for diff in again.tables:
        assert diff.unchanged > 0, f"{diff.table} reported nothing"
        assert diff.created == 0 and diff.updated == 0 and diff.deleted == 0


def test_report_names_every_table(db):
    _ensure_loaded(db)
    report = load_index_layer(db)
    db.rollback()

    assert {t.table for t in report.tables} == {
        "commodity_indexes", "type_codes", "index_cards",
        "index_monthly_values", "drop_issues",
    }
    assert "total changes" in report.render()


def test_nothing_is_skipped_on_the_current_drop(db):
    """Every row of every layer resolves. A skip here would mean a real
    referential problem, and the report names it rather than hiding it."""
    _ensure_loaded(db)
    report = load_index_layer(db)
    db.rollback()
    assert report.skipped == []


def test_an_edit_is_detected_as_an_update(db):
    """Proof the unchanged/updated split is real comparison and not a
    hardcoded "second run is clean"."""
    _ensure_loaded(db)
    card = db.query(IndexCard).first()
    original = card.region_label
    card.region_label = "MUTATED BY TEST"
    db.flush()
    try:
        report = load_index_layer(db)
        cards = report.table("index_cards")
        assert cards.updated == 1
        assert cards.unchanged > 0
    finally:
        db.rollback()
        # The rollback restores it; confirm rather than assume.
        db.expire_all()
        assert db.query(IndexCard).filter(IndexCard.id == card.id).one().region_label == original


def test_dry_run_leaves_nothing_behind(db):
    """A dry run is the caller rolling back — which is exactly why the loader
    has no `if dry_run` branch that could drift from the real path."""
    _ensure_loaded(db)
    before = db.query(func.count(TypeCode.id)).scalar()

    # Pick one no recipe line references: since the catalog retarget, cost
    # lines FK at type_codes, so deleting a referenced code raises instead of
    # exercising the re-create path this test is about.
    from app.models.formula_template import FormulaTemplateComponent

    referenced = db.query(FormulaTemplateComponent.type_code_id).filter(
        FormulaTemplateComponent.type_code_id.isnot(None)
    ).distinct().subquery()
    victim = db.query(TypeCode).filter(~TypeCode.id.in_(db.query(referenced))).first()
    assert victim is not None, "expected at least one unreferenced type code"
    db.delete(victim)
    db.flush()
    report = load_index_layer(db)   # would re-create it
    assert report.table("type_codes").created == 1
    db.rollback()

    db.expire_all()
    assert db.query(func.count(TypeCode.id)).scalar() == before


# ── Distinctions the loader must preserve ────────────────────────────────────

def test_swap_priority_survives_as_itself(db):
    """The pre-drop seeder folded A into `good_proxy` and everything else into
    `weak_proxy`, losing B-vs-C and reading a C-ranked code — already correct
    by design — as a rough approximation. A/B/C are a sourcing backlog rank,
    not an accuracy ladder."""
    _ensure_loaded(db)
    ranks = {
        r[0] for r in db.query(TypeCode.swap_priority).distinct() if r[0] is not None
    }
    assert {"A", "B", "C"} <= ranks
    # Blank is the modal value in the source, so it has to stay nullable.
    assert db.query(TypeCode).filter(TypeCode.swap_priority.is_(None)).count() > 0


def test_ideal_index_is_kept_as_prose(db):
    """It names a series we do not have — none of its values correspond to a
    commodity_key — so it can never be an FK today. The set of non-null values
    IS the sourcing backlog."""
    _ensure_loaded(db)
    wanted = [
        r[0] for r in db.query(TypeCode.ideal_index).filter(TypeCode.ideal_index.isnot(None))
    ]
    assert wanted, "expected a sourcing wishlist"

    keys = {
        r[0] for r in db.query(CommodityIndex.commodity_key)
        .filter(CommodityIndex.commodity_key.isnot(None))
    }
    assert not (set(wanted) & keys), "ideal_index should name series we lack"


def test_registry_proxy_status_is_stored_and_the_line_reading_stays_available(db):
    """Neither reading is discarded. The registry's lands on type_codes; the
    line's stays readable from the drop, and `proxy_status_pair` is what keeps
    them side by side rather than adjudicated."""
    from app.services.drop import proxy_status_pair, read_table

    _ensure_loaded(db)
    stored = {tc.code: tc.proxy_status for tc in db.query(TypeCode)}
    assert {"direct", "proxy", "unclassified"} <= set(
        v for v in stored.values() if v is not None
    )

    type_rows = {r["type_code"]: r for r in read_table("type_codes")}
    disagreements = 0
    for line in read_table("combo_lines"):
        pair = proxy_status_pair(line, type_rows.get(line["type_code"]))
        if pair.line is not None and pair.registry is not None and not pair.agrees:
            disagreements += 1
            # The stored registry value is one half; the line's other half is
            # still legible, which is the whole point.
            assert stored.get(line["type_code"]) == pair.registry
    assert disagreements > 0, "expected the known proxy_status conflict"


def test_no_series_codes_keep_their_target(db):
    """`no_series` means the target series has no NUMBERS, not that there is
    no target — so the FK is populated and only `ambiguous` is null."""
    _ensure_loaded(db)
    for tc in db.query(TypeCode).filter(TypeCode.resolution == "no_series"):
        assert tc.resolves_to_id is not None, tc.code
    for tc in db.query(TypeCode).filter(TypeCode.resolution == "ambiguous"):
        assert tc.resolves_to_id is None, tc.code


def test_base_period_only_where_there_is_history(db):
    """The forecast-only series have no anchor month at all, so claiming one
    would make their levels look comparable to the others when they are not."""
    _ensure_loaded(db)
    anchored = db.query(CommodityIndex).filter(
        CommodityIndex.commodity_key.isnot(None),
        CommodityIndex.base_period == BASE_PERIOD,
    ).count()
    assert anchored > 0

    for series in db.query(CommodityIndex).filter(
        CommodityIndex.commodity_key.isnot(None),
        CommodityIndex.base_period.is_(None),
    ):
        actuals = db.query(func.count(IndexMonthlyValue.id)).filter(
            IndexMonthlyValue.commodity_id == series.id,
            IndexMonthlyValue.kind == "actual",
        ).scalar()
        assert actuals == 0, f"{series.commodity_key} has history but no base period"


def test_recomputable_snapshots_are_not_stored(db):
    """DB-7's rule. `volatility_pct` is also internally contradictory in the
    source — the same series carries two different values on two cards — so
    importing it would enshrine a conflict."""
    for column in ("current_value", "change_pct", "volatility_pct", "cycle_pct",
                   "card_status", "has_intel_block", "shares_series_with"):
        assert not hasattr(IndexCard, column), f"IndexCard should not store {column}"


# ── The queries the three-layer split exists to enable ───────────────────────

def test_the_full_chain_is_one_join(db):
    """type code -> series -> numbers, without reassembling anything in
    memory. This is what `seed_combos.feed_code_map()` used to compute and
    throw away at the end of every load."""
    _ensure_loaded(db)
    row = (
        db.query(
            TypeCode.code, CommodityIndex.commodity_key,
            func.count(IndexMonthlyValue.id).label("points"),
        )
        .join(CommodityIndex, CommodityIndex.id == TypeCode.resolves_to_id)
        .join(IndexMonthlyValue, IndexMonthlyValue.commodity_id == CommodityIndex.id)
        .filter(TypeCode.resolution == "resolved")
        .group_by(TypeCode.code, CommodityIndex.commodity_key)
        .first()
    )
    assert row is not None and row.points > 0


def test_concentration_is_visible(db):
    """The finding the layer was built for: many codes resolve to one series,
    so a cost breakdown that looks diversified can be one commodity wearing
    several labels. Asserted as a relationship, not a count — the drop's own
    numbers will move."""
    _ensure_loaded(db)
    top = (
        db.query(
            CommodityIndex.commodity_key,
            func.count(TypeCode.id).label("codes"),
            func.sum(TypeCode.source_total_weight).label("weight"),
        )
        .join(TypeCode, TypeCode.resolves_to_id == CommodityIndex.id)
        .group_by(CommodityIndex.commodity_key)
        .order_by(func.sum(TypeCode.source_total_weight).desc())
        .first()
    )
    assert top.codes > 1, "expected at least one series backing several codes"
    assert float(top.weight) > 0

    total = db.query(func.sum(TypeCode.source_total_weight)).scalar()
    share = float(top.weight) / float(total) * 100
    # The most-concentrated series carries a materially large share — the
    # reason this is worth surfacing to a buyer at all.
    assert share > 10, f"top series carries only {share:.1f}%"


def test_a_series_can_back_several_cards(db):
    """132 cards over 121 series in this drop — keying the app by series would
    silently lose the difference."""
    _ensure_loaded(db)
    shared = (
        db.query(IndexCard.commodity_id, func.count(IndexCard.id).label("n"))
        .group_by(IndexCard.commodity_id)
        .having(func.count(IndexCard.id) > 1)
        .all()
    )
    assert shared, "expected at least one series displayed by several cards"


def test_actual_and_forecast_are_separable_after_load(db):
    _ensure_loaded(db)
    kinds = {r[0] for r in db.query(IndexMonthlyValue.kind).distinct()}
    assert kinds == {"actual", "forecast"}


# ── The delivered defect register ────────────────────────────────────────────

def test_issues_are_carried_through_not_recomputed(db):
    """Every finding the data team shipped is present, and classified so the
    register is filterable in SQL without re-parsing prose."""
    _ensure_loaded(db)
    from_file = load_issues()
    stored = db.query(func.count(DropIssueRecord.id)).scalar()
    assert stored == len({(i.table, i.key, i.column, i.problem) for i in from_file})

    assert db.query(DropIssueRecord).filter(DropIssueRecord.awaiting_decision.is_(True)).count() > 0
    assert db.query(DropIssueRecord).filter(DropIssueRecord.blocking.is_(True)).count() > 0
    # Most findings are neither — notes and already-applied resolutions.
    assert db.query(DropIssueRecord).filter(
        DropIssueRecord.awaiting_decision.is_(False),
        DropIssueRecord.blocking.is_(False),
    ).count() > 0


def test_a_finding_is_traceable_to_its_row(db):
    """The point of persisting the register: a defect travels with the data it
    describes instead of being rediscovered by whoever hits it."""
    _ensure_loaded(db)
    finding = (
        db.query(DropIssueRecord)
        .filter(DropIssueRecord.source_table == "type_codes")
        .first()
    )
    assert finding is not None
    assert db.query(TypeCode).filter(TypeCode.code == finding.source_key).one_or_none() is not None


# ── The existing costing path is untouched ───────────────────────────────────

def test_pre_drop_indexes_are_left_alone(db):
    """Additive, per the drop analysis. The drop's series are their own
    population (`commodity_key IS NOT NULL`); nothing was matched by name,
    because our keys are region-agnostic while the drop's bake region in —
    a name match would collapse three regional series onto one row and
    repoint every cost model referencing it."""
    _ensure_loaded(db)
    legacy = db.query(CommodityIndex).filter(CommodityIndex.commodity_key.is_(None)).count()
    dropped = db.query(CommodityIndex).filter(CommodityIndex.commodity_key.isnot(None)).count()
    assert legacy > 0, "pre-drop indexes should still be here"
    assert dropped > 0

    # No drop series claims a legacy row's identity.
    overlap = (
        db.query(CommodityIndex.name)
        .filter(CommodityIndex.commodity_key.isnot(None))
        .filter(CommodityIndex.name.in_(
            db.query(CommodityIndex.name).filter(CommodityIndex.commodity_key.is_(None))
        ))
        .count()
    )
    assert overlap == 0
