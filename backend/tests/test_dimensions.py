"""Polymorphic dimension model + alias layer + query API (Wave 3, SCRUM-77).

The ticket's "Done" criteria, as tests:

1. terms, aliases and assertions load from the drop idempotently — re-running
   changes nothing;
2. the analyst-decided parts live in a reviewable file, and re-importing it is
   what changes the DB — not a loader branch (the shipped `sheet_roundtrip`
   mechanism, registered as a second payload);
3. every raw value that did not resolve is **listed by a report, not
   swallowed**;
4. a term list per kind, and a faceted query at **both grains**, with the
   alias / region / scope provenance on each hit.

Plus what the ticket calls out as the four dimensions being four different
problems, and the three measured producer facts that make a one-line
canonicaliser wrong.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
from sqlalchemy import text

from app.database import SessionLocal, bypass_rls_var, current_user_id_var
from app.models.dimension import (
    DIMENSION_KINDS, KIND_COMPLIANCE_FLAG, KIND_FUNCTIONALITY,
    KIND_FUNCTIONALITY_FAMILY, KIND_INDUSTRY, KIND_SUBSTITUTION_RISK,
    KIND_SUPPLY_REGION, DimensionAlias, DimensionAssertion, DimensionTerm,
    UnresolvedValue, normalize_value,
)
from app.models.producer import Producer, ProducerAlias, ProducerFormula
from app.models.product import Product
from app.models.cost_model import CostModel
from app.models.rbac import Permission, Role, RolePermission, TeamMemberRole, UserPlatformRole
from app.models.team import TeamMembership
from app.services.dimensions import (
    assert_term, record_unresolved, resolve_raw, unresolved_report, upsert_alias,
    upsert_term,
)
from app.services.drop.dimension_loader import load_dimensions
from app.services.drop.reader import drop_available
from app.services.producers import (
    resolve_raw_name, split_raw_name, upsert_producer_formula,
)
from app.services.sheet_roundtrip import get_spec

DROP_RAW = (pathlib.Path(__file__).resolve().parents[2]
            / "sample_idea" / "costadvisor-data" / "raw")

needs_drop = pytest.mark.skipif(
    not drop_available(), reason="costadvisor-data drop not present in this checkout"
)


def _cleanup(db, *, term_ids=(), producer_ids=(), product_ids=(), cost_model_ids=()):
    db.rollback()
    bypass_rls_var.set(True)
    for cid in cost_model_ids:
        db.execute(text("DELETE FROM cost_models WHERE id = :i"), {"i": str(cid)})
    for pid in product_ids:
        db.execute(text("DELETE FROM products WHERE id = :i"), {"i": str(pid)})
    for tid in term_ids:
        db.execute(text("DELETE FROM dimension_terms WHERE id = :i"), {"i": str(tid)})
    for pid in producer_ids:
        db.execute(text("DELETE FROM producers WHERE id = :i"), {"i": str(pid)})
    db.commit()


def _term(db, kind, code, label=None, **kw):
    return upsert_term(db, kind=kind, code=code, label=label or code, **kw)


@pytest.fixture(scope="module")
def _drop_loaded():
    """Load the drop once for the module.

    Module-scoped because a full load takes ~40s: six function-scoped loads made
    this file the slowest in the suite by an order of magnitude, and every one of
    them was loading identical data.
    """
    if not drop_available():
        pytest.skip("costadvisor-data drop not present in this checkout")
    session = SessionLocal()
    bypass_rls_var.set(True)
    try:
        load_dimensions(session)
        session.commit()
    finally:
        session.close()
    return True


# ── 1. Idempotent load from the drop ────────────────────────────────────────

@needs_drop
def test_the_load_is_idempotent(db, _drop_loaded):
    """AC1. Re-running changes nothing — asserted on the diff, so a counter
    that lies about an upsert fails here too (it did, and that is why
    `upsert_producer_formula` returns whether it created)."""
    bypass_rls_var.set(True)
    second = load_dimensions(db)
    db.commit()
    assert second.report.changed == 0, second.render()
    for diff in second.report.tables:
        assert diff.created == 0, f"{diff.table} created {diff.created} on a re-run"
        assert diff.updated == 0
        assert diff.deleted == 0


@needs_drop
def test_functionality_loads_clean_with_no_strays(db, _drop_loaded):
    """The one facet that is genuinely mechanical: 41 controlled terms, and
    every tag value is in the taxonomy."""
    bypass_rls_var.set(True)
    taxonomy = json.loads((DROP_RAW / "FUNCTIONALITY_TAXONOMY.json").read_text(encoding="utf-8"))
    terms = db.query(DimensionTerm).filter(
        DimensionTerm.kind == KIND_FUNCTIONALITY,
        DimensionTerm.team_id.is_(None)).all()
    assert len(terms) == len(taxonomy)
    assert {t.label for t in terms} == set(taxonomy)
    # Zero strays, so nothing lands in the register for this facet.
    assert db.query(UnresolvedValue).filter(
        UnresolvedValue.kind == KIND_FUNCTIONALITY).count() == 0
    assert db.query(DimensionAssertion).join(DimensionTerm).filter(
        DimensionTerm.kind == KIND_FUNCTIONALITY).count() > 0


@needs_drop
def test_the_two_functionality_schemes_stay_separate_kinds(db, _drop_loaded):
    """The trap the ticket names, verified: the family/subfamily vocabulary has
    **zero** overlap with the taxonomy, so one kind holding both would produce a
    facet with two disjoint halves and no way to tell which half a filter is
    acting on."""
    bypass_rls_var.set(True)
    a = {t.label for t in db.query(DimensionTerm).filter(
        DimensionTerm.kind == KIND_FUNCTIONALITY, DimensionTerm.team_id.is_(None)).all()}
    b = {t.label for t in db.query(DimensionTerm).filter(
        DimensionTerm.kind == KIND_FUNCTIONALITY_FAMILY,
        DimensionTerm.team_id.is_(None)).all()}
    assert a and b
    assert a & b == set(), "the two schemes are meant to be disjoint"
    assert KIND_FUNCTIONALITY in DIMENSION_KINDS
    assert KIND_FUNCTIONALITY_FAMILY in DIMENSION_KINDS


@needs_drop
def test_the_industry_classifier_is_unrecoverable_and_the_load_says_so(db, _drop_loaded):
    """A finding, pinned. `INDUSTRY_RULES.json` serialised **all 19** regexes to
    `{}` and the mockup holding the originals is not in this repo — so the
    mapping is entirely an analyst decision, and the loader must not pretend
    otherwise."""
    rules = json.loads((DROP_RAW / "INDUSTRY_RULES.json").read_text(encoding="utf-8"))
    assert rules, "expected the rule list to exist"
    assert all(r[0] == {} for r in rules), (
        "the regexes are no longer empty — the classifier may be recoverable "
        "now, and this test should be revisited"
    )

    bypass_rls_var.set(True)
    report = load_dimensions(db)
    db.commit()
    assert any("INDUSTRY_RULES" in n for n in report.notes)
    # The 19 controlled targets still load; only the mapping is missing.
    assert db.query(DimensionTerm).filter(
        DimensionTerm.kind == KIND_INDUSTRY,
        DimensionTerm.team_id.is_(None)).count() == 19


@needs_drop
def test_no_compliance_terms_are_invented_from_the_raw_labels(db, _drop_loaded):
    """The raw side is 239 distinct labels, many of them full sentences — a term
    table over that is not a facet. So terms come from the decision file, and
    every raw label is an alias candidate in the register instead."""
    bypass_rls_var.set(True)
    assert db.query(DimensionTerm).filter(
        DimensionTerm.kind == KIND_COMPLIANCE_FLAG).count() == 0
    queued = db.query(UnresolvedValue).filter(
        UnresolvedValue.kind == KIND_COMPLIANCE_FLAG).count()
    assert queued > 100, f"expected the raw flag labels to be queued, got {queued}"


@needs_drop
def test_out_of_vocabulary_risk_levels_are_queued_not_collapsed(db, _drop_loaded):
    """Collapsing "Medium-High" into "High" or "Medium" would silently re-rate a
    product, so it is queued for a human instead."""
    bypass_rls_var.set(True)
    codes = {t.code for t in db.query(DimensionTerm).filter(
        DimensionTerm.kind == KIND_SUBSTITUTION_RISK).all()}
    assert codes == {"low", "medium", "high"}
    queued = {u.raw_value for u in db.query(UnresolvedValue).filter(
        UnresolvedValue.kind == KIND_SUBSTITUTION_RISK).all()}
    assert queued, "expected the out-of-vocabulary levels to be queued"
    assert not (queued & {"Low", "Medium", "High"})


# ── 3. The unresolved report ────────────────────────────────────────────────

@needs_drop
def test_every_unresolved_value_is_reported_and_ranked(db, _drop_loaded):
    """AC3. The report is the analyst's work queue and how anyone checks the
    load worked, so it is counted rather than merely listed — one unresolved
    industry string can block dozens of assertions."""
    bypass_rls_var.set(True)
    rows = unresolved_report(db, kind=KIND_INDUSTRY)
    assert rows, "expected unresolved industry strings"
    # Ranked by how much each blocked.
    counts = [r.occurrences for r in rows]
    assert counts == sorted(counts, reverse=True)
    assert rows[0].occurrences >= 1
    assert rows[0].reason
    # Named in context, so an analyst can recognise the value.
    assert any(r.sample_subjects for r in rows)


@needs_drop
def test_the_register_is_a_snapshot_not_a_ledger(db, _drop_loaded):
    """A value resolved by yesterday's decision-file import must stop
    appearing, or the queue never shrinks and nobody trusts it."""
    bypass_rls_var.set(True)
    row = db.query(UnresolvedValue).filter(
        UnresolvedValue.kind == KIND_INDUSTRY).order_by(
        UnresolvedValue.occurrences.desc()).first()
    assert row is not None
    raw = row.raw_value

    target = db.query(DimensionTerm).filter(
        DimensionTerm.kind == KIND_INDUSTRY, DimensionTerm.team_id.is_(None)).first()
    alias = upsert_alias(db, target, raw, source="decision_file")
    alias_id = alias.id
    db.commit()
    try:
        load_dimensions(db)
        db.commit()
        assert db.query(UnresolvedValue).filter(
            UnresolvedValue.kind == KIND_INDUSTRY,
            UnresolvedValue.normalized == normalize_value(raw)).count() == 0
        # And it is now a real assertion.
        assert db.query(DimensionAssertion).filter(
            DimensionAssertion.term_id == target.id,
            DimensionAssertion.raw_value == raw).count() > 0
    finally:
        # A **platform** alias has no team to CASCADE from, so it survives the
        # tenant teardown — and a later test that expects this raw value to be
        # undecided then finds it already mapped. Same class of leak as the
        # platform market signal in unit 8; cleaned up for the same reason.
        db.rollback()
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM dimension_assertions WHERE matched_alias_id = :i"),
                   {"i": str(alias_id)})
        db.execute(text("DELETE FROM dimension_aliases WHERE id = :i"),
                   {"i": str(alias_id)})
        db.commit()


def test_unresolved_counts_occurrences_and_caps_its_samples(db):
    # Unique value: the real load already queues "Cross-sector" with its own
    # count, so reusing it would measure that instead of these nine calls.
    raw = f"Cross-sector {uuid.uuid4().hex[:8]}"
    for i in range(9):
        record_unresolved(db, KIND_INDUSTRY, raw, f"SUBJ-{i}", "no mapping")
    db.commit()
    try:
        row = db.query(UnresolvedValue).filter(
            UnresolvedValue.kind == KIND_INDUSTRY,
            UnresolvedValue.normalized == normalize_value(raw)).one()
        assert row.occurrences == 9
        # A work queue, not a data dump.
        assert len(row.sample_subjects) == 5
    finally:
        db.rollback()
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM dimension_unresolved WHERE normalized = :n"),
                   {"n": normalize_value(raw)})
        db.commit()


# ── 4. Terms, aliases, assertions, and the faceted query ───────────────────

def test_a_term_is_idempotent_and_an_alias_has_one_meaning_per_facet(db):
    code = f"t-{uuid.uuid4().hex[:8]}"
    a = _term(db, KIND_INDUSTRY, code, "Widgets")
    b = _term(db, KIND_INDUSTRY, code, "Widgets Renamed")
    db.commit()
    try:
        assert a.id == b.id, "same (kind, code) must upsert, not duplicate"
        assert b.label == "Widgets Renamed"

        other = _term(db, KIND_INDUSTRY, f"t-{uuid.uuid4().hex[:8]}", "Gadgets")
        first = upsert_alias(db, a, "  WIDGETS  ")
        again = upsert_alias(db, other, "widgets")
        db.commit()
        # Re-pointing happens IN PLACE — otherwise the same string resolves two
        # ways depending on row order, which is how a reordered rule list
        # quietly reclassifies a library.
        assert first.id == again.id
        assert again.term_id == other.id
        assert resolve_raw(db, KIND_INDUSTRY, "Widgets").term_id == other.id
    finally:
        _cleanup(db, term_ids=[a.id, other.id])


def test_a_team_alias_overrides_the_platform_one(db, tenant_a):
    """A team can disagree with our mapping of "Industrial" without us changing
    it for everyone."""
    plat = _term(db, KIND_INDUSTRY, f"p-{uuid.uuid4().hex[:8]}", "Platform target")
    team = _term(db, KIND_INDUSTRY, f"m-{uuid.uuid4().hex[:8]}", "Team target",
                 team_id=tenant_a["team_id"])
    db.commit()
    try:
        upsert_alias(db, plat, "Industrial")
        upsert_alias(db, team, "Industrial", team_id=tenant_a["team_id"])
        db.commit()
        assert resolve_raw(db, KIND_INDUSTRY, "Industrial").term_id == plat.id
        assert resolve_raw(db, KIND_INDUSTRY, "Industrial",
                           team_id=tenant_a["team_id"]).term_id == team.id
    finally:
        _cleanup(db, term_ids=[plat.id, team.id])


def test_an_assertion_is_idempotent_including_the_wildcard_region(db):
    """`region` is nullable and Postgres treats every NULL as distinct in a
    unique index, so the "every region" case is folded to a literal — otherwise
    the same claim inserts twice and the load stops being idempotent."""
    term = _term(db, KIND_INDUSTRY, f"t-{uuid.uuid4().hex[:8]}", "Target")
    db.commit()
    try:
        a = assert_term(db, term, subject_type="formula", subject_code="X-1")
        b = assert_term(db, term, subject_type="formula", subject_code="X-1")
        eu = assert_term(db, term, subject_type="formula", subject_code="X-1",
                         region="Europe")
        db.commit()
        assert a.id == b.id
        assert eu.id != a.id, "a region-specific claim is a different claim"
        assert db.query(DimensionAssertion).filter(
            DimensionAssertion.term_id == term.id).count() == 2
    finally:
        _cleanup(db, term_ids=[term.id])


def test_a_template_less_subject_asserts_with_template_id_null(db):
    """The same rule as the editorial blocks: a hard FK would drop these at
    import without raising."""
    term = _term(db, KIND_INDUSTRY, f"t-{uuid.uuid4().hex[:8]}", "Target")
    db.commit()
    try:
        row = assert_term(db, term, subject_type="formula",
                          subject_code="GRP-NOT-A-FORMULA")
        db.commit()
        assert row.template_id is None
        assert row.subject_code == "GRP-NOT-A-FORMULA"
    finally:
        _cleanup(db, term_ids=[term.id])


@pytest.fixture
def facet_fixture(db, tenant_a):
    """A platform term asserted on a formula the team actually owns a product
    for, so both query grains have something to return."""
    from app.models.formula_template import FormulaTemplate

    tpl = FormulaTemplate(
        team_id=None, created_by=tenant_a["user_id"],
        name=f"tpl-{uuid.uuid4().hex[:6]}", code=f"FX-{uuid.uuid4().hex[:8]}",
        expression=None,
    )
    db.add(tpl)
    db.flush()
    product = Product(
        id=uuid.uuid4(), team_id=tenant_a["team_id"], created_by=tenant_a["user_id"],
        name=f"Prod-{uuid.uuid4().hex[:4]}", unit="kg", formula_template_id=tpl.id,
    )
    db.add(product)
    db.flush()
    cm = CostModel(
        id=uuid.uuid4(), team_id=tenant_a["team_id"], product_id=product.id,
        created_by=tenant_a["user_id"], region="Europe", currency="USD",
    )
    db.add(cm)
    term = _term(db, KIND_COMPLIANCE_FLAG, f"eudr-{uuid.uuid4().hex[:6]}", "EUDR")
    alias = upsert_alias(db, term, "EU Deforestation Regulation (EUDR) traceability")
    db.commit()

    yield {"template": tpl, "product": product, "cost_model": cm,
           "term": term, "alias": alias}

    db.rollback()
    bypass_rls_var.set(True)
    db.execute(text("DELETE FROM cost_models WHERE id = :i"), {"i": str(cm.id)})
    db.execute(text("DELETE FROM products WHERE id = :i"), {"i": str(product.id)})
    db.execute(text("DELETE FROM dimension_terms WHERE id = :i"), {"i": str(term.id)})
    db.execute(text("DELETE FROM formula_templates WHERE id = :i"), {"i": str(tpl.id)})
    db.commit()


def test_the_faceted_query_answers_at_both_grains(db, tenant_a, client_as, facet_fixture):
    """AC4, and the grain error the old single-"products" framing made: the
    Intelligence library renders platform tiles, Portfolio renders a team's
    products, and both read the same join."""
    f = facet_fixture
    assert_term(db, f["term"], subject_type="formula",
                subject_code=f["template"].code,
                raw_value="EU Deforestation Regulation (EUDR) traceability",
                matched_alias=f["alias"])
    db.commit()

    c = client_as(tenant_a)
    base = (f"/api/dimensions/query?team_id={tenant_a['team_id']}"
            f"&kind={KIND_COMPLIANCE_FLAG}&code={f['term'].code}")

    plat = c.get(f"{base}&grain=platform")
    assert plat.status_code == 200, plat.text
    pj = plat.json()
    assert pj["grain"] == "platform" and pj["total"] == 1
    hit = pj["hits"][0]
    assert hit["subject_code"] == f["template"].code
    assert hit["template_name"] == f["template"].name
    # The audit trail on every hit: which alias matched, the region, the scope.
    assert hit["matched_alias"] == "EU Deforestation Regulation (EUDR) traceability"
    assert hit["scope"] == "platform"
    assert hit["region"] is None

    team = c.get(f"{base}&grain=team")
    assert team.status_code == 200, team.text
    tj = team.json()
    assert tj["grain"] == "team" and tj["total"] == 1
    th = tj["hits"][0]
    assert th["product_id"] == str(f["product"].id)
    assert th["cost_model_id"] == str(f["cost_model"].id)
    assert th["cost_model_region"] == "Europe"
    assert th["region_applies"] is True


def test_a_region_specific_claim_does_not_reach_a_combo_in_another_region(
        db, tenant_a, client_as, facet_fixture):
    """Assertions arrive keyed to a formula but the question is asked per
    formula x region — "which of my *EU* combos touch EUDR" is the real
    question, and EUDR is an EU claim."""
    f = facet_fixture
    assert_term(db, f["term"], subject_type="formula",
                subject_code=f["template"].code, region="NA")
    db.commit()

    body = client_as(tenant_a).get(
        f"/api/dimensions/query?team_id={tenant_a['team_id']}"
        f"&kind={KIND_COMPLIANCE_FLAG}&code={f['term'].code}&grain=team").json()
    assert body["total"] == 1
    # The product's combo is in Europe; the claim is NA-only.
    assert body["hits"][0]["region"] == "NA"
    assert body["hits"][0]["region_applies"] is False


def test_a_region_filter_still_admits_the_every_region_claims(
        db, tenant_a, client_as, facet_fixture):
    """An EU query that dropped the NULL-region claims would silently lose every
    global assertion."""
    f = facet_fixture
    assert_term(db, f["term"], subject_type="formula", subject_code=f["template"].code)
    db.commit()

    body = client_as(tenant_a).get(
        f"/api/dimensions/query?team_id={tenant_a['team_id']}"
        f"&kind={KIND_COMPLIANCE_FLAG}&code={f['term'].code}"
        f"&grain=platform&region=Europe").json()
    assert body["total"] == 1
    assert body["hits"][0]["region"] is None


def test_the_subject_read_groups_every_kind_for_the_card(
        db, tenant_a, client_as, facet_fixture):
    """The dimension half that SCRUM-76's composed card folds in — not a second
    card-shaped endpoint."""
    f = facet_fixture
    other = _term(db, KIND_FUNCTIONALITY, f"fn-{uuid.uuid4().hex[:6]}", "Thickening")
    db.commit()
    try:
        assert_term(db, f["term"], subject_type="formula",
                    subject_code=f["template"].code)
        assert_term(db, other, subject_type="formula", subject_code=f["template"].code)
        db.commit()

        body = client_as(tenant_a).get(
            f"/api/dimensions/subjects/formula/{f['template'].code}"
            f"?team_id={tenant_a['team_id']}").json()
        assert set(body["dimensions"]) == {KIND_COMPLIANCE_FLAG, KIND_FUNCTIONALITY}
        assert body["dimensions"][KIND_FUNCTIONALITY][0]["label"] == "Thickening"
        assert body["dimensions"][KIND_FUNCTIONALITY][0]["scope"] == "platform"
    finally:
        _cleanup(db, term_ids=[other.id])


def test_the_term_list_and_kind_summary(db, tenant_a, client_as):
    c = client_as(tenant_a)
    r = c.get(f"/api/dimensions/kinds?team_id={tenant_a['team_id']}")
    assert r.status_code == 200, r.text
    assert [k["kind"] for k in r.json()["kinds"]] == list(DIMENSION_KINDS)

    bad = c.get(f"/api/dimensions/terms?team_id={tenant_a['team_id']}&kind=nope")
    assert bad.status_code == 422


# ── Tenancy ─────────────────────────────────────────────────────────────────

def test_platform_terms_stay_visible_and_team_terms_do_not_leak(
        db, tenant_a, tenant_b):
    """The tenancy shape the ticket insists on: under strict tenant every
    platform term would be invisible to every team, so the facet would be empty
    for everyone on day one and the bug would look like a loader failure."""
    plat = _term(db, KIND_INDUSTRY, f"p-{uuid.uuid4().hex[:8]}", "Platform")
    a_term = _term(db, KIND_INDUSTRY, f"a-{uuid.uuid4().hex[:8]}", "A only",
                   team_id=tenant_a["team_id"])
    db.commit()
    # Ids captured before the RLS-scoped session: the ORM objects expire across
    # it and a later attribute read raises ObjectDeletedError on the policy's
    # own hidden row rather than on anything being wrong.
    plat_id, a_id = plat.id, a_term.id
    try:
        s = SessionLocal()
        bypass_rls_var.set(False)
        current_user_id_var.set(str(tenant_b["user_id"]))
        try:
            visible = {t.id for t in s.query(DimensionTerm).all()}
            assert plat_id in visible, "platform terms must be readable by every team"
            assert a_id not in visible, "another team's term must be invisible"
        finally:
            s.close()
            bypass_rls_var.set(True)
    finally:
        _cleanup(db, term_ids=[plat_id, a_id])


def test_platform_writes_need_the_platform_permission(db, tenant_a, client_as):
    r = client_as(tenant_a).post(
        f"/api/dimensions/terms?team_id={tenant_a['team_id']}",
        json={"kind": KIND_INDUSTRY, "code": "nope", "label": "Nope",
              "platform": True})
    assert r.status_code == 403, r.text
    assert "Platform permission" in r.json()["detail"]


def test_the_content_editor_role_can_author_platform_terms(db, tenant_a, client_as):
    """`dimensions.*` comes from SCRUM-76's single permission migration — this
    story consumes those keys and adds no second migration."""
    role = db.query(Role).filter(Role.team_id.is_(None),
                                 Role.name == "Content Editor").first()
    assert role is not None
    granted = {
        p.key for p in db.query(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .filter(RolePermission.role_id == role.id).all()
    }
    assert {"dimensions.view", "dimensions.edit"} <= granted

    db.add(UserPlatformRole(user_id=tenant_a["user_id"], role_id=role.id))
    db.commit()
    code = f"pt-{uuid.uuid4().hex[:8]}"
    r = client_as(tenant_a).post(
        f"/api/dimensions/terms?team_id={tenant_a['team_id']}",
        json={"kind": KIND_INDUSTRY, "code": code, "label": "Authored",
              "platform": True})
    assert r.status_code == 201, r.text
    try:
        assert r.json()["team_id"] is None
    finally:
        _cleanup(db, term_ids=[r.json()["id"]])


def test_a_view_only_member_cannot_write(db, tenant_a, user_factory, client_as):
    member = user_factory()
    db.add(TeamMembership(user_id=member["user_id"], team_id=tenant_a["team_id"],
                          role="member"))
    role = Role(team_id=tenant_a["team_id"], name=f"Reader-{uuid.uuid4().hex[:6]}")
    db.add(role)
    db.flush()
    view = db.query(Permission).filter(Permission.key == "dimensions.view").one()
    db.add(RolePermission(role_id=role.id, permission_id=view.id))
    db.add(TeamMemberRole(user_id=member["user_id"], team_id=tenant_a["team_id"],
                          role_id=role.id))
    db.commit()

    c = client_as(member)
    assert c.get(f"/api/dimensions/terms?team_id={tenant_a['team_id']}").status_code == 200
    r = c.post(f"/api/dimensions/terms?team_id={tenant_a['team_id']}",
               json={"kind": KIND_INDUSTRY, "code": "x", "label": "X"})
    assert r.status_code == 403


def test_dimension_endpoints_require_authentication(client):
    assert client.get(
        f"/api/dimensions/terms?team_id={uuid.uuid4()}").status_code == 401


# ── Producers: the three facts that break a one-line canonicaliser ──────────

def test_one_raw_string_can_name_several_producers(db):
    """40 of 901 distinct raw names contain " / ". A canonicalise-to-one-string
    helper collapses those into a fictional company."""
    assert split_raw_name("Sinopec / PetroChina") == ["Sinopec", "PetroChina"]
    assert split_raw_name("BASF SE / Hexion / INEOS Melamines") == [
        "BASF SE", "Hexion", "INEOS Melamines"]

    resolved = resolve_raw_name(db, "Sinopec / PetroChina", alias_map={})
    db.commit()
    ids = [r.producer.id for r in resolved]
    try:
        assert len(resolved) == 2
        assert all(r.from_split for r in resolved)
        # The whole original string stays resolvable, so a later lookup of the
        # exact source value finds every company it named.
        again = resolve_raw_name(db, "Sinopec / PetroChina", alias_map={})
        assert {r.producer.id for r in again} == set(ids)
    finally:
        _cleanup(db, producer_ids=ids)


def test_alias_resolution_walks_the_chain_to_a_fixpoint(db):
    """45 canonical values also appear as raw names, so a single lookup lands
    mid-chain."""
    alias_map = {"BASF SE": "BASF Group", "BASF Group": "BASF"}
    resolved = resolve_raw_name(db, "BASF SE", alias_map=alias_map)
    db.commit()
    ids = [r.producer.id for r in resolved]
    try:
        assert len(resolved) == 1
        assert resolved[0].producer.name == "BASF"
        assert resolved[0].minted is False
    finally:
        _cleanup(db, producer_ids=ids)


def test_a_self_referential_alias_chain_cannot_hang_the_load(db):
    resolved = resolve_raw_name(db, "A", alias_map={"A": "B", "B": "A"})
    db.commit()
    ids = [r.producer.id for r in resolved]
    try:
        assert len(resolved) == 1
    finally:
        _cleanup(db, producer_ids=ids)


def test_an_unmapped_name_mints_a_producer_and_says_it_did(db):
    """The alias map covers under a third of the rows, so dropping the remainder
    would lose most of the data while reporting success."""
    name = f"Unmapped Chemicals {uuid.uuid4().hex[:6]}"
    resolved = resolve_raw_name(db, name, alias_map={})
    db.commit()
    ids = [r.producer.id for r in resolved]
    try:
        assert len(resolved) == 1
        assert resolved[0].minted is True
        assert resolved[0].producer.name == name
    finally:
        _cleanup(db, producer_ids=ids)


def test_a_product_line_qualifier_does_not_fork_the_company(db):
    """"BASF (Uvinul line)" is BASF — the parenthetical is stripped for matching
    while the raw string is preserved on the alias row."""
    stem = f"Acme {uuid.uuid4().hex[:6]}"
    first = resolve_raw_name(db, stem, alias_map={})
    second = resolve_raw_name(db, f"{stem} (Uvinul line)", alias_map={})
    db.commit()
    ids = {r.producer.id for r in first + second}
    try:
        assert len(ids) == 1, "the qualifier must not create a second company"
        raws = {a.raw_value for a in db.query(ProducerAlias).filter(
            ProducerAlias.producer_id == first[0].producer.id).all()}
        assert f"{stem} (Uvinul line)" in raws, "the qualifier is preserved, not lost"
    finally:
        _cleanup(db, producer_ids=list(ids))


def test_share_zero_is_stored_as_not_disclosed(db):
    """2,215 of 2,237 source rows carry 0, and several notes say the breakdown
    is not public. Storing the number alone ships "BASF — 0% market share"."""
    resolved = resolve_raw_name(db, f"ShareCo {uuid.uuid4().hex[:6]}", alias_map={})
    db.commit()
    producer = resolved[0].producer
    try:
        undisclosed, _ = upsert_producer_formula(
            db, producer, subject_code="X-1", share=0)
        real, _ = upsert_producer_formula(
            db, producer, subject_code="X-2", share=18.5)
        db.commit()
        assert undisclosed.share_disclosed is False
        assert undisclosed.share_pct is None, "0 must never be stored as a real zero"
        assert real.share_disclosed is True
        assert float(real.share_pct) == pytest.approx(18.5)
    finally:
        _cleanup(db, producer_ids=[producer.id])


def test_the_producer_endpoints_answer_what_a_producer_makes(db, tenant_a, client_as):
    """The question `Supplier` could never answer: its `team_id` is NOT NULL
    under strict tenant, so it has no row shape for a company that exists
    independently of a team buying from it."""
    name = f"MakerCo {uuid.uuid4().hex[:6]}"
    resolved = resolve_raw_name(db, name, alias_map={})
    db.commit()
    producer = resolved[0].producer
    try:
        upsert_producer_formula(db, producer, subject_code="MADE-1", share=0,
                                regions_raw=["EU", "NA"], raw_name=name)
        db.commit()

        c = client_as(tenant_a)
        listing = c.get(f"/api/dimensions/producers?team_id={tenant_a['team_id']}"
                        f"&q={name.split()[0].lower()}")
        assert listing.status_code == 200, listing.text
        assert any(p["name"] == name for p in listing.json())

        detail = c.get(f"/api/dimensions/producers/{producer.id}"
                       f"?team_id={tenant_a['team_id']}").json()
        assert detail["name"] == name
        assert [p["subject_code"] for p in detail["portfolio"]] == ["MADE-1"]
        assert detail["portfolio"][0]["share_disclosed"] is False
        assert detail["portfolio"][0]["regions_raw"] == ["EU", "NA"]
    finally:
        _cleanup(db, producer_ids=[producer.id])


# ── 2. The decision file, on the shipped round-trip mechanism ───────────────

def test_the_decision_payload_is_registered_on_the_shipped_mechanism(db):
    """AC2. The analyst-decided parts live in a reviewable file, and a second
    payload is a spec plus one registry line — the mechanism never branched."""
    spec = get_spec("dimension_decision")
    assert spec.permission_key == "dimensions.edit"
    assert [c.name for c in spec.key_columns] == ["kind", "raw_value"]
    # Exactly one thing the human owns.
    assert [c.name for c in spec.editable_columns] == ["term_code"]
    # And the context is locked, so an edit there is reported, never applied.
    assert {c.name for c in spec.readonly_columns} == {
        "occurrences", "sample_subjects", "reason"}


def test_applying_a_decision_creates_the_alias_that_makes_a_load_resolve(db):
    """Re-importing the file is what changes the DB — the loader keeps no
    branches for the analyst's judgement."""
    spec = get_spec("dimension_decision")
    term = _term(db, KIND_INDUSTRY, f"dec-{uuid.uuid4().hex[:8]}", "Water Treatment")
    record_unresolved(db, KIND_INDUSTRY, "Water & wastewater treatment",
                      "SUBJ-1", "no analyst mapping")
    db.commit()
    try:
        row_key = {"kind": KIND_INDUSTRY, "raw_value": "Water & wastewater treatment"}
        # Undecided reads as "", which is what the export carries.
        assert spec.get_current_value(db, row_key, "term_code") == ""

        spec.apply_change(db, row_key, "term_code", term.code)
        db.commit()
        assert spec.get_current_value(db, row_key, "term_code") == term.code
        assert resolve_raw(db, KIND_INDUSTRY,
                           "Water & Wastewater Treatment").term_id == term.id

        # A blank decision is a partially filled sheet, not an error to apply.
        with pytest.raises(ValueError):
            spec.apply_change(db, row_key, "term_code", "")
        # And a term that does not exist is refused rather than silently created.
        with pytest.raises(ValueError):
            spec.apply_change(db, row_key, "term_code", "no-such-term")
    finally:
        db.rollback()
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM dimension_unresolved WHERE normalized = :n"),
                   {"n": normalize_value("Water & wastewater treatment")})
        db.execute(text("DELETE FROM dimension_terms WHERE id = :i"), {"i": str(term.id)})
        db.commit()


def test_the_decision_export_only_lists_undecided_values(db):
    spec = get_spec("dimension_decision")
    record_unresolved(db, KIND_INDUSTRY, f"Cross-sector {uuid.uuid4().hex[:6]}",
                      "SUBJ-1", "no mapping")
    db.commit()
    try:
        rows = spec.query_rows(db, spec.filter_schema(kind=KIND_INDUSTRY))
        assert rows
        # The editable column always exports blank, so the diff IS the answer.
        assert all(r["term_code"] == "" for r in rows)
        assert all(r["kind"] == KIND_INDUSTRY for r in rows)
        # Ranked, so an analyst can work the top of the queue.
        counts = [r["occurrences"] for r in rows]
        assert counts == sorted(counts, reverse=True)

        filtered = spec.query_rows(
            db, spec.filter_schema(kind=KIND_INDUSTRY, min_occurrences=10 ** 6))
        assert filtered == []
    finally:
        db.rollback()
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM dimension_unresolved WHERE kind = :k "
                        "AND raw_value LIKE 'Cross-sector %'"), {"k": KIND_INDUSTRY})
        db.commit()


# ── Orphan repair (SCRUM-77 follow-up) ───────────────────────────────────────

def test_repair_deletes_only_what_nothing_supports(db):
    """The predicate has two halves and both are load-bearing.

    An earlier loader pass wrote assertions against aliases that were later
    rebuilt; the FK nulled out and the never-delete rule kept the rows, leaving
    141 live `industry` assertions whose raw value has nothing to do with the
    term they sit under. But *plenty* of sound rows also have no alias —
    `functionality_family` has 56 — so deleting on "no alias" alone would
    destroy real data. Only a row failing both halves is unsupported by
    anything.
    """
    from app.services.dimension_repair import repair

    good = _term(db, "industry", f"repair-good-{uuid.uuid4().hex[:6]}", "Repair Good")
    other = _term(db, "industry", f"repair-other-{uuid.uuid4().hex[:6]}", "Repair Other")
    upsert_alias(db, good, good.label, source="taxonomy")
    upsert_alias(db, other, other.label, source="taxonomy")
    db.commit()
    try:
        # 1. Sound and aliased.
        assert_term(db, good, subject_type="formula", subject_code="RPR-ALIASED",
                    raw_value=good.label,
                    matched_alias=resolve_raw(db, "industry", good.label))
        # 2. No alias recorded, but the raw value still resolves to this term.
        #    Sound — the mapping exists, only the back-link is missing.
        a2 = assert_term(db, good, subject_type="formula", subject_code="RPR-ALIASLESS",
                         raw_value=good.label)
        a2.matched_alias_id = None
        # 3. No alias, and the raw value belongs to a DIFFERENT term. Orphan.
        a3 = assert_term(db, good, subject_type="formula", subject_code="RPR-ORPHAN",
                         raw_value=other.label)
        a3.matched_alias_id = None
        # 4. No alias and no raw value at all — unjudgeable, must be kept.
        a4 = assert_term(db, good, subject_type="formula", subject_code="RPR-NORAW")
        a4.matched_alias_id = None
        a4.raw_value = None
        db.commit()

        # Scoped to this test's own terms. conftest imports SessionLocal
        # directly, so the suite runs against the SAME database as the app: an
        # unscoped `apply=True` here deletes real catalogue rows as a side
        # effect. It did, the first time this test ran.
        scope = [good.code, other.code]
        dry = repair(db, term_codes=scope, apply=False)
        mine = {o.subject_code for o in dry.orphans}
        assert "RPR-ORPHAN" in mine
        assert "RPR-ALIASLESS" not in mine, "an alias-less row that still resolves is sound"
        assert "RPR-ALIASED" not in mine
        assert "RPR-NORAW" not in mine, "nothing to judge is not the same as wrong"
        assert dry.dry_run is True
        db.rollback()

        # The dry run wrote nothing.
        survivors = {a.subject_code for a in db.query(DimensionAssertion)
                     .filter(DimensionAssertion.term_id == good.id).all()}
        assert {"RPR-ALIASED", "RPR-ALIASLESS", "RPR-ORPHAN", "RPR-NORAW"} <= survivors

        applied = repair(db, term_codes=scope, apply=True)
        db.commit()
        assert applied.deleted >= 1
        after = {a.subject_code for a in db.query(DimensionAssertion)
                 .filter(DimensionAssertion.term_id == good.id).all()}
        assert "RPR-ORPHAN" not in after
        assert {"RPR-ALIASED", "RPR-ALIASLESS", "RPR-NORAW"} <= after

        # Idempotent: the rows it would match are gone.
        again = repair(db, term_codes=scope, apply=True)
        db.commit()
        assert not [o for o in again.orphans if o.subject_code == "RPR-ORPHAN"]
    finally:
        db.rollback()
        bypass_rls_var.set(True)
        db.query(DimensionAssertion).filter(
            DimensionAssertion.subject_code.in_(
                ["RPR-ALIASED", "RPR-ALIASLESS", "RPR-ORPHAN", "RPR-NORAW"])).delete(
            synchronize_session=False)
        db.commit()
        _cleanup(db, term_ids=[good.id, other.id])


def test_repair_scope_is_honoured_so_a_run_cannot_reach_past_it(db):
    """A guard on the tool, not just on this test.

    The suite shares a database with the app, and `repair` deletes. Scoping has
    to actually restrict what is scanned, or a targeted repair silently becomes
    a library-wide one.
    """
    from app.services.dimension_repair import find_orphans

    a = _term(db, "industry", f"scope-a-{uuid.uuid4().hex[:6]}", "Scope A")
    b = _term(db, "industry", f"scope-b-{uuid.uuid4().hex[:6]}", "Scope B")
    upsert_alias(db, a, a.label, source="taxonomy")
    upsert_alias(db, b, b.label, source="taxonomy")
    db.commit()
    try:
        for term, code in ((a, "SCOPE-A-ORPHAN"), (b, "SCOPE-B-ORPHAN")):
            row = assert_term(db, term, subject_type="formula", subject_code=code,
                              raw_value="a value no term claims")
            row.matched_alias_id = None
        db.commit()

        only_a = find_orphans(db, term_codes=[a.code])
        subjects = {o.subject_code for o in only_a.orphans}
        assert "SCOPE-A-ORPHAN" in subjects
        assert "SCOPE-B-ORPHAN" not in subjects, "scope leaked past the named term"
        assert only_a.scanned == 1, "scanned must be scoped too, or the report lies"

        both = find_orphans(db, term_codes=[a.code, b.code])
        assert {"SCOPE-A-ORPHAN", "SCOPE-B-ORPHAN"} <= {o.subject_code for o in both.orphans}
    finally:
        db.rollback()
        bypass_rls_var.set(True)
        db.query(DimensionAssertion).filter(
            DimensionAssertion.subject_code.in_(
                ["SCOPE-A-ORPHAN", "SCOPE-B-ORPHAN"])).delete(synchronize_session=False)
        db.commit()
        _cleanup(db, term_ids=[a.id, b.id])
