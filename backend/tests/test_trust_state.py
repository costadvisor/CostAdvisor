"""Expert sign-off + derived trust state (Wave 3, SCRUM-78 / INT-4).

The ticket's acceptance criteria, as tests:

1. the review endpoint writes a **users FK**, the read path returns the
   reviewer's display identity, and that identity still resolves after the user
   changes their email;
2. a combo whose inputs all resolve with no proxy and a closed weight set grades
   at the **top** tier with no human action; a combo with a type-code that
   resolves to no series grades at the **bottom** and the response **names the
   offending code(s)**;
3. the grade is stored in its own field — **neither `coverage_tier` column is
   overwritten** by it;
4. the queue endpoint returns unreviewed combos **across the whole library**
   filtered by grade, and is orderable by something other than region/name;
5. editing a component weight on a signed-off combo returns it to the queue; a
   recompute that changes nothing leaves the sign-off intact;
6. a user holding only `formulas.edit` gets 403; a user holding the approve
   permission succeeds;
7. signing off a platform combo produces an audit record **without attributing
   it to an unrelated team**;
8. nothing reads `data_confidence` as the driver of `needs_review` any more.
"""
from __future__ import annotations

import pathlib
import uuid

import pytest
from sqlalchemy import text

from app.constants.trust import (
    GRADE_BLOCKED, GRADE_HIGH, GRADE_LOW, GRADE_MEDIUM, GRADE_UNRATED,
    GRADES_NEEDING_REVIEW, PROVENANCE_STATES, PROXY_STATUS_SOURCE,
    REASON_NO_SERIES, REASON_PROXY, REASON_PROXY_DISAGREEMENT,
    REASON_WEIGHTS_OPEN, TRUST_GRADES,
)
from app.database import bypass_rls_var
from app.models.audit_log import AuditLog
from app.models.formula_template import (
    FormulaRegionCoverage, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.index_data import CommodityIndex
from app.models.index_layer import TypeCode
from app.models.rbac import (
    Permission, Role, RolePermission, TeamMemberRole, UserPlatformRole,
)
from app.models.team import TeamMembership
from app.models.user import User
from app.services.audit import PLATFORM_TEAM_SENTINEL
from app.services.trust import (
    apply_assessment, assess, fingerprint_for, coverage_lines, recompute_all,
    review_queue, sign_off,
)

REPO = pathlib.Path(__file__).resolve().parents[2]


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _series(db) -> CommodityIndex:
    ci = CommodityIndex(name=f"ts-{uuid.uuid4().hex[:8]}", scrape_enabled=False)
    db.add(ci)
    db.flush()
    return ci


def _code(db, series, *, resolution="resolved", proxy_status="direct") -> TypeCode:
    tc = TypeCode(
        code=f"TC-{uuid.uuid4().hex[:8]}", resolution=resolution,
        resolves_to_id=series.id if series else None, proxy_status=proxy_status,
    )
    db.add(tc)
    db.flush()
    return tc


_UNSET = object()


def _combo(db, *, team_id=None, created_by, lines, region="Europe",
           coverage_tier="P1", proxy_density_tier="P2", line_region=_UNSET):
    """A platform (or team) template with one region's coverage and a line set.

    `lines` is `[(weight, type_code_or_None, component_type, line_is_proxy)]`.
    `line_region` defaults to the coverage's region; pass None to build the
    region-NULL (template-level) set the components API actually rewrites.
    """
    tpl = FormulaTemplate(
        team_id=team_id, created_by=created_by,
        name=f"tpl-{uuid.uuid4().hex[:6]}", code=f"T-{uuid.uuid4().hex[:8]}",
        expression=None,
    )
    db.add(tpl)
    db.flush()
    cov = FormulaRegionCoverage(
        template_id=tpl.id, region=region, base_price=1000,
        coverage_tier=coverage_tier, proxy_density_tier=proxy_density_tier,
    )
    db.add(cov)
    lines_region = region if line_region is _UNSET else line_region
    for i, (weight, code, ctype, line_proxy) in enumerate(lines):
        db.add(FormulaTemplateComponent(
            template_id=tpl.id, region=lines_region, variant="",
            name=f"line-{i}", component_type=ctype,
            commodity_id=code.resolves_to_id if code else None,
            type_code_id=code.id if code else None,
            weight_pct=weight, is_proxy=line_proxy, sort_order=i,
        ))
    db.commit()
    return tpl, cov


def _cleanup(db, *, template_ids=(), code_ids=(), series_ids=()):
    db.rollback()
    bypass_rls_var.set(True)
    for tid in template_ids:
        db.execute(text("DELETE FROM formula_templates WHERE id = :i"), {"i": str(tid)})
    for cid in code_ids:
        db.execute(text("DELETE FROM type_codes WHERE id = :i"), {"i": cid})
    for sid in series_ids:
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": sid})
    db.commit()


def _grant_approve(db, user_id):
    """Give a user the platform approve right via the Content Editor role."""
    role = db.query(Role).filter(Role.team_id.is_(None),
                                 Role.name == "Content Editor").one()
    db.add(UserPlatformRole(user_id=user_id, role_id=role.id))
    db.commit()


# ── 2. The derivation ───────────────────────────────────────────────────────

def test_a_clean_combo_grades_at_the_top_with_no_human_action(db, tenant_a):
    """AC2, first half."""
    s = _series(db)
    code = _code(db, s)
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(90, code, "index", False), (10, None, "fixed", None)])
    try:
        result = apply_assessment(db, cov)
        db.commit()
        assert result.grade == GRADE_HIGH
        assert result.needs_review is False
        assert cov.trust_inputs["reasons"] == []
        assert cov.trust_inputs["total_weight"] == pytest.approx(100.0)
        # Margin/fixed weight is inside the total and is not graded on resolution.
        assert cov.trust_inputs["indexed_weight"] == pytest.approx(90.0)
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


def test_a_no_series_input_grades_at_the_bottom_and_names_the_code(db, tenant_a):
    """AC2, second half — the reason it exists: an ungraded "low" tells a
    reviewer nothing about what to go and look at."""
    good_series, dry_series = _series(db), _series(db)
    good = _code(db, good_series)
    unbought = _code(db, dry_series, resolution="no_series")
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(60, good, "index", False),
                             (40, unbought, "index", False)])
    try:
        result = apply_assessment(db, cov)
        db.commit()
        assert result.grade == GRADE_BLOCKED
        assert result.needs_review is True

        reasons = {r["reason"]: r for r in cov.trust_inputs["reasons"]}
        assert REASON_NO_SERIES in reasons
        # Named type-codes, not a bare enum.
        assert reasons[REASON_NO_SERIES]["subjects"] == [unbought.code]
        assert reasons[REASON_NO_SERIES]["weight_pct"] == pytest.approx(40.0)
        assert cov.trust_inputs["blocked_weight"] == pytest.approx(40.0)
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[good.id, unbought.id],
                 series_ids=[good_series.id, dry_series.id])


def test_a_proxy_alone_does_not_put_a_combo_in_the_queue(db, tenant_a):
    """The failure mode the grade exists to avoid: "any proxy input means
    review" would put most of the library in the queue in one pass, because
    proxies are a large share of the resolution layer."""
    s = _series(db)
    proxy_code = _code(db, s, proxy_status="proxy")
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(30, proxy_code, "index", True),
                             (70, None, "fixed", None)])
    try:
        result = apply_assessment(db, cov)
        db.commit()
        assert result.grade == GRADE_MEDIUM
        assert result.needs_review is False
        assert GRADE_MEDIUM not in GRADES_NEEDING_REVIEW
        reasons = {r["reason"] for r in cov.trust_inputs["reasons"]}
        assert REASON_PROXY in reasons
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[proxy_code.id],
                 series_ids=[s.id])


def test_heavy_proxy_density_does_drop_to_low(db, tenant_a):
    s = _series(db)
    proxy_code = _code(db, s, proxy_status="proxy")
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(90, proxy_code, "index", True),
                             (10, None, "fixed", None)])
    try:
        result = apply_assessment(db, cov)
        db.commit()
        assert result.grade == GRADE_LOW
        assert result.needs_review is True
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[proxy_code.id],
                 series_ids=[s.id])


def test_the_proxy_status_disagreement_is_reported_not_hidden(db, tenant_a):
    """A substantial share of indexed cost lines carry a `proxy_status` that
    contradicts the one on their own type-code row, so whichever column is read
    the other disagrees. The registry side is authoritative and the
    contradiction is its own named reason."""
    s = _series(db)
    code = _code(db, s, proxy_status="proxy")
    # The line says direct; the registry says proxy.
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(40, code, "index", False), (60, None, "fixed", None)])
    try:
        apply_assessment(db, cov)
        db.commit()
        reasons = {r["reason"]: r for r in cov.trust_inputs["reasons"]}
        assert REASON_PROXY_DISAGREEMENT in reasons
        assert reasons[REASON_PROXY_DISAGREEMENT]["subjects"] == [code.code]
        # And every assessment names which column it believed.
        assert cov.trust_inputs["proxy_status_source"] == PROXY_STATUS_SOURCE
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


def test_an_open_weight_set_drops_to_low(db, tenant_a):
    """Margin sits inside the 100% total now, so the catalog band is 99.5–110.5
    — a different signal than when CONF-LOW meant "closed by proportional
    scaling"."""
    s = _series(db)
    code = _code(db, s)
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(60, code, "index", False)])
    try:
        result = apply_assessment(db, cov)
        db.commit()
        assert result.grade == GRADE_LOW
        reasons = {r["reason"]: r for r in cov.trust_inputs["reasons"]}
        assert REASON_WEIGHTS_OPEN in reasons
        assert "99.5" in reasons[REASON_WEIGHTS_OPEN]["detail"]
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


def test_nothing_to_grade_is_not_the_same_as_graded_badly(db, tenant_a):
    tpl, cov = _combo(db, created_by=tenant_a["user_id"], lines=[])
    try:
        result = apply_assessment(db, cov)
        db.commit()
        assert result.grade == GRADE_UNRATED
        # Nothing for an expert to look at, so it does not clog the queue.
        assert result.needs_review is False
        assert GRADE_UNRATED not in GRADES_NEEDING_REVIEW
    finally:
        _cleanup(db, template_ids=[tpl.id])


# ── 3. Its own field ────────────────────────────────────────────────────────

def test_neither_coverage_tier_column_is_overwritten_by_the_grade(db, tenant_a):
    """AC3. Either coverage column answers "how well covered is this"; the grade
    answers "is this worth a human's time", and coverage is an input to it."""
    s = _series(db)
    code = _code(db, s, resolution="no_series")
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(100, code, "index", False)],
                      coverage_tier="P1", proxy_density_tier="P3")
    try:
        apply_assessment(db, cov)
        db.commit()
        db.expire_all()
        row = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.id == cov.id).one()
        assert row.trust_grade == GRADE_BLOCKED
        assert row.coverage_tier == "P1", "the shipped coverage tier must survive"
        assert row.proxy_density_tier == "P3", "the drop's proxy density must survive"
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


# ── 1 + 6 + 7. The review endpoint ──────────────────────────────────────────

def test_review_writes_a_users_fk_that_survives_an_email_change(
        db, tenant_a, user_factory, client_as):
    """AC1. The column this replaces held `current_user.email`, so the record
    decayed the moment somebody changed their address."""
    reviewer = user_factory()
    _grant_approve(db, reviewer["user_id"])
    s = _series(db)
    code = _code(db, s, resolution="no_series")
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(100, code, "index", False)])
    try:
        apply_assessment(db, cov)
        db.commit()

        r = client_as(reviewer).post(
            f"/api/formulas/{tpl.id}/coverage/Europe/review")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["reviewed_by_id"] == str(reviewer["user_id"])
        assert body["needs_review"] is False
        assert body["reviewed_by_name"]
        assert body["review_fingerprint"]

        # Change the email; the identity still resolves.
        bypass_rls_var.set(True)
        user = db.query(User).filter(User.id == reviewer["user_id"]).one()
        user.email = f"moved-{uuid.uuid4().hex[:6]}@test.local"
        db.commit()

        after = client_as(tenant_a).get(
            f"/api/formulas/{tpl.id}/coverage?team_id={tenant_a['team_id']}").json()
        row = next(c for c in after if c["region"] == "Europe")
        assert row["reviewed_by_id"] == str(reviewer["user_id"])
        assert row["reviewed_by_name"]
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


def test_edit_permission_alone_cannot_sign_off(db, tenant_a, user_factory, client_as):
    """AC6, and the reason the split matters: `_require_template_edit` resolves
    to `formulas.edit`, so the person who authored the weights could also vouch
    for them — which is not a review."""
    author = user_factory()
    role = db.query(Role).filter(Role.team_id.is_(None), Role.name == "Chemist").first()
    if role is not None:
        db.add(UserPlatformRole(user_id=author["user_id"], role_id=role.id))
        db.commit()

    s = _series(db)
    code = _code(db, s)
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(100, code, "index", False)])
    try:
        # Chemist holds formulas.view/edit/delete — enough to author, not to approve.
        denied = client_as(author).post(f"/api/formulas/{tpl.id}/coverage/Europe/review")
        assert denied.status_code == 403, denied.text

        _grant_approve(db, author["user_id"])
        allowed = client_as(author).post(f"/api/formulas/{tpl.id}/coverage/Europe/review")
        assert allowed.status_code == 200, allowed.text
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


def test_a_platform_sign_off_is_audited_without_borrowing_a_tenant(
        db, tenant_a, user_factory, client_as):
    """AC7. The pattern this replaces picked *the first team the reviewer happens
    to belong to* — putting the event in an unrelated tenant's log — and skipped
    it entirely for a reviewer with no team."""
    reviewer = user_factory()
    _grant_approve(db, reviewer["user_id"])
    s = _series(db)
    code = _code(db, s)
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(100, code, "index", False)])
    try:
        assert tpl.team_id is None, "this test is about a platform template"
        r = client_as(reviewer).post(f"/api/formulas/{tpl.id}/coverage/Europe/review")
        assert r.status_code == 200, r.text

        bypass_rls_var.set(True)
        db.rollback()
        events = db.query(AuditLog).filter(
            AuditLog.entity_type == "formula_region_coverage",
            AuditLog.entity_id == f"{tpl.id}:Europe").all()
        assert events, "the platform sign-off was not recorded at all"
        # No tenant at all, rather than the reviewer's own or any other team.
        assert all(e.team_id is None for e in events)
        assert PLATFORM_TEAM_SENTINEL is None
        assert events[0].new_value["reviewed_by_id"] == str(reviewer["user_id"])
    finally:
        bypass_rls_var.set(True)
        db.rollback()
        db.execute(text("DELETE FROM audit_logs WHERE team_id IS NULL "
                        "AND entity_id = :e"), {"e": f"{tpl.id}:Europe"})
        db.commit()
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


# ── 5. The fingerprint ──────────────────────────────────────────────────────

def test_a_recompute_that_changes_nothing_leaves_the_sign_off_intact(db, tenant_a):
    """AC5, second half — the behaviour the existing seed-preservation test
    covers, asserted at the layer that now owns it."""
    s = _series(db)
    code = _code(db, s, resolution="no_series")
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(100, code, "index", False)])
    try:
        apply_assessment(db, cov)
        sign_off(db, cov, tenant_a["user_id"])
        db.commit()
        signed_at = cov.reviewed_at
        fingerprint = cov.review_fingerprint

        result = apply_assessment(db, cov)
        db.commit()
        assert result.sign_off_invalidated is False
        assert cov.reviewed_at == signed_at
        assert cov.review_fingerprint == fingerprint
        # A live sign-off outranks the derivation: an expert who accepted a
        # blocked combo should not be asked again every night.
        assert cov.trust_grade == GRADE_BLOCKED
        assert cov.needs_review is False
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


def test_changing_a_weight_returns_a_signed_off_combo_to_the_queue(db, tenant_a):
    """AC5, first half. Without the fingerprint the combo would keep a green
    tick for a recipe nobody reviewed."""
    s = _series(db)
    code = _code(db, s)
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(60, code, "index", False), (40, None, "fixed", None)])
    try:
        apply_assessment(db, cov)
        sign_off(db, cov, tenant_a["user_id"])
        db.commit()
        assert cov.reviewed_at is not None

        line = db.query(FormulaTemplateComponent).filter(
            FormulaTemplateComponent.template_id == tpl.id,
            FormulaTemplateComponent.component_type == "index").one()
        line.weight_pct = 65
        db.flush()

        result = apply_assessment(db, cov)
        db.commit()
        assert result.sign_off_invalidated is True
        assert cov.reviewed_at is None
        assert cov.reviewed_by_id is None
        assert cov.review_fingerprint is None
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


def test_reordering_a_recipe_is_not_a_change(db, tenant_a):
    """`sort_order` is presentation, not substance — re-queueing on a reorder
    would train reviewers to ignore the flag."""
    s = _series(db)
    code = _code(db, s)
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(60, code, "index", False), (40, None, "fixed", None)])
    try:
        before = fingerprint_for(coverage_lines(db, cov))
        for line in db.query(FormulaTemplateComponent).filter(
                FormulaTemplateComponent.template_id == tpl.id).all():
            line.sort_order = 10 - line.sort_order
        db.commit()
        assert fingerprint_for(coverage_lines(db, cov)) == before
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


def test_editing_the_recipe_through_the_api_re_queues_it(
        db, tenant_a, user_factory, client_as):
    """The endpoint path, not just the service: the components PUT has to
    regrade, or an edit made through the UI leaves a stale tick.

    Built on a **region-NULL** line set on purpose. The API's replace-all only
    touches region-NULL rows (unit 3b, so a seeded region-tagged recipe survives
    an edit), so a combo whose lines are region-tagged genuinely is not changed
    by this call — and asserting it was invalidated would have been asserting
    the wrong thing.
    """
    author = user_factory(is_super_admin=True)
    s = _series(db)
    code = _code(db, s)
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(60, code, "index", False), (40, None, "fixed", None)],
                      line_region=None)
    try:
        apply_assessment(db, cov)
        sign_off(db, cov, tenant_a["user_id"])
        db.commit()

        r = client_as(author).put(
            f"/api/formulas/{tpl.id}/components?team_id={author['team_id']}",
            json={"components": [
                {"name": "reworked", "component_type": "index",
                 "commodity_id": s.id, "weight_pct": 100},
            ]},
        )
        assert r.status_code == 200, r.text

        db.expire_all()
        row = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.id == cov.id).one()
        assert row.reviewed_at is None, "the sign-off should have been invalidated"
        assert row.review_fingerprint is None
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


# ── 4. The queue ────────────────────────────────────────────────────────────

def test_the_queue_spans_the_library_and_filters_by_grade(
        db, tenant_a, client_as):
    """AC4. Coverage was only listable per template before this, so a console
    had nothing to read."""
    s, dry = _series(db), _series(db)
    good = _code(db, s)
    unbought = _code(db, dry, resolution="no_series")
    clean_tpl, clean_cov = _combo(db, created_by=tenant_a["user_id"],
                                  lines=[(100, good, "index", False)])
    bad_tpl, bad_cov = _combo(db, created_by=tenant_a["user_id"],
                              lines=[(100, unbought, "index", False)])
    try:
        apply_assessment(db, clean_cov)
        apply_assessment(db, bad_cov)
        db.commit()

        c = client_as(tenant_a)
        body = c.get(f"/api/formulas/review-queue?team_id={tenant_a['team_id']}"
                     f"&grade={GRADE_BLOCKED}").json()
        codes = {row["template_code"] for row in body["rows"]}
        assert bad_tpl.code in codes
        assert clean_tpl.code not in codes, "a clean combo must not be queued"
        # The queue is across the library, not scoped to one template.
        assert body["total"] >= 1
        assert all(row["trust_grade"] == GRADE_BLOCKED for row in body["rows"])
        # And each row carries the "why".
        row = next(r for r in body["rows"] if r["template_code"] == bad_tpl.code)
        assert row["trust_inputs"]["reasons"][0]["subjects"] == [unbought.code]
    finally:
        _cleanup(db, template_ids=[clean_tpl.id, bad_tpl.id],
                 code_ids=[good.id, unbought.id], series_ids=[s.id, dry.id])


def test_the_queue_is_orderable_by_severity_not_just_name(db, tenant_a, client_as):
    """A queue ordered by region or name would have a reviewer reading
    alphabetically through something whose whole point is triage."""
    c = client_as(tenant_a)
    ordered = c.get(f"/api/formulas/review-queue?team_id={tenant_a['team_id']}"
                    f"&order_by=severity&limit=50").json()
    assert ordered["order_by"] == "severity"
    from app.constants.trust import GRADE_SEVERITY
    severities = [GRADE_SEVERITY.get(r["trust_grade"], 9) for r in ordered["rows"]]
    assert severities == sorted(severities), "worst first"

    for order in ("blocked_weight", "recipe_size", "code", "region"):
        r = c.get(f"/api/formulas/review-queue?team_id={tenant_a['team_id']}"
                  f"&order_by={order}&limit=5")
        assert r.status_code == 200, r.text
    bad = c.get(f"/api/formulas/review-queue?team_id={tenant_a['team_id']}"
                "&order_by=nonsense")
    assert bad.status_code == 422
    bad_grade = c.get(f"/api/formulas/review-queue?team_id={tenant_a['team_id']}"
                      "&grade=platinum")
    assert bad_grade.status_code == 422


def test_the_queue_route_is_not_parsed_as_a_template_id(db, tenant_a, client_as):
    """Registered ahead of the `/{template_id}` routes — otherwise
    `review-queue` would be parsed as a UUID and 422."""
    r = client_as(tenant_a).get(
        f"/api/formulas/review-queue?team_id={tenant_a['team_id']}")
    assert r.status_code == 200, r.text


def test_the_recompute_endpoint_is_super_admin(db, tenant_a, user_factory, client_as):
    assert client_as(tenant_a).post("/api/formulas/trust/recompute").status_code == 403
    admin = user_factory(is_super_admin=True)
    r = client_as(admin).post("/api/formulas/trust/recompute")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["considered"] >= 0
    assert set(body["by_grade"]) <= set(TRUST_GRADES)


# ── 8. Nothing drives needs_review off data_confidence ──────────────────────

def test_data_confidence_no_longer_drives_needs_review(db, tenant_a):
    """AC8. It was set by `data_confidence == "CONF-LOW"`; the July sheet
    dropped that column and `seed_catalog` resolves it to None, so the flag was
    never set again and the queue was empty."""
    s = _series(db)
    code = _code(db, s)
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(100, code, "index", False)])
    try:
        cov.data_confidence = "CONF-LOW"
        db.commit()
        result = apply_assessment(db, cov)
        db.commit()
        # A clean recipe stays out of the queue no matter what the legacy
        # confidence column says.
        assert result.grade == GRADE_HIGH
        assert result.needs_review is False
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


def test_no_source_file_still_derives_needs_review_from_data_confidence():
    """Checked against the AST, not with a grep.

    A grep over source text also matches the docstrings and migration notes that
    *describe* the change, so it reported three false positives on its first run.
    Walking the tree instead finds only real code: a dict entry keyed
    `needs_review`, or an assignment to a `needs_review` attribute/subscript,
    whose value expression mentions `data_confidence`.
    """
    import ast

    def mentions_confidence(node) -> bool:
        return any(
            (isinstance(n, ast.Name) and n.id == "data_confidence")
            or (isinstance(n, ast.Attribute) and n.attr == "data_confidence")
            or (isinstance(n, ast.Constant) and n.value == "data_confidence")
            for n in ast.walk(node)
        )

    offenders = []
    for path in (REPO / "backend").rglob("*.py"):
        if "venv" in path.parts or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (isinstance(key, ast.Constant) and key.value == "needs_review"
                            and mentions_confidence(value)):
                        offenders.append(f"{path.name}:{node.lineno} dict entry")
            elif isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {
                    t.attr if isinstance(t, ast.Attribute) else
                    (t.slice.value if isinstance(t, ast.Subscript)
                     and isinstance(t.slice, ast.Constant) else None)
                    for t in targets
                }
                if "needs_review" in names and mentions_confidence(node.value):
                    offenders.append(f"{path.name}:{node.lineno} assignment")
    assert offenders == [], offenders


# ── The shared vocabulary ───────────────────────────────────────────────────

def test_the_trust_and_provenance_vocabularies_have_one_home():
    """The ticket asks for one constants module so the editorial provenance
    states and the combo grade cannot drift into two spellings."""
    from app.constants import trust as canonical
    from app.models import editorial

    assert editorial.PROVENANCE_STATES is canonical.PROVENANCE_STATES
    assert editorial.PROVENANCE_BADGES is canonical.PROVENANCE_BADGES
    assert set(PROVENANCE_STATES) == {
        "imported", "ai_draft", "human_edited", "human_approved"}
    # The grade vocabulary is separate from provenance — they answer different
    # questions — but both live in the one module.
    assert set(TRUST_GRADES).isdisjoint(set(PROVENANCE_STATES))


def test_every_grade_has_a_caveat_decision(db):
    """The caveat text reads one field from this story, so a grade with no
    entry would silently render nothing where the mockup rendered a warning."""
    from app.constants.trust import GRADE_CAVEATS
    assert set(GRADE_CAVEATS) == set(TRUST_GRADES)
    assert GRADE_CAVEATS[GRADE_HIGH] is None
    assert all(GRADE_CAVEATS[g] for g in TRUST_GRADES if g != GRADE_HIGH)


# ── The fork decision ───────────────────────────────────────────────────────

def test_a_platform_sign_off_does_not_carry_into_a_fork(
        db, tenant_a, user_factory, client_as):
    """The open call the ticket asks to be made explicitly: it does not carry.
    The platform expert vouched for the platform numbers, and a fork exists so
    the team can change them."""
    s = _series(db)
    code = _code(db, s)
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(100, code, "index", False)])
    fork_id = None
    try:
        apply_assessment(db, cov)
        sign_off(db, cov, tenant_a["user_id"])
        db.commit()

        r = client_as(tenant_a).post(
            f"/api/formulas/{tpl.id}/fork",
            json={"team_id": str(tenant_a["team_id"])})
        assert r.status_code == 201, r.text
        fork_id = r.json()["id"]

        forked = db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.template_id == uuid.UUID(fork_id)).all()
        assert forked
        for row in forked:
            assert row.reviewed_at is None
            assert row.reviewed_by_id is None
            assert row.review_fingerprint is None
            # But the fork does get its own grade, derived from its own rows.
            assert row.trust_grade in TRUST_GRADES
    finally:
        ids = [tpl.id] + ([uuid.UUID(fork_id)] if fork_id else [])
        _cleanup(db, template_ids=ids, code_ids=[code.id], series_ids=[s.id])


# ── Recompute ───────────────────────────────────────────────────────────────

def test_recompute_is_idempotent_and_grades_everything(db, tenant_a):
    s = _series(db)
    code = _code(db, s)
    tpl, cov = _combo(db, created_by=tenant_a["user_id"],
                      lines=[(100, code, "index", False)])
    try:
        first = recompute_all(db)
        db.commit()
        assert first.considered >= 1
        assert sum(first.by_grade.values()) == first.considered

        second = recompute_all(db)
        db.commit()
        assert second.graded == 0, "a re-run should change nothing"
        assert second.invalidated == 0
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[code.id], series_ids=[s.id])


def test_the_queue_does_not_swallow_the_whole_library(db, tenant_a):
    """The ticket's warning, checked against real data: "any proxy input means
    review" would flag most of the library, and the flags cluster rather than
    spread because roughly a quarter of indexed cost weight resolves through one
    series. A graded queue has to be a minority of the whole."""
    report = recompute_all(db)
    db.commit()
    if report.considered < 50:
        pytest.skip("not enough loaded combos to make the ratio meaningful")
    queued = sum(report.by_grade.get(g, 0) for g in GRADES_NEEDING_REVIEW)
    assert queued < report.considered, "everything is queued"
    assert report.by_grade.get(GRADE_MEDIUM, 0) > 0, (
        "no combo landed at medium — the proxy tier is not doing its job of "
        "keeping softer signals out of the queue"
    )


def test_the_catalog_list_carries_the_grade_not_the_retired_confidence(
        db, tenant_a, client_as):
    """The list page renders one row per *template*, but trust is graded per
    (template, region) — so the list has to roll up, and it has to roll up to
    the **worst** region. A blocked region hiding behind a healthy one is the
    exact failure the old `catalog_meta.data_confidence` badge had after the
    July sheet stopped shipping that column: it went null everywhere and the
    badge silently rendered a dash for the entire catalog.
    """
    s_ok, s_dry = _series(db), _series(db)
    good = _code(db, s_ok)
    unbought = _code(db, s_dry, resolution="no_series")
    tpl, clean_cov = _combo(db, created_by=tenant_a["user_id"],
                            lines=[(100, good, "index", False)], region="Europe")
    # A second region on the SAME template, blocked.
    bad_cov = FormulaRegionCoverage(
        template_id=tpl.id, region="NA", base_price=100, currency="EUR",
        base_year=2024, base_quarter=1,
    )
    db.add(bad_cov)
    db.flush()
    db.add(FormulaTemplateComponent(
        template_id=tpl.id, region="NA", name="line", component_type="index",
        commodity_id=s_dry.id, type_code_id=unbought.id, weight_pct=100,
        is_proxy=False, sort_order=0,
    ))
    db.flush()
    try:
        apply_assessment(db, clean_cov)
        apply_assessment(db, bad_cov)
        db.commit()

        rows = client_as(tenant_a).get(
            f"/api/formulas/?team_id={tenant_a['team_id']}").json()
        row = next(r for r in rows if r["id"] == str(tpl.id))
        summary = row["trust_summary"]

        assert summary["worst_grade"] == GRADE_BLOCKED, (
            "the healthy Europe combo must not mask the blocked NA one"
        )
        assert summary["combo_count"] == 2
        assert summary["grades"][GRADE_HIGH] == 1
        assert summary["needs_review_count"] == 1
        # The hover names what to go and look at, not just a verdict.
        reasons = {r["reason"]: r["subjects"] for r in summary["reasons"]}
        assert unbought.code in reasons[REASON_NO_SERIES]
        # And it does not resurrect the retired column.
        assert row["catalog_meta"] is None or             row["catalog_meta"].get("data_confidence") is None
    finally:
        _cleanup(db, template_ids=[tpl.id], code_ids=[good.id, unbought.id],
                 series_ids=[s_ok.id, s_dry.id])


def test_the_queue_doubles_as_the_cross_library_coverage_index(db, tenant_a, client_as):
    """Omitting `needs_review` means no filter, not "only the queued ones".

    Unit 11 built this endpoint because coverage was listable only per template,
    so it is the cross-library coverage listing as well as the review queue — the
    Intelligence catalogue needs every (template, region) pair, and a default
    that silently hid the clean ones would give it a catalogue of defects.
    """
    s_ok, s_dry = _series(db), _series(db)
    good, unbought = _code(db, s_ok), _code(db, s_dry, resolution="no_series")
    clean_tpl, clean_cov = _combo(db, created_by=tenant_a["user_id"],
                                  lines=[(100, good, "index", False)])
    bad_tpl, bad_cov = _combo(db, created_by=tenant_a["user_id"],
                              lines=[(100, unbought, "index", False)])
    try:
        apply_assessment(db, clean_cov)
        apply_assessment(db, bad_cov)
        db.commit()

        c = client_as(tenant_a)
        base = f"/api/formulas/review-queue?team_id={tenant_a['team_id']}"

        # Scoped by grade so the assertion does not depend on how many combos
        # the library happens to hold — the point is the filter's presence, not
        # a page position.
        high = {r["template_code"] for r in
                c.get(f"{base}&grade={GRADE_HIGH}&limit=500").json()["rows"]}
        assert clean_tpl.code in high, "a clean combo must appear in the index"

        blocked = {r["template_code"] for r in
                   c.get(f"{base}&grade={GRADE_BLOCKED}&limit=500").json()["rows"]}
        assert bad_tpl.code in blocked

        queued_high = {r["template_code"] for r in
                       c.get(f"{base}&grade={GRADE_HIGH}&needs_review=true&limit=500").json()["rows"]}
        assert clean_tpl.code not in queued_high, "the explicit queue must still exclude clean combos"

        # And the index is strictly the larger of the two.
        assert (c.get(f"{base}&limit=1").json()["total"]
                >= c.get(f"{base}&needs_review=true&limit=1").json()["total"])
    finally:
        _cleanup(db, template_ids=[clean_tpl.id, bad_tpl.id],
                 code_ids=[good.id, unbought.id], series_ids=[s_ok.id, s_dry.id])
