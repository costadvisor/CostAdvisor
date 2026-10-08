"""The playbook loader (app/services/content_drop/playbooks.py).

The playbooks load as platform rows with every lever, lever objective and
authored objective the files carry; a second load changes nothing; and a
reload repairs a damaged lever **in place**, so the team state that points at
it (lever_scores, strategy_actions) survives.

Expected numbers and values come from the drop files at test time
(`tests/content_drop_expect.py` and the playbook files), never from constants.
The suite shares the database, and the drop may already be loaded. So every
assertion is on totals and on the stored state, never on `created`. The module
works in one transaction and rolls it back at the end, so it leaves nothing
behind whatever state it found.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

import seed_content_drop
from app.database import SessionLocal, bypass_rls_var
from app.models.strategy import (
    GEMSTONE_CODE_BY_NAME, GEMSTONES, OBJECTIVE_CODE_BY_NAME, LeverScore, Playbook,
    PlaybookLever, PlaybookLeverObjective, PlaybookObjective, StrategyAction,
)
from app.models.team import Team
from app.services.drop.report import LoadReport, TableDiff
from app.services.content_drop import playbooks, reader
from tests import content_drop_expect as expect

pytestmark = pytest.mark.skipif(not reader.drop_available(),
                                reason="content drop not extracted (set CONTENT_DROP_DIR)")

COAGULANTS = "coagulants"


def _total(report: LoadReport, table: str) -> int:
    diff = report.table(table)
    return diff.created + diff.updated + diff.unchanged


def _files() -> dict[str, dict]:
    return {doc["category"]["id"]: doc for _, doc in playbooks.read_playbooks()}


def _without_report() -> list[str]:
    delivered = {r["slug"] for r in reader.report_manifest()}
    return sorted(slug for slug in _files() if slug not in delivered)


def _lever_objective_count() -> int:
    """Each lever's distinct objectives, as codes (the link table's key)."""
    return sum(len({OBJECTIVE_CODE_BY_NAME[n] for n in opp.get("objectives") or []})
               for doc in _files().values() for opp in doc.get("opportunities") or [])


def _levers(db, slugs) -> list[PlaybookLever]:
    return db.query(PlaybookLever).filter(PlaybookLever.playbook_slug.in_(slugs)).all()


@pytest.fixture(scope="module")
def loaded():
    """One session and one transaction for the module: load twice, yield,
    roll back. RLS bypass as the CLI sets it."""
    token = bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        first = playbooks.load(db, LoadReport(title="test playbooks 1"))
        db.flush()
        second = playbooks.load(db, LoadReport(title="test playbooks 2"))
        db.flush()
        yield db, first, second
    finally:
        db.rollback()
        db.close()
        bypass_rls_var.reset(token)


# ── Without a database ───────────────────────────────────────────────────────

def test_the_harness_finds_the_loader():
    assert seed_content_drop.resolve_loader("playbooks") is playbooks


def test_lever_fields_store_what_is_authored_and_refuse_what_the_checks_would():
    diff = TableDiff("playbook_levers")
    opp = _files()[COAGULANTS]["opportunities"][0]
    fields = playbooks.lever_fields(COAGULANTS, 0, opp, diff)
    assert fields == {
        "playbook_slug": COAGULANTS, "gemstone_code": GEMSTONE_CODE_BY_NAME[opp["gemstone"]],
        "title": opp["title"], "guidance": opp["guidance"],
        "default_applies": opp["applies"], "default_ease": opp["ease"],
        "savings_score": opp["savings_score"], "default_status": opp["status"],
        "default_notes": opp["notes"], "scales": opp["scales"], "sort_order": 0,
    }
    assert diff.skipped == []

    # A value a table check would refuse is stored NULL and reported, never coerced.
    bad = {**opp, "id": "x-1", "ease": 7, "savings_score": 0, "status": "Maybe",
           "applies": "yes", "savings_value": 5000}
    fields = playbooks.lever_fields(COAGULANTS, 3, bad, diff)
    assert fields["default_ease"] is None and fields["savings_score"] is None
    assert fields["default_status"] is None and fields["default_applies"] is None
    reasons = " | ".join(why for key, why in diff.skipped if key == "x-1")
    for word in ("ease 7", "savings_score 0", "status 'Maybe'", "applies 'yes'",
                 "savings_value 5000"):
        assert word in reasons

    # An unknown gemstone cannot be stored (NOT NULL): the lever is skipped.
    assert playbooks.lever_fields(COAGULANTS, 4, {**opp, "id": "x-2", "gemstone": "Other"},
                                  diff) is None
    assert any(key == "x-2" and "gemstone 'Other'" in why for key, why in diff.skipped)


def test_a_missing_vocabulary_fails_loudly(monkeypatch):
    monkeypatch.setattr(playbooks, "GEMSTONE_CODE_BY_NAME",
                        {**GEMSTONE_CODE_BY_NAME, "Unseeded": "ZZ"})
    token = bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        with pytest.raises(RuntimeError, match="ZZ"):
            playbooks.load(db, LoadReport(title="no vocabulary"))
    finally:
        db.rollback()
        db.close()
        bypass_rls_var.reset(token)


# ── The load ─────────────────────────────────────────────────────────────────

def test_load_reaches_the_drop_counts(loaded):
    db, first, _ = loaded
    counts = expect.playbook_counts()
    links = _lever_objective_count()
    assert _total(first, "playbooks") == counts["playbooks"]
    assert _total(first, "playbook_levers") == counts["levers"]
    assert _total(first, "playbook_lever_objectives") == links
    assert _total(first, "playbook_objectives") == counts["objectives"]

    slugs = list(_files())
    assert db.query(Playbook).filter(Playbook.slug.in_(slugs)).count() == counts["playbooks"]
    levers = _levers(db, slugs)
    assert len(levers) == counts["levers"]
    ids = [lever.id for lever in levers]
    assert db.query(PlaybookLeverObjective).filter(
        PlaybookLeverObjective.lever_id.in_(ids)).count() == links
    assert db.query(PlaybookObjective).filter(
        PlaybookObjective.playbook_slug.in_(slugs)).count() == counts["objectives"]
    # The only skips are the playbooks with no delivered report.
    assert first.skipped == [
        ("playbooks", slug, "no delivered report with this slug; report_slug left NULL")
        for slug in _without_report()]


def test_a_second_load_changes_nothing(loaded):
    _, _, second = loaded
    assert second.changed == 0, second.render()
    for table in ("playbooks", "playbook_levers", "playbook_lever_objectives",
                  "playbook_objectives"):
        diff = second.table(table)
        assert diff.created == diff.updated == diff.deleted == 0, table


def test_playbooks_carry_their_category(loaded):
    db, _, _ = loaded
    files = _files()
    delivered = {r["slug"] for r in reader.report_manifest()}
    for slug, doc in files.items():
        cat = doc["category"]
        pb = db.get(Playbook, slug)
        assert pb.name == cat["name"] and pb.family == cat["family"], slug
        assert pb.legacy_line_key == f"{cat['family']}|||{cat['name']}"
        assert pb.kraljic == cat["kraljic"]
        assert pb.last_updated == date.fromisoformat(cat["lastUpdated"])
        assert pb.report_slug == (slug if slug in delivered else None)
        # owner / annualSpend / hasContent are shim fields, not loaded.
        assert pb.meta is None
    assert [s for s in files if db.get(Playbook, s).report_slug is None] == _without_report()


def test_every_lever_is_stored_as_authored_in_file_order(loaded):
    db, _, _ = loaded
    stored = {lever.lever_code: lever for lever in _levers(db, list(_files()))}
    for slug, doc in _files().items():
        for pos, opp in enumerate(doc["opportunities"]):
            lever = stored[opp["id"]]
            assert lever.playbook_slug == slug and lever.sort_order == pos
            assert lever.gemstone_code == GEMSTONE_CODE_BY_NAME[opp["gemstone"]]
            assert (lever.title, lever.guidance) == (opp["title"], opp["guidance"])
            assert lever.default_applies is opp["applies"]
            assert lever.default_ease == opp["ease"]
            assert lever.savings_score == opp["savings_score"]
            assert lever.default_status == opp["status"]
            assert lever.default_notes == opp["notes"]   # '' stays ''
            assert lever.scales == opp["scales"]          # authored order kept


def test_objectives_map_display_names_to_codes(loaded):
    db, _, _ = loaded
    files = _files()
    by_code = {lever.lever_code: lever.id for lever in _levers(db, list(files))}
    links: dict[int, set] = {}
    for link in db.query(PlaybookLeverObjective).filter(
            PlaybookLeverObjective.lever_id.in_(by_code.values())):
        links.setdefault(link.lever_id, set()).add(link.objective_code)
    for doc in files.values():
        for opp in doc["opportunities"]:
            assert links[by_code[opp["id"]]] == {
                OBJECTIVE_CODE_BY_NAME[n] for n in opp["objectives"]}, opp["id"]

    for slug, doc in files.items():
        rows = (db.query(PlaybookObjective).filter_by(playbook_slug=slug)
                .order_by(PlaybookObjective.sort_order).all())
        assert [(r.objective_code, r.priority, r.note) for r in rows] == [
            (OBJECTIVE_CODE_BY_NAME[o["type"]], o["priority"], o["note"])
            for o in doc["objectives"]], slug


def test_the_coagulants_playbook(loaded):
    db, first, _ = loaded
    doc = _files()[COAGULANTS]
    cat = doc["category"]
    pb = db.get(Playbook, COAGULANTS)
    assert (pb.name, pb.family, pb.report_slug) == (cat["name"], cat["family"], COAGULANTS)
    assert pb.kraljic == cat["kraljic"]
    levers = (db.query(PlaybookLever).filter_by(playbook_slug=COAGULANTS)
              .order_by(PlaybookLever.sort_order).all())
    count = expect.playbook_lever_count(COAGULANTS)
    assert len(levers) == count > 0
    assert first.table(playbooks.CHECK_SPOT).unchanged == count
    authored = doc["opportunities"]
    assert [lv.lever_code for lv in levers] == [o["id"] for o in authored]
    assert [lv.gemstone_code for lv in levers] == [
        GEMSTONE_CODE_BY_NAME[o["gemstone"]] for o in authored]
    objectives = (db.query(PlaybookObjective).filter_by(playbook_slug=COAGULANTS)
                  .order_by(PlaybookObjective.sort_order).all())
    assert [(o.objective_code, o.priority) for o in objectives] == [
        (OBJECTIVE_CODE_BY_NAME[o["type"]], o["priority"]) for o in doc["objectives"]]


def test_the_report_counts_levers_by_gemstone_status_and_applies(loaded):
    _, first, _ = loaded
    opps = [o for doc in _files().values() for o in doc["opportunities"]]
    for name, n in Counter(o["gemstone"] for o in opps).items():
        code = GEMSTONE_CODE_BY_NAME[name]
        assert first.table(f"{playbooks.CHECK_GEMSTONE}{code}").unchanged == n, name
    for status, n in Counter(o["status"] for o in opps).items():
        assert first.table(f"{playbooks.CHECK_STATUS}{status}").unchanged == n, status
    applies = Counter(o["applies"] for o in opps)
    for value, label in ((True, "yes"), (False, "no")):
        row = first.table(f"{playbooks.CHECK_APPLIES}{label}")
        assert (row.unchanged if row else 0) == applies[value], label
    assert sum(applies.values()) == expect.playbook_counts()["levers"]
    sets = first.table(playbooks.CHECK_LEVER_SETS)
    assert sets.unchanged == expect.playbook_counts()["playbooks"] and sets.skipped == []
    # Check rows are counts, not changes.
    assert all(t.changed == 0 for t in first.tables if t.table.startswith("check: "))


def test_a_reload_repairs_levers_in_place_and_keeps_team_state(loaded):
    db, _, _ = loaded
    doc = _files()[COAGULANTS]
    authored = doc["opportunities"][0]
    authored_links = {OBJECTIVE_CODE_BY_NAME[n] for n in authored["objectives"]}
    authored_objectives = [OBJECTIVE_CODE_BY_NAME[o["type"]] for o in doc["objectives"]]
    all_objectives = list(OBJECTIVE_CODE_BY_NAME.values())
    stray_link = next(c for c in all_objectives if c not in authored_links)
    stray_objective = next(c for c in all_objectives if c not in authored_objectives)
    gem = GEMSTONE_CODE_BY_NAME[authored["gemstone"]]
    other_gem = next(code for code, _n, _c in GEMSTONES if code != gem)

    pb = db.get(Playbook, COAGULANTS)
    lever = db.query(PlaybookLever).filter_by(lever_code=authored["id"]).one()
    lever_id = lever.id

    # Team state pointing at the lever, on any team that exists (inside this
    # transaction, so nothing is committed).
    team_id = db.query(Team.id).order_by(Team.created_at).limit(1).scalar()
    if team_id is not None:
        db.add(LeverScore(team_id=team_id, lever_id=lever_id, ease=2, notes="our view"))
        db.add(StrategyAction(team_id=team_id, playbook_slug=COAGULANTS, lever_id=lever_id,
                              title="Run the regional tender"))

    # Damage every tier.
    pb.name = "renamed"
    pb.kraljic = {"cx": 1}
    lever.title, lever.gemstone_code, lever.default_ease, lever.sort_order = (
        "damaged", other_gem, 1 if authored["ease"] != 1 else 2, 99)
    db.delete(db.get(PlaybookLeverObjective, (lever_id, sorted(authored_links)[0])))
    db.add(PlaybookLeverObjective(lever_id=lever_id, objective_code=stray_link))
    db.delete(db.get(PlaybookObjective, (COAGULANTS, authored_objectives[0])))
    db.add(PlaybookObjective(playbook_slug=COAGULANTS, objective_code=stray_objective,
                             priority="Low", note="stray"))
    # A lever and a playbook the drop does not mention.
    db.add(PlaybookLever(playbook_slug=COAGULANTS, lever_code="coagulants-zz-not-in-drop",
                         gemstone_code=gem, title="ghost", sort_order=100))
    db.add(Playbook(slug="zz-not-in-drop", name="ghost playbook"))
    db.flush()

    repaired = playbooks.load(db, LoadReport(title="repair"))
    db.flush()
    assert repaired.table("playbooks").updated == 1
    assert repaired.table("playbooks").stale >= 1
    levers = repaired.table("playbook_levers")
    assert (levers.created, levers.updated, levers.deleted) == (0, 1, 0)
    assert levers.stale >= 1
    links = repaired.table("playbook_lever_objectives")
    assert (links.created, links.deleted) == (1, 1)
    objectives = repaired.table("playbook_objectives")
    assert (objectives.created, objectives.updated, objectives.deleted) == (1, 0, 1)

    # Repaired in place: same id, authored values back.
    db.refresh(lever)
    assert lever.id == lever_id
    assert (lever.title, lever.gemstone_code, lever.default_ease, lever.sort_order) == (
        authored["title"], gem, authored["ease"], 0)
    assert {link.objective_code for link in db.query(PlaybookLeverObjective)
            .filter_by(lever_id=lever_id)} == authored_links
    db.refresh(pb)
    assert pb.name == doc["category"]["name"] and pb.kraljic == doc["category"]["kraljic"]
    assert [o.objective_code for o in db.query(PlaybookObjective)
            .filter_by(playbook_slug=COAGULANTS).order_by(PlaybookObjective.sort_order)] == \
        authored_objectives

    # The team's state survived the repair.
    if team_id is not None:
        score = db.query(LeverScore).filter_by(team_id=team_id, lever_id=lever_id).one()
        assert score.ease == 2 and score.notes == "our view"
        action = db.query(StrategyAction).filter_by(
            team_id=team_id, title="Run the regional tender").one()
        assert action.lever_id == lever_id

    # What the drop does not mention is reported and kept, never deleted; the
    # lever-set check names the playbook still showing it.
    assert db.query(PlaybookLever).filter_by(
        lever_code="coagulants-zz-not-in-drop").one().title == "ghost"
    assert db.get(Playbook, "zz-not-in-drop").name == "ghost playbook"
    assert any(slug == COAGULANTS and "1 not in the file" in why
               for slug, why in repaired.table(playbooks.CHECK_LEVER_SETS).skipped)

    settled = playbooks.load(db, LoadReport(title="after repair"))
    db.flush()
    assert settled.changed == 0, settled.render()
