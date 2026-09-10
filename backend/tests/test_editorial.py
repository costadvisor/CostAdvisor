"""Editorial block model + versioning + API (Wave 3, SCRUM-76 / INT-2).

Every acceptance criterion, as a test:

1. the two tables exist with the four-state provenance, nullable `template_id`
   and nullable `region`;
2. a block whose `subject_code` has no `formula_templates` row survives
   create → read → edit → read with `template_id` NULL throughout;
3. a `subject_type='index'` block with a region-baked slug stores and retrieves
   by slug;
4. an edit appends a version, the prior version is still readable by
   `version_no`, and the block points at the new one;
5. approve records approver + timestamp and sets `human_approved`, and a
   subsequent edit does not leave the row reading as approved;
6. a team member editing a platform block yields a team-owned block with
   `origin_id` set, the platform row unchanged and still readable by other
   teams — plus an RLS test proving one team's forks are invisible to another;
7. a user without the platform content-authoring permission gets 403 on
   platform writes;
8. the card returns every block type for a subject in one request, and the query
   count for one card **does not grow** with the number of block types present;
9. a fixture drawn from real drop records — a template-less key, a json-bodied
   type, a wildcard-region block, an index-slug block — round-trips unchanged.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
from sqlalchemy import event, text

from app.database import SessionLocal, bypass_rls_var, current_user_id_var
from app.models.editorial import (
    BLOCK_TYPES, PROVENANCE_BADGES, PROVENANCE_HUMAN_APPROVED, PROVENANCE_STATES,
    EditorialBlock, EditorialBlockVersion, subfamily_subject_code,
)
from app.models.index_data import CommodityIndex
from app.models.rbac import (
    Permission, Plan, PlanPermission, Role, RolePermission, TeamMemberRole,
    UserPlatformRole,
)
from app.models.team import TeamMembership
from app.services.editorial import read_card

DROP_RAW = (pathlib.Path(__file__).resolve().parents[2]
            / "sample_idea" / "costadvisor-data" / "raw")

needs_drop = pytest.mark.skipif(
    not DROP_RAW.exists(), reason="costadvisor-data drop not present in this checkout"
)

# A key that deliberately has no `formula_templates` row — 53 of 423
# `CURATED_CONTENT` keys are in this state, so it is the normal case, not an edge.
ORPHAN_CODE = "GRP-F22-LAR"


def _cleanup(db, *, block_ids=(), commodity_ids=()):
    db.rollback()
    bypass_rls_var.set(True)
    for bid in block_ids:
        db.execute(text("DELETE FROM editorial_blocks WHERE id = :i"), {"i": str(bid)})
    for cid in commodity_ids:
        db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": cid})
    db.commit()


def _post(client, tenant, **kw):
    body = {"subject_type": "formula", "subject_code": ORPHAN_CODE,
            "block_type": "supplier_note", "body_text": "hello",
            "body_format": "text", "provenance": "imported"}
    body.update(kw)
    return client.post(f"/api/editorial/blocks?team_id={tenant['team_id']}", json=body)


# ── 1. Shape ─────────────────────────────────────────────────────────────────

def test_the_four_provenance_states_exist_and_only_approval_clears_the_caveat():
    """The state machine and the customer-facing claim are the same thing, so
    the badge mapping ships with the model rather than being re-invented by
    whoever builds the surface."""
    assert set(PROVENANCE_STATES) == {
        "imported", "ai_draft", "human_edited", "human_approved",
    }
    # A bulk import is neither AI-draft nor human-approved — a two-state flag
    # would force it into a bucket where both readings are false.
    assert PROVENANCE_BADGES["imported"]["reviewed"] is False
    assert PROVENANCE_BADGES["human_edited"]["reviewed"] is False
    cleared = [k for k, v in PROVENANCE_BADGES.items() if v["caveat"] is None]
    assert cleared == [PROVENANCE_HUMAN_APPROVED]


def test_the_subfamily_subject_key_is_a_single_pipe():
    """Pinned here because the ticket named `|||`, which appears **zero** times
    anywhere in the drop — `SUBFAMILY_FUNCTIONALITY_OVERRIDE` keys all 33 of its
    entries as `Family|Subfamily`."""
    assert subfamily_subject_code("Oleochemicals", "Glycerine") == "Oleochemicals|Glycerine"
    assert "|||" not in subfamily_subject_code("A", "B")


# ── 2. Template-less subjects survive the whole round trip ──────────────────

def test_a_template_less_subject_round_trips_with_template_id_null(db, tenant_a, client_as):
    c = client_as(tenant_a)
    r = _post(c, tenant_a, body_text="Group roll-up note")
    assert r.status_code == 201, r.text
    created = r.json()
    bid = created["id"]
    try:
        # A hard FK here would not raise — the row would simply not be there
        # afterwards, and nothing could tell that from "never authored".
        assert created["template_id"] is None
        assert created["subject_code"] == ORPHAN_CODE

        got = c.get(f"/api/editorial/blocks/{bid}?team_id={tenant_a['team_id']}").json()
        assert got["template_id"] is None

        edited = c.put(f"/api/editorial/blocks/{bid}?team_id={tenant_a['team_id']}",
                       json={"body_text": "Revised roll-up note"}).json()
        assert edited["template_id"] is None
        assert edited["body_text"] == "Revised roll-up note"

        again = c.get(f"/api/editorial/blocks/{bid}?team_id={tenant_a['team_id']}").json()
        assert again["template_id"] is None
    finally:
        _cleanup(db, block_ids=[bid])


def test_a_subject_with_a_real_template_resolves_the_convenience_join(
        db, tenant_a, client_as):
    """The join is a convenience, not identity — but where it resolves, it must
    actually resolve, or the column is decoration."""
    from app.models.formula_template import FormulaTemplate

    tpl = db.query(FormulaTemplate).filter(
        FormulaTemplate.team_id.is_(None), FormulaTemplate.code.isnot(None)).first()
    if tpl is None:
        pytest.skip("no platform formula templates seeded in this DB")
    r = _post(client_as(tenant_a), tenant_a, subject_code=tpl.code)
    assert r.status_code == 201, r.text
    try:
        assert r.json()["template_id"] == str(tpl.id)
    finally:
        _cleanup(db, block_ids=[r.json()["id"]])


# ── 3. Index subjects ───────────────────────────────────────────────────────

def test_an_index_slug_block_stores_and_retrieves_by_slug(db, tenant_a, client_as):
    """Region is already baked into the slug where it applies, and `-ppi` /
    `-wb` / `-mb` suffixes are *sources*, not regions — so the slug is the
    identity and is never parsed apart."""
    slug = f"probe-series-{uuid.uuid4().hex[:8]}-ppi"
    ci = CommodityIndex(name=slug, commodity_key=slug, scrape_enabled=False)
    db.add(ci)
    db.commit()
    c = client_as(tenant_a)
    r = _post(c, tenant_a, subject_type="index", subject_code=slug,
              block_type="index_narrative", body_format="json",
              body_json={"why3m": "war shock", "why24m": "normalisation"})
    assert r.status_code == 201, r.text
    body = r.json()
    try:
        assert body["commodity_id"] == ci.id
        assert body["region"] is None, "region stays on the card, not the series"
        card = c.get(
            f"/api/editorial/cards/index/{slug}?team_id={tenant_a['team_id']}"
        ).json()
        assert card["blocks"]["index_narrative"]["body_json"]["why3m"] == "war shock"
    finally:
        _cleanup(db, block_ids=[body["id"]], commodity_ids=[ci.id])


# ── 4. Versioning ───────────────────────────────────────────────────────────

def test_an_edit_appends_a_version_and_the_prior_one_stays_readable(
        db, tenant_a, client_as):
    c = client_as(tenant_a)
    bid = _post(c, tenant_a, body_text="v1 text").json()["id"]
    try:
        after = c.put(f"/api/editorial/blocks/{bid}?team_id={tenant_a['team_id']}",
                      json={"body_text": "v2 text", "change_note": "tightened"}).json()
        assert after["current_version_no"] == 2
        assert after["body_text"] == "v2 text"

        versions = c.get(
            f"/api/editorial/blocks/{bid}/versions?team_id={tenant_a['team_id']}").json()
        assert [v["version_no"] for v in versions] == [1, 2]

        # Append-only: the earlier text is still there.
        v1 = c.get(
            f"/api/editorial/blocks/{bid}/versions/1?team_id={tenant_a['team_id']}").json()
        assert v1["body_text"] == "v1 text"
        assert v1["change_note"] == "initial"
    finally:
        _cleanup(db, block_ids=[bid])


def test_a_version_must_carry_the_body_its_format_declares(db, tenant_a, client_as):
    """A row claiming `json` with an empty `body_json` reads as authored and is
    not — refused at both the schema layer and a DB CHECK."""
    r = _post(client_as(tenant_a), tenant_a, body_format="json", body_json=None,
              body_text="prose")
    assert r.status_code == 422, r.text
    r2 = _post(client_as(tenant_a), tenant_a, body_format="text", body_text=None)
    assert r2.status_code == 422, r2.text


# ── 5. Approval ─────────────────────────────────────────────────────────────

def test_approve_records_the_approver_and_a_later_edit_clears_it(
        db, tenant_a, client_as):
    """Approval is what removes the customer-facing caveat, so a row must never
    keep reading as approved after its text changed."""
    c = client_as(tenant_a)
    bid = _post(c, tenant_a, body_text="draft").json()["id"]
    try:
        ok = c.post(f"/api/editorial/blocks/{bid}/approve?team_id={tenant_a['team_id']}")
        assert ok.status_code == 200, ok.text
        approved = ok.json()
        assert approved["provenance"] == "human_approved"
        assert approved["approved_by"] == str(tenant_a["user_id"])
        assert approved["approved_at"] is not None
        assert approved["badge"]["caveat"] is None
        assert approved["badge"]["reviewed"] is True

        after = c.put(f"/api/editorial/blocks/{bid}?team_id={tenant_a['team_id']}",
                      json={"body_text": "changed after sign-off"}).json()
        assert after["provenance"] == "human_edited"
        assert after["approved_by"] is None
        assert after["approved_at"] is None
        assert after["badge"]["reviewed"] is False
    finally:
        _cleanup(db, block_ids=[bid])


# ── 6. Platform vs team forking ─────────────────────────────────────────────

@pytest.fixture
def platform_block(db, tenant_a, user_factory, client_as):
    """A platform block, authored by a super admin (the only user who can write
    platform content without the Content Editor role)."""
    admin = user_factory(is_super_admin=True)
    r = _post(client_as(admin), admin, platform=True,
              body_text="platform supplier note", provenance="imported")
    assert r.status_code == 201, r.text
    bid = uuid.UUID(r.json()["id"])
    yield bid
    _cleanup(db, block_ids=[bid])


def test_a_team_editing_a_platform_block_gets_a_fork(
        db, tenant_a, tenant_b, client_as, platform_block):
    c = client_as(tenant_a)
    fork = c.put(f"/api/editorial/blocks/{platform_block}?team_id={tenant_a['team_id']}",
                 json={"body_text": "our own wording"})
    assert fork.status_code == 200, fork.text
    body = fork.json()
    try:
        assert body["id"] != str(platform_block)
        assert body["team_id"] == str(tenant_a["team_id"])
        assert body["origin_id"] == str(platform_block)
        assert body["body_text"] == "our own wording"

        # The platform row every other team reads is untouched.
        bypass_rls_var.set(True)
        db.expire_all()
        original = db.query(EditorialBlock).filter(
            EditorialBlock.id == platform_block).one()
        assert original.team_id is None
        assert original.current_version.body_text == "platform supplier note"

        # And still readable by another team.
        other = client_as(tenant_b).get(
            f"/api/editorial/blocks/{platform_block}?team_id={tenant_b['team_id']}")
        assert other.status_code == 200, other.text
        assert other.json()["body_text"] == "platform supplier note"
    finally:
        _cleanup(db, block_ids=[body["id"]])


def test_a_fork_starts_unapproved_even_from_an_approved_original(
        db, tenant_a, user_factory, client_as, platform_block):
    admin = user_factory(is_super_admin=True)
    client_as(admin).post(
        f"/api/editorial/blocks/{platform_block}/approve?team_id={admin['team_id']}")

    fork = client_as(tenant_a).put(
        f"/api/editorial/blocks/{platform_block}?team_id={tenant_a['team_id']}",
        json={"body_text": "team wording"}).json()
    try:
        # The sign-off was on the platform text, and the team just changed it.
        assert fork["provenance"] == "human_edited"
        assert fork["approved_by"] is None
    finally:
        _cleanup(db, block_ids=[fork["id"]])


def test_rls_hides_one_teams_forks_from_another(db, tenant_a, tenant_b, platform_block):
    """The policy itself, not the app-layer gate: platform rows are readable by
    all, team forks are not."""
    from app.services.editorial import fork_block

    bypass_rls_var.set(True)
    block = db.query(EditorialBlock).filter(EditorialBlock.id == platform_block).one()
    fork_a = fork_block(db, block, tenant_a["team_id"])
    db.commit()
    fork_a_id = fork_a.id
    try:
        s = SessionLocal()
        bypass_rls_var.set(False)
        current_user_id_var.set(str(tenant_b["user_id"]))
        try:
            visible = {b.id for b in s.query(EditorialBlock).all()}
            assert platform_block in visible, "platform content must stay visible"
            assert fork_a_id not in visible, "another team's fork must be invisible"
            # Versions inherit visibility transitively through the parent block.
            fork_versions = (
                s.query(EditorialBlockVersion)
                .filter(EditorialBlockVersion.block_id == fork_a_id).count()
            )
            assert fork_versions == 0
        finally:
            s.close()
            bypass_rls_var.set(True)
    finally:
        _cleanup(db, block_ids=[fork_a_id])


# ── 7. Platform authoring permission ───────────────────────────────────────

def test_platform_writes_are_refused_without_the_platform_permission(
        db, tenant_a, client_as):
    """Team `content.edit` is not platform authoring: "may you write the library
    everybody reads" is a different question from "may you write your own copy"."""
    r = _post(client_as(tenant_a), tenant_a, platform=True)
    assert r.status_code == 403, r.text
    assert "Platform permission" in r.json()["detail"]


def test_the_content_editor_platform_role_can_author_platform_content(
        db, tenant_a, client_as):
    """The migration seeds this role because without it only a super admin could
    author platform content, which is not a workable editorial process."""
    role = db.query(Role).filter(Role.team_id.is_(None),
                                 Role.name == "Content Editor").first()
    assert role is not None, "the migration should seed the Content Editor role"
    granted = {
        p.key for p in db.query(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .filter(RolePermission.role_id == role.id).all()
    }
    assert {"content.view", "content.edit", "content.approve"} <= granted

    db.add(UserPlatformRole(user_id=tenant_a["user_id"], role_id=role.id))
    db.commit()
    r = _post(client_as(tenant_a), tenant_a, platform=True,
              subject_code=f"{ORPHAN_CODE}-editor")
    assert r.status_code == 201, r.text
    try:
        assert r.json()["team_id"] is None
    finally:
        _cleanup(db, block_ids=[r.json()["id"]])


def test_a_member_with_view_only_cannot_write(db, tenant_a, user_factory, client_as):
    member = user_factory()
    db.add(TeamMembership(user_id=member["user_id"], team_id=tenant_a["team_id"],
                          role="member"))
    role = Role(team_id=tenant_a["team_id"], name=f"Reader-{uuid.uuid4().hex[:6]}")
    db.add(role)
    db.flush()
    view = db.query(Permission).filter(Permission.key == "content.view").one()
    db.add(RolePermission(role_id=role.id, permission_id=view.id))
    db.add(TeamMemberRole(user_id=member["user_id"], team_id=tenant_a["team_id"],
                          role_id=role.id))
    db.commit()

    c = client_as(member)
    assert c.get(f"/api/editorial/blocks?team_id={tenant_a['team_id']}").status_code == 200
    r = c.post(f"/api/editorial/blocks?team_id={tenant_a['team_id']}",
               json={"subject_type": "formula", "subject_code": ORPHAN_CODE,
                     "block_type": "supplier_note", "body_text": "x"})
    assert r.status_code == 403, r.text


def test_the_content_and_dimensions_keys_exist_and_are_plan_granted(db):
    """The plan ceiling is applied BEFORE roles, so a key missing from a team's
    plan is denied for every non-super-admin no matter their role — a new
    category that is not plan-granted ships silently disabled."""
    keys = {"content.view", "content.edit", "content.approve", "content.delete",
            "dimensions.view", "dimensions.edit", "dimensions.delete"}
    rows = db.query(Permission).filter(Permission.key.in_(keys)).all()
    assert {r.key for r in rows} == keys

    dream = db.query(Plan).filter(Plan.name == "Dream Plan").first()
    if dream:
        granted = {
            p.key for p in db.query(Permission)
            .join(PlanPermission, PlanPermission.permission_id == Permission.id)
            .filter(PlanPermission.plan_id == dream.id, Permission.key.in_(keys)).all()
        }
        assert granted == keys
    free = db.query(Plan).filter(Plan.name == "Free").first()
    if free:
        granted = {
            p.key for p in db.query(Permission)
            .join(PlanPermission, PlanPermission.permission_id == Permission.id)
            .filter(PlanPermission.plan_id == free.id, Permission.key.in_(keys)).all()
        }
        # Free is "view and export", so the read keys and nothing else.
        assert granted == {"content.view", "dimensions.view"}


def test_editorial_endpoints_require_authentication(client):
    assert client.get(
        f"/api/editorial/blocks?team_id={uuid.uuid4()}").status_code == 401


# ── 8. The card read, and its query budget ─────────────────────────────────

def _count_queries(db, fn):
    """Count the statements `fn` issues **against the editorial tables**.

    Counted around the service call rather than the endpoint, so auth and
    permission lookups do not mask the claim. Filtered to the editorial tables
    because `database.py` has an `after_begin` listener that emits `SET LOCAL`
    on every new transaction — so a measurement taken right after a commit
    carries one extra statement that a measurement inside an open transaction
    does not, which has nothing to do with the card read.
    """
    calls = []

    def before(conn, cursor, statement, params, context, executemany):
        if "editorial_block" in statement:
            calls.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", before)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    return result, len(calls)


def test_the_card_query_count_does_not_grow_with_the_number_of_block_types(
        db, tenant_a, client_as):
    """The read-path decision, asserted: `current_version_id` makes the card one
    query joined to versions, so adding block types cannot add round trips. A
    per-block-type read would have been one query per type per card, and the
    library renders a page of cards at once."""
    c = client_as(tenant_a)
    code = f"CARD-{uuid.uuid4().hex[:8]}"
    ids = []
    try:
        two = ["supplier_note", "compliance"]
        for bt in two:
            ids.append(_post(c, tenant_a, subject_code=code, block_type=bt,
                             body_text=f"{bt} text").json()["id"])
        db.expire_all()
        card_two, n_two = _count_queries(
            db, lambda: read_card(db, "formula", code, tenant_a["team_id"]))
        assert len(card_two.blocks) == 2

        for bt in ["functionalities", "applications", "macro_drivers",
                   "substitution", "synthesis_route"]:
            ids.append(_post(c, tenant_a, subject_code=code, block_type=bt,
                             body_text=f"{bt} text").json()["id"])
        db.expire_all()
        card_seven, n_seven = _count_queries(
            db, lambda: read_card(db, "formula", code, tenant_a["team_id"]))
        assert len(card_seven.blocks) == 7

        assert n_two == n_seven, (
            f"{n_two} queries for 2 block types vs {n_seven} for 7 — the card "
            "read is scaling with the vocabulary"
        )
        assert n_seven == 1, f"expected a single query, got {n_seven}"
    finally:
        _cleanup(db, block_ids=ids)


def test_the_card_returns_every_block_type_in_one_request(db, tenant_a, client_as):
    c = client_as(tenant_a)
    code = f"CARD-{uuid.uuid4().hex[:8]}"
    ids = []
    try:
        for bt in ("supplier_note", "compliance", "supply", "demand"):
            ids.append(_post(c, tenant_a, subject_code=code, block_type=bt,
                             body_text=f"{bt} text").json()["id"])
        card = c.get(
            f"/api/editorial/cards/formula/{code}?team_id={tenant_a['team_id']}").json()
        assert set(card["blocks"]) == {"supplier_note", "compliance", "supply", "demand"}
        # Two calls by design — the derived numbers are SCRUM-75's endpoint, and
        # naming it keeps a consumer from thinking the card lost half its content.
        assert card["derived_payload_endpoint"]
        assert all(v == "team:wildcard" for v in card["resolved_from"].values())
    finally:
        _cleanup(db, block_ids=ids)


def test_a_region_specific_block_wins_over_the_wildcard(db, tenant_a, client_as):
    """Only `CURRENT_EVENTS_OUTLOOK` carries a region key today and it is always
    `"*"`, but the consumer reads `entry[region] || entry['*']` — so the
    dimension has to survive the load, and the precedence is resolved
    server-side rather than left to each caller."""
    c = client_as(tenant_a)
    code = f"CARD-{uuid.uuid4().hex[:8]}"
    ids = []
    try:
        ids.append(_post(c, tenant_a, subject_code=code, block_type="current_events",
                         body_text="global view").json()["id"])
        ids.append(_post(c, tenant_a, subject_code=code, block_type="current_events",
                         region="Europe", body_text="Europe view").json()["id"])

        wild = c.get(
            f"/api/editorial/cards/formula/{code}?team_id={tenant_a['team_id']}").json()
        assert wild["blocks"]["current_events"]["body_text"] == "global view"
        assert wild["resolved_from"]["current_events"] == "team:wildcard"

        eu = c.get(f"/api/editorial/cards/formula/{code}"
                   f"?team_id={tenant_a['team_id']}&region=Europe").json()
        assert eu["blocks"]["current_events"]["body_text"] == "Europe view"
        assert eu["resolved_from"]["current_events"] == "team:region"

        # A region with no specific block falls back to the wildcard.
        na = c.get(f"/api/editorial/cards/formula/{code}"
                   f"?team_id={tenant_a['team_id']}&region=NA").json()
        assert na["blocks"]["current_events"]["body_text"] == "global view"
    finally:
        _cleanup(db, block_ids=ids)


def test_a_team_fork_shadows_the_platform_block_on_the_card(
        db, tenant_a, client_as, platform_block):
    fork = client_as(tenant_a).put(
        f"/api/editorial/blocks/{platform_block}?team_id={tenant_a['team_id']}",
        json={"body_text": "our wording"}).json()
    try:
        card = client_as(tenant_a).get(
            f"/api/editorial/cards/formula/{ORPHAN_CODE}"
            f"?team_id={tenant_a['team_id']}").json()
        block = card["blocks"]["supplier_note"]
        # A fork exists precisely to override, so both existing is the normal
        # case rather than a conflict.
        assert block["id"] == fork["id"]
        assert block["body_text"] == "our wording"
        assert card["resolved_from"]["supplier_note"].startswith("team:")
    finally:
        _cleanup(db, block_ids=[fork["id"]])


# ── 9. Real drop records round-trip ────────────────────────────────────────

@needs_drop
def test_real_drop_records_round_trip_unchanged(db, tenant_a, client_as):
    """Four shapes a naive schema silently drops, taken verbatim from the drop:
    a template-less key, a json-bodied structured type, a wildcard-region block,
    and an index-slug block."""
    cc = json.loads((DROP_RAW / "CURATED_CONTENT.json").read_text(encoding="utf-8"))
    sdc = json.loads((DROP_RAW / "SUPPLY_DEMAND_COMPLIANCE.json").read_text(encoding="utf-8"))
    ceo = json.loads((DROP_RAW / "CURRENT_EVENTS_OUTLOOK.json").read_text(encoding="utf-8"))
    narr = json.loads((DROP_RAW / "INDEX_NARRATIVES.json").read_text(encoding="utf-8"))

    # A GRP-* group pseudo-key: a roll-up, not a formula, so no template row.
    orphan_key = next(k for k in cc if k.startswith("GRP-"))
    orphan_note = cc[orphan_key]["supplierNote"]
    # A structured type whose elements are objects, with the known polymorphism:
    # `compliance` is 367 dicts and 31 bare strings across the payload.
    sd_key = next(k for k in sdc if sdc[k].get("supply"))
    supply = sdc[sd_key]["supply"]
    # The wildcard region.
    ceo_key = next(iter(ceo))
    ceo_text = ceo[ceo_key]["*"]
    # An index slug that resolves to a loaded series.
    slug = next(iter(narr))
    narrative = narr[slug]

    c = client_as(tenant_a)
    ids = []
    try:
        r1 = _post(c, tenant_a, subject_code=orphan_key, block_type="supplier_note",
                   body_text=orphan_note)
        assert r1.status_code == 201, r1.text
        ids.append(r1.json()["id"])
        assert r1.json()["template_id"] is None
        assert r1.json()["body_text"] == orphan_note

        r2 = _post(c, tenant_a, subject_code=sd_key, block_type="supply",
                   body_format="json", body_json=supply)
        assert r2.status_code == 201, r2.text
        ids.append(r2.json()["id"])
        # Byte-for-byte, including the short `l`/`v`/`c`/`t` element keys and the
        # colour hexes — a typed loader that normalised them would lose them.
        assert r2.json()["body_json"] == supply

        r3 = _post(c, tenant_a, subject_code=ceo_key, block_type="current_events",
                   region=None, body_text=ceo_text)
        assert r3.status_code == 201, r3.text
        ids.append(r3.json()["id"])
        assert r3.json()["region"] is None
        assert r3.json()["body_text"] == ceo_text

        r4 = _post(c, tenant_a, subject_type="index", subject_code=slug,
                   block_type="index_narrative", body_format="json",
                   body_json=narrative)
        assert r4.status_code == 201, r4.text
        ids.append(r4.json()["id"])
        assert r4.json()["body_json"] == narrative
        # All 27 INDEX_NARRATIVES keys resolve to a loaded series, so the
        # convenience join should be populated here.
        assert r4.json()["commodity_id"] is not None
    finally:
        _cleanup(db, block_ids=ids)


@needs_drop
def test_the_substitution_overlap_is_a_duplicate_not_a_conflict():
    """A finding that changes what a loader has to do, pinned so a later drop
    that breaks it is noticed.

    `macroDrivers` and `substitution` appear in both `CURATED_CONTENT` and
    `FUTURE_OUTLOOK`, which reads like a precedence problem. It is not:
    `macroDrivers` overlaps on **zero** keys, and every one of the 100
    overlapping `substitution` keys carries **identical** content. So the loader
    needs a dedupe, not an arbitration rule — and the unique index on
    (subject, block_type, region) is what enforces it.
    """
    cc = json.loads((DROP_RAW / "CURATED_CONTENT.json").read_text(encoding="utf-8"))
    fo = json.loads((DROP_RAW / "FUTURE_OUTLOOK.json").read_text(encoding="utf-8"))

    def keys_with(src, field):
        return {k for k, v in src.items() if isinstance(v, dict) and v.get(field)}

    assert keys_with(cc, "macroDrivers") & keys_with(fo, "macroDrivers") == set()

    overlap = keys_with(cc, "substitution") & keys_with(fo, "substitution")
    assert overlap, "expected substitution to appear in both files"
    differing = [k for k in overlap if cc[k]["substitution"] != fo[k]["substitution"]]
    assert differing == [], f"{len(differing)} keys now disagree: {differing[:5]}"


def test_one_block_per_subject_type_and_region(db, tenant_a, client_as):
    """The dedupe the overlap above needs. `region` is nullable and Postgres
    treats every NULL as distinct in a unique index, so the wildcard is folded
    to a literal in the index expression — otherwise two wildcard blocks for the
    same subject and type could both be inserted."""
    c = client_as(tenant_a)
    code = f"DUP-{uuid.uuid4().hex[:8]}"
    first = _post(c, tenant_a, subject_code=code, block_type="substitution",
                  body_text="once")
    assert first.status_code == 201, first.text
    try:
        dup = _post(c, tenant_a, subject_code=code, block_type="substitution",
                    body_text="twice")
        # A clean 409, not a 500 — the collision is expected, since
        # `substitution` is carried by two files for 100 keys.
        assert dup.status_code == 409, dup.text
    finally:
        _cleanup(db, block_ids=[first.json()["id"]])


def test_the_block_type_vocabulary_covers_every_drop_source():
    """The vocabulary is read off the drop, not invented — so this asserts the
    fields the six editorial files actually carry are all representable."""
    expected = {
        "functionalities", "applications", "suppliers", "supplier_note",
        "compliance", "macro_drivers", "substitution", "supply", "demand",
        "synthesis_route", "current_events", "negotiation_note",
        "index_narrative", "index_source_meta",
    }
    assert expected <= set(BLOCK_TYPES)


def test_the_approvals_queue_filters_by_provenance(db, tenant_a, client_as):
    """The curation console reads "everything not yet signed off". Answering that
    by fetching the whole library and filtering client-side stops working the
    moment the editorial loader lands its real volume, so the filter is on the
    endpoint."""
    c = client_as(tenant_a)
    made = []
    try:
        for prov, block_type in (("imported", "supplier_note"),
                                 ("ai_draft", "compliance"),
                                 ("human_approved", "macro_drivers")):
            r = _post(c, tenant_a, block_type=block_type, provenance=prov)
            assert r.status_code == 201, r.text
            made.append(r.json()["id"])

        base = f"/api/editorial/blocks?team_id={tenant_a['team_id']}"
        mine = lambda rows: [b for b in rows if b["id"] in made]  # noqa: E731

        unsigned = mine(c.get(f"{base}&provenance=imported&provenance=ai_draft").json())
        assert {b["provenance"] for b in unsigned} == {"imported", "ai_draft"}
        assert len(unsigned) == 2

        approved = mine(c.get(f"{base}&provenance=human_approved").json())
        assert len(approved) == 1
        # The badge rides along, so the queue does not re-invent the caveat.
        assert approved[0]["badge"]["reviewed"] is True
        assert approved[0]["badge"]["caveat"] is None

        # Unfiltered still returns everything — the filter is additive.
        assert len(mine(c.get(base).json())) == 3

        # An unknown state is refused rather than silently matching nothing.
        assert c.get(f"{base}&provenance=nonsense").status_code == 422

        # Pagination exists for the same reason the filter does.
        assert len(c.get(f"{base}&limit=1").json()) == 1
    finally:
        _cleanup(db, block_ids=made)
