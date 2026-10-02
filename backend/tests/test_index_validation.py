"""Index data-quality validation (Wave 3, SCRUM-34).

The ticket's acceptance criteria, as tests:

1. a run emits **structured** findings, each naming the table, key, column and
   the two conflicting values;
2. a type code whose `proxy_status` disagrees with the `proxy_status` on its
   cost lines appears as a finding;
3. a card whose declared agency, unit or incoterm disagrees with its series
   appears as a finding;
4. findings from `_issues.csv` are **carried through**, not recomputed, and are
   distinguishable from findings we derived;
5. a series with forecast points and no actual history is flagged, and the flag
   is **queryable** rather than log-only;
6. the run is inspectable after the fact and **re-runnable without duplicating
   findings**.

Plus the refusals — what is deliberately *not* a finding — because a check that
over-reports buries the real ones, and the region check has already done that
once (see `test_a_card_agreeing_with_its_series_key_is_not_a_finding`).

The database this runs against carries the real drop, so every test asserts
about rows it created rather than about totals.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.drop_issue import DropIssueRecord
from app.models.formula_template import (
    FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexCard, IndexMonthlyValue, TypeCode
from app.models.index_validation import IndexValidationFinding, IndexValidationRun
from app.services.index_validation import (
    CHECK_CODES, DERIVED_CHECKS, check_ambiguous_resolution,
    check_card_series_provenance, check_forecast_only_series,
    check_frequency_vocabulary, check_proxy_status_contradiction,
    check_region_rebadged, check_sibling_card_disagreement, collect,
    run_validation,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _series(db, key: str, **kwargs) -> CommodityIndex:
    ci = CommodityIndex(name=f"V-{key}", commodity_key=key, scrape_enabled=False,
                        **kwargs)
    db.add(ci)
    db.commit()
    return ci


def _card(db, series: CommodityIndex, feed_key: str, **kwargs) -> IndexCard:
    card = IndexCard(feed_key=feed_key, feed_slug=feed_key.split("|")[0],
                     commodity_id=series.id, **kwargs)
    db.add(card)
    db.commit()
    return card


def _code(db, code: str, series=None, resolution="resolved", **kwargs) -> TypeCode:
    tc = TypeCode(code=code, resolves_to_id=series.id if series else None,
                  resolution=resolution, **kwargs)
    db.add(tc)
    db.commit()
    return tc


def _line(db, user_id, type_code: TypeCode, *, is_proxy: bool) -> FormulaTemplate:
    """One platform template carrying one index line that names `type_code`."""
    tpl = FormulaTemplate(team_id=None, created_by=user_id,
                          name=f"v-{uuid.uuid4().hex[:6]}",
                          code=f"V-{uuid.uuid4().hex[:8]}", expression=None)
    db.add(tpl)
    db.flush()
    db.add(FormulaTemplateComponent(
        template_id=tpl.id, region="Europe", variant="", name="line",
        component_type="index", type_code_id=type_code.id,
        is_proxy=is_proxy, weight_pct=100, sort_order=0))
    db.commit()
    return tpl


def _cleanup(db, *, series_ids=(), code_ids=(), template_ids=(), issue_ids=(),
             run_ids=(), fingerprints=()):
    db.rollback()
    bypass_rls_var.set(True)
    for fp in fingerprints:
        db.execute(text("DELETE FROM index_validation_findings WHERE fingerprint = :f"),
                   {"f": fp})
    for rid in run_ids:
        db.execute(text("UPDATE index_validation_findings SET first_seen_run_id = NULL"
                        " WHERE first_seen_run_id = :i"), {"i": str(rid)})
        db.execute(text("UPDATE index_validation_findings SET last_seen_run_id = NULL"
                        " WHERE last_seen_run_id = :i"), {"i": str(rid)})
        db.execute(text("DELETE FROM index_validation_runs WHERE id = :i"), {"i": str(rid)})
    for iid in issue_ids:
        db.execute(text("DELETE FROM drop_issues WHERE id = :i"), {"i": iid})
    for tid in template_ids:
        db.execute(text("DELETE FROM formula_templates WHERE id = :i"), {"i": str(tid)})
    for cid in code_ids:
        db.execute(text("DELETE FROM formula_template_components WHERE type_code_id = :i"),
                   {"i": cid})
        db.execute(text("DELETE FROM type_codes WHERE id = :i"), {"i": cid})
    for sid in series_ids:
        db.execute(text("DELETE FROM index_monthly_values WHERE commodity_id = :i"), {"i": sid})
        db.execute(text("DELETE FROM index_cards WHERE commodity_id = :i"), {"i": sid})
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": sid})
    db.commit()


def _for_key(findings, key):
    return [f for f in findings if f.subject_key == key]


# ── 1. Structured findings ───────────────────────────────────────────────────

def test_a_finding_names_the_table_key_column_and_both_values(db, tenant_a):
    """AC1, and the reason the two values are two labelled columns rather than a
    message: a contradiction is only actionable if you can see which side says
    what without parsing prose."""
    series = _series(db, f"vfind-{uuid.uuid4().hex[:6]}")
    code = _code(db, f"VF-{uuid.uuid4().hex[:6]}".upper(), series,
                 proxy_status="proxy")
    tpl = _line(db, tenant_a["user_id"], code, is_proxy=False)
    try:
        [finding] = _for_key(check_proxy_status_contradiction(db), code.code)
        assert finding.subject_table == "type_codes"
        assert finding.subject_key == code.code
        assert finding.subject_column == "proxy_status"
        assert finding.left_label == "type_codes.proxy_status"
        assert finding.left_value == "proxy"
        assert finding.right_label == "combo_lines.proxy_status"
        assert finding.right_value == "direct"
        assert finding.severity == "contradiction"
        assert finding.origin == "derived"
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id],
                 series_ids=[series.id])


# ── 2. The proxy_status contradiction ────────────────────────────────────────

def test_a_registry_proxy_over_a_direct_line_is_a_finding(db, tenant_a):
    """AC2. DB-5 kept both columns rather than picking a winner, because the
    shipped `w_proxy` / `coverage_tier` were computed from the line value — so
    the contradiction has to be surfaced, or whichever column a reader consults
    looks authoritative."""
    series = _series(db, f"vprox-{uuid.uuid4().hex[:6]}")
    code = _code(db, f"VP-{uuid.uuid4().hex[:6]}".upper(), series,
                 proxy_status="proxy")
    tpl = _line(db, tenant_a["user_id"], code, is_proxy=False)
    try:
        assert len(_for_key(check_proxy_status_contradiction(db), code.code)) == 1
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id],
                 series_ids=[series.id])


def test_an_agreeing_line_is_not_a_finding(db, tenant_a):
    series = _series(db, f"vagr-{uuid.uuid4().hex[:6]}")
    code = _code(db, f"VA-{uuid.uuid4().hex[:6]}".upper(), series,
                 proxy_status="proxy")
    tpl = _line(db, tenant_a["user_id"], code, is_proxy=True)
    try:
        assert _for_key(check_proxy_status_contradiction(db), code.code) == []
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id],
                 series_ids=[series.id])


def test_unclassified_cannot_contradict_a_line(db, tenant_a):
    """`unclassified` is the registry declining to say. A column with no claim
    cannot disagree with one that has a claim, and treating it as disagreement
    would add a large block of findings nobody can act on."""
    series = _series(db, f"vunc-{uuid.uuid4().hex[:6]}")
    code = _code(db, f"VU-{uuid.uuid4().hex[:6]}".upper(), series,
                 proxy_status="unclassified")
    tpl = _line(db, tenant_a["user_id"], code, is_proxy=True)
    try:
        assert _for_key(check_proxy_status_contradiction(db), code.code) == []
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id],
                 series_ids=[series.id])


# ── 3. Declared provenance ───────────────────────────────────────────────────

def test_a_card_disagreeing_with_its_series_about_agency_is_a_finding(db):
    """AC3."""
    series = _series(db, f"vagy-{uuid.uuid4().hex[:6]}", provider="ICIS")
    card = _card(db, series, f"vagy-{uuid.uuid4().hex[:6]}|EU", region="EU",
                 agency="Argus")
    try:
        [finding] = _for_key(check_card_series_provenance(db), card.feed_key)
        assert finding.subject_column == "agency"
        assert {finding.left_value, finding.right_value} == {"Argus", "ICIS"}
    finally:
        _cleanup(db, series_ids=[series.id])


def test_an_undeclared_value_is_not_a_contradiction(db):
    """`decisions/index_basis.csv` shipped empty, so most of the library has no
    declared incoterm. Treating absence as disagreement would emit a finding for
    nearly every card and bury the real ones."""
    series = _series(db, f"vund-{uuid.uuid4().hex[:6]}", quoted_incoterm=None)
    card = _card(db, series, f"vund-{uuid.uuid4().hex[:6]}|EU", region="EU",
                 incoterm="CIF")
    try:
        assert _for_key(check_card_series_provenance(db), card.feed_key) == []
    finally:
        _cleanup(db, series_ids=[series.id])


def test_two_cards_on_one_series_declaring_different_units_is_a_finding(db):
    """Where the card-vs-series contradiction actually lives: the loader copies
    a card's provenance onto its series, so that comparison checks a value
    against its own copy. A series is one set of numbers — two cards cannot
    correctly describe it in two different units."""
    key = f"vsib-{uuid.uuid4().hex[:6]}"
    series = _series(db, key, unit="USD/t")
    _card(db, series, f"{key}|EU", region="EU", unit="USD/t")
    _card(db, series, f"{key}|NA", region="NA", unit="USD/lb")
    try:
        findings = [f for f in _for_key(check_sibling_card_disagreement(db), key)
                    if f.subject_column == "unit"]
        assert len(findings) == 1
        assert findings[0].detail["values"] == ["USD/lb", "USD/t"]
    finally:
        _cleanup(db, series_ids=[series.id])


def test_a_card_agreeing_with_its_series_key_is_not_a_finding(db):
    """Regression for a false-positive class this check shipped with in draft.

    `index_cards.region` stores the drop's **raw** code (`CN`); an earlier
    version compared it against the mapped region name (`China`) and reported
    all 44 region-suffixed cards as contradicting themselves. Comparison is on
    the code, in the drop's own vocabulary.
    """
    key = f"vreg-{uuid.uuid4().hex[:6]}-cn"
    series = _series(db, key)
    agreeing = _card(db, series, f"{key}|CN", region="CN")
    disagreeing = _card(db, series, f"{key}|EU", region="EU")
    try:
        found = check_region_rebadged(db)
        assert _for_key(found, agreeing.feed_key) == []
        [bad] = _for_key(found, disagreeing.feed_key)
        assert bad.left_value == "EU" and bad.right_value == "CN"
    finally:
        _cleanup(db, series_ids=[series.id])


def test_a_source_suffix_is_not_read_as_a_region(db):
    """`-ppi` / `-wb` / `-mb` name a source, not a region (DB-5's finding). A
    check that parsed any trailing token would report every one of them."""
    key = f"vsrc-{uuid.uuid4().hex[:6]}-ppi"
    series = _series(db, key)
    card = _card(db, series, f"{key}|EU", region="EU")
    try:
        assert _for_key(check_region_rebadged(db), card.feed_key) == []
    finally:
        _cleanup(db, series_ids=[series.id])


def test_an_unknown_cadence_is_reported(db):
    """The DB-5 loader stores a card's frequency as free text, but the pre-drop
    seeders assert against `FREQUENCIES` — so an unknown cadence loads on one
    path and fails on the other. This check is what found the drop's compound
    per-region cadence, which is now in the vocabulary."""
    key = f"vfreq-{uuid.uuid4().hex[:6]}"
    series = _series(db, key)
    card = _card(db, series, f"{key}|EU", region="EU",
                 frequency="Every other Thursday")
    try:
        [finding] = _for_key(check_frequency_vocabulary(db), card.feed_key)
        assert finding.left_value == "Every other Thursday"
        assert finding.severity == "note"
    finally:
        _cleanup(db, series_ids=[series.id])


# ── 4. Carried through, and distinguishable ──────────────────────────────────

def test_declared_findings_are_carried_through_and_marked_as_such(db):
    """AC4. Re-deriving a delivered defect list would both duplicate it and risk
    quietly contradicting it, so the register is carried verbatim — and `origin`
    is a stored field rather than a convention, so a caller can tell which
    findings we stand behind ourselves."""
    rec = DropIssueRecord(
        source_table="index_commodities", source_key=f"v-{uuid.uuid4().hex[:6]}",
        source_column="basis_currency", problem="basis not declared",
        awaiting_decision=True, blocking=False)
    db.add(rec)
    db.commit()
    try:
        report = collect(db)
        [finding] = _for_key(report.findings, rec.source_key)
        assert finding.origin == "declared"
        assert finding.check_code == "drop_issue"
        # The register's own classification, preserved rather than reinterpreted.
        assert finding.severity == "gap"
        assert finding.left_value == "basis not declared"
        # And every derived finding is distinguishable from it.
        assert all(f.origin == "derived" for f in report.findings
                   if f.check_code != "drop_issue")
    finally:
        _cleanup(db, issue_ids=[rec.id])


def test_a_derived_only_run_does_not_resolve_declared_findings_by_silence(db):
    """A run that was told to skip the register has no opinion about it. Letting
    silence resolve those findings would make `--derived-only` quietly wipe the
    carried-through list."""
    rec = DropIssueRecord(
        source_table="index_feeds", source_key=f"v-{uuid.uuid4().hex[:6]}",
        source_column="agency", problem="unverified", blocking=False)
    db.add(rec)
    db.commit()
    full = run_validation(db, include_declared=True)
    db.commit()
    stored = db.query(IndexValidationFinding).filter(
        IndexValidationFinding.subject_key == rec.source_key).one()
    assert stored.resolved_at is None
    derived_only = run_validation(db, include_declared=False)
    db.commit()
    try:
        db.refresh(stored)
        assert stored.resolved_at is None, \
            "a derived-only run must not resolve the declared register"
    finally:
        _cleanup(db, issue_ids=[rec.id], fingerprints=[stored.fingerprint],
                 run_ids=[full.run_id, derived_only.run_id])


# ── 5. Forecast-only series ──────────────────────────────────────────────────

def test_a_forecast_only_series_is_flagged_and_queryable(db):
    """AC5. Such a series charts and has a latest value, while `data_resolver`
    refuses forecast rows outright — so it looks populated everywhere and
    prices nothing. `queryable rather than log-only` is the findings row."""
    key = f"vfc-{uuid.uuid4().hex[:6]}"
    series = _series(db, key)
    for month in range(1, 7):
        db.add(IndexMonthlyValue(commodity_id=series.id, year=2026, month=month,
                                 value=100 + month, kind="forecast"))
    db.commit()
    report = run_validation(db)
    db.commit()
    try:
        [finding] = _for_key(report.findings, key)
        assert finding.check_code == "forecast_only_series"
        assert finding.left_value == "0" and finding.right_value == "6"
        # Queryable, which is the half the ticket calls out.
        stored = db.query(IndexValidationFinding).filter(
            IndexValidationFinding.check_code == "forecast_only_series",
            IndexValidationFinding.subject_key == key).one()
        assert stored.resolved_at is None
        fingerprint = stored.fingerprint
    finally:
        _cleanup(db, series_ids=[series.id], fingerprints=[fingerprint],
                 run_ids=[report.run_id])


def test_a_series_with_any_actual_history_is_not_flagged(db):
    key = f"vact-{uuid.uuid4().hex[:6]}"
    series = _series(db, key)
    db.add(IndexMonthlyValue(commodity_id=series.id, year=2025, month=1,
                             value=100, kind="actual"))
    db.add(IndexMonthlyValue(commodity_id=series.id, year=2026, month=1,
                             value=110, kind="forecast"))
    db.commit()
    try:
        assert _for_key(check_forecast_only_series(db), key) == []
    finally:
        _cleanup(db, series_ids=[series.id])


# ── 6. The run: inspectable, and re-runnable without duplicating ─────────────

def test_a_rerun_touches_the_finding_rather_than_duplicating_it(db):
    """AC6, the half that is easy to get wrong: a findings-per-run table is
    inspectable but duplicates every standing finding on every run."""
    key = f"vdup-{uuid.uuid4().hex[:6]}"
    series = _series(db, key)
    db.add(IndexMonthlyValue(commodity_id=series.id, year=2026, month=1,
                             value=100, kind="forecast"))
    db.commit()

    first = run_validation(db)
    db.commit()
    stored = db.query(IndexValidationFinding).filter(
        IndexValidationFinding.subject_key == key).one()
    fingerprint, first_seen = stored.fingerprint, stored.first_seen_at

    second = run_validation(db)
    db.commit()
    try:
        rows = db.query(IndexValidationFinding).filter(
            IndexValidationFinding.subject_key == key).all()
        assert len(rows) == 1, "a re-run must not insert a second copy"
        assert rows[0].first_seen_at == first_seen
        assert rows[0].last_seen_run_id == second.run_id
        assert rows[0].first_seen_run_id == first.run_id
        # And the run itself stays inspectable afterwards.
        run = db.query(IndexValidationRun).filter(
            IndexValidationRun.id == first.run_id).one()
        assert run.finished_at is not None
        assert run.checks["forecast_only_series"] >= 1
    finally:
        _cleanup(db, series_ids=[series.id], fingerprints=[fingerprint],
                 run_ids=[first.run_id, second.run_id])


def test_a_finding_that_stops_appearing_is_resolved_not_deleted(db):
    """Evidence that a fix landed is worth more than a tidy table — and a
    deleted finding is indistinguishable from one that was never found."""
    key = f"vres-{uuid.uuid4().hex[:6]}"
    series = _series(db, key)
    db.add(IndexMonthlyValue(commodity_id=series.id, year=2026, month=1,
                             value=100, kind="forecast"))
    db.commit()
    first = run_validation(db)
    db.commit()
    stored = db.query(IndexValidationFinding).filter(
        IndexValidationFinding.subject_key == key).one()
    fingerprint = stored.fingerprint

    # Fix it: the series now has observed history.
    db.add(IndexMonthlyValue(commodity_id=series.id, year=2025, month=1,
                             value=90, kind="actual"))
    db.commit()
    second = run_validation(db)
    db.commit()
    try:
        db.refresh(stored)
        assert stored.resolved_at is not None
        assert second.n_resolved >= 1

        # And if it comes back it un-resolves rather than looking new.
        db.execute(text("DELETE FROM index_monthly_values"
                        " WHERE commodity_id = :i AND kind = 'actual'"),
                   {"i": series.id})
        db.commit()
        third = run_validation(db)
        db.commit()
        db.refresh(stored)
        assert stored.resolved_at is None
        assert db.query(IndexValidationFinding).filter(
            IndexValidationFinding.subject_key == key).count() == 1
    finally:
        _cleanup(db, series_ids=[series.id], fingerprints=[fingerprint],
                 run_ids=[first.run_id, second.run_id, third.run_id])


def test_a_check_that_found_nothing_reports_zero(db):
    """A run that skipped a check must not read as a run that found nothing
    wrong, so every check reports its count even when that count is zero."""
    report = collect(db, include_declared=False)
    for check in DERIVED_CHECKS:
        assert CHECK_CODES[check] in report.checks


def test_no_series_is_not_a_finding(db):
    """31 codes name the series they want and have no numbers for it. That is
    the sourcing instruction `swap_backlog` already ranks by cost weight — a
    second, unranked copy here would be noise, not validation."""
    series = _series(db, f"vns-{uuid.uuid4().hex[:6]}")
    code = _code(db, f"VN-{uuid.uuid4().hex[:6]}".upper(), series,
                 resolution="no_series")
    try:
        assert _for_key(check_ambiguous_resolution(db), code.code) == []
    finally:
        _cleanup(db, code_ids=[code.id], series_ids=[series.id])


def test_ambiguous_is_a_finding_because_the_remedy_is_different(db):
    """`no_series` is a purchase; `ambiguous` is a decision about what the code
    even means. Collapsing them would point somebody at the wrong fix."""
    code = _code(db, f"VM-{uuid.uuid4().hex[:6]}".upper(), None,
                 resolution="ambiguous", registry_note="two candidate series")
    try:
        [finding] = _for_key(check_ambiguous_resolution(db), code.code)
        assert finding.severity == "gap"
        assert "two candidate series" in finding.summary
    finally:
        _cleanup(db, code_ids=[code.id])


# ── API surface ──────────────────────────────────────────────────────────────

def test_running_requires_super_admin_but_reading_does_not(client_as, tenant_a):
    """Reading is any authenticated user — these are facts about the shared
    library. Running writes a judgement about it, and takes the same gate as
    the volatility recompute for the same reason."""
    member = client_as(tenant_a)
    assert member.post("/api/validation/runs", json={}).status_code == 403
    assert member.get("/api/validation/findings").status_code == 200
    assert member.get("/api/validation/preview").status_code == 200


def test_a_super_admin_can_run_it(client_as, user_factory, db):
    admin = user_factory(is_super_admin=True)
    response = client_as(admin).post("/api/validation/runs",
                                     json={"include_declared": False})
    try:
        assert response.status_code == 201
        body = response.json()
        # Per-check counts ride on the run, so a partial run is never mistaken
        # for a clean one.
        assert "forecast_only_series" in body["checks"]
    finally:
        _cleanup(db, run_ids=[uuid.UUID(response.json()["id"])]
                 if response.status_code == 201 else [])


def test_unauthenticated_is_refused(client):
    assert client.get("/api/validation/findings").status_code == 401
