"""Strategy API (design §2.6, §2.8 and §4.2 "Strategy"; demo spec §6 / §9).

What is pinned:

* permissions — a non-member is refused everything; a member with view but
  not edit (a plain `member` with no custom role) reads but cannot write;
* RLS — two fixture teams never see each other's strategy rows, through the
  API and at the database with the policies on;
* the overlay — a new team sees the authored levers and objectives with no
  seeding; one team's override changes only that team's view; an explicit
  null returns a lever field to the playbook default;
* adoption is idempotent; suggestions come from the portfolio (product →
  effective line → market_report_lines.product_line_id → playbook), through
  each of `effective_lines()`'s three paths: a linked template, a cost-model
  template (a product tracked from the cost-model builder) and a custom
  product's own line;
* a report key that is no current line reaches nothing and is never served
  (design §4.1): it is "Product line not yet published";
* the catalogue products a category lists are listed cards (design §2.3);
* overdue = due date before today and status not Done;
* spend = latest four priced quarters × that quarter's volume.

Expected figures (lever counts, default statuses, the Kraljic position, the
lines a report reaches) are read from the content drop at run time through
`tests/content_drop_expect.py` and the playbook files; nothing here repeats a
number from the drop. Every test needs loaded content (`content_loaded`).

Fixtures build their own teams (conftest's `user_factory`, torn down by team
CASCADE, audit rows by user). Platform rows — playbooks, levers, templates,
reports — are only read; nothing here writes one.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from functools import lru_cache

import pytest

from app.database import SessionLocal, bypass_rls_var, current_user_id_var
from app.models.actual_volume import ActualVolume
from app.models.cost_model import CostModel, FormulaVersion
from app.models.formula_template import FormulaRegionCoverage, FormulaTemplate
from app.models.price_data import ActualPrice
from app.models.product import Product
from app.models.product_line import ProductLine
from app.models.strategy import (
    GEMSTONES, LeverScore, PlaybookLever, PlaybookObjective, StrategyAction, StrategyRecord,
    TeamObjective,
)
from app.models.supplier import Supplier
from app.models.team import TeamMembership
from app.services.strategy import OPEN_LEVER_STATUSES, UNPUBLISHED_LINE
from tests import content_drop_expect as expect

pytestmark = pytest.mark.usefixtures("content_loaded")

SLUG = "coagulants"
FERRIC = "BCI-FECL3-LIQ"     # ferric chloride
ALUM = "BCI-ALUM-LIQ"        # alum — its line has more than one report


def _url(path: str, team_id) -> str:
    sep = "&" if "?" in path else "?"
    return f"/api/strategy{path}{sep}team_id={team_id}"


def _template_id(db, code: str) -> uuid.UUID:
    t = db.query(FormulaTemplate).filter(
        FormulaTemplate.code == code, FormulaTemplate.team_id.is_(None)).first()
    assert t is not None, f"{code} is not loaded: rebuild the test database"
    return t.id


def _lever_id(db, code: str) -> int:
    lv = db.query(PlaybookLever).filter(PlaybookLever.lever_code == code).first()
    assert lv is not None, f"lever {code} is not loaded: rebuild the test database"
    return lv.id


# ── What the drop says (read at run time) ────────────────────────────────────

GEM_CODE = {name: code for code, name, _c in GEMSTONES}
GEM_ORDER = [code for code, _n, _c in GEMSTONES]


@lru_cache(maxsize=None)
def _playbook(slug: str) -> dict:
    path = expect.drop_dir() / "playbooks" / f"playbook_{slug}_appdata.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def _playbook_slugs() -> frozenset[str]:
    out = set()
    for path in expect.playbook_files():
        with open(path, encoding="utf-8") as fh:
            out.add(json.load(fh)["category"]["id"])
    return frozenset(out)


def _lever(slug: str, code: str) -> dict:
    return next(o for o in _playbook(slug)["opportunities"] if o["id"] == code)


def _lever_order(slug: str) -> list[str]:
    """Authored lever codes in the order the API numbers them: gemstone
    order, then position in the file."""
    opps = _playbook(slug)["opportunities"]
    return [o["id"] for _pos, o in sorted(
        enumerate(opps), key=lambda po: (GEM_ORDER.index(GEM_CODE[po[1]["gemstone"]]), po[0]))]


def _open_opportunities(slug: str) -> int:
    return sum(1 for o in _playbook(slug)["opportunities"]
               if o.get("status") in OPEN_LEVER_STATUSES and o.get("applies") is not False)


def _report_line_keys(slug: str) -> set[str | None]:
    """The current line keys a report reaches (None for a key that is no
    current line)."""
    return {expect.resolve_line_key(k) for s, k in expect.report_joins() if s == slug}


def _playbooks_of_line(line_key: str | None) -> set[str]:
    if line_key is None:
        return set()
    return {s for s, k in expect.report_joins()
            if expect.resolve_line_key(k) == line_key} & _playbook_slugs()


def _playbooks_of(pid: str) -> set[str]:
    return _playbooks_of_line(expect.template_line(pid))


def _unpublished_joins() -> dict[str, set[str]]:
    """report slug → its stored keys that are no current line, for the
    reports that have a playbook."""
    out: dict[str, set[str]] = {}
    for s, k in expect.report_joins():
        if expect.resolve_line_key(k) is None and s in _playbook_slugs():
            out.setdefault(s, set()).add(k)
    return out


@pytest.fixture
def member_of_a(user_factory, tenant_a, db):
    """A second user on team A as a plain member with no custom role: the
    `has_permission` fallback grants view and refuses edit."""
    m = user_factory()
    db.add(TeamMembership(user_id=m["user_id"], team_id=tenant_a["team_id"], role="member"))
    db.commit()
    return m


@pytest.fixture
def portfolio_a(db, tenant_a):
    """Team A buys ferric chloride (two suppliers, two sites) and alum."""
    team, user = tenant_a["team_id"], tenant_a["user_id"]
    kemira = Supplier(team_id=team, name="Supplier one (test)")
    feralco = Supplier(team_id=team, name="Supplier two (test)")
    db.add_all([kemira, feralco])
    db.flush()
    products, models = {}, {}
    for code, name in ((FERRIC, "Ferric product (test)"), (ALUM, "Alum product (test)")):
        p = Product(team_id=team, created_by=user, name=name, unit="t",
                    formula_template_id=_template_id(db, code))
        db.add(p)
        db.flush()
        products[code] = p
    specs = [
        ("fecl3_rtm", FERRIC, kemira, "Rotterdam, NL", 400.0, 100.0),
        ("fecl3_ant", FERRIC, feralco, "Antwerp, BE", 410.0, 50.0),
        ("alum_rtm", ALUM, kemira, "Rotterdam, NL", 200.0, 30.0),
    ]
    quarters = [(2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2)]
    for key, code, sup, site, price, vol in specs:
        cm = CostModel(team_id=team, product_id=products[code].id, supplier_id=sup.id,
                       region="Europe", currency="EUR", created_by=user,
                       destination_country=site)
        db.add(cm)
        db.flush()
        for i, (y, q) in enumerate(quarters):
            db.add(ActualPrice(cost_model_id=cm.id, uploaded_by=user, year=y, quarter=q,
                               price=price + 10 * i))
            db.add(ActualVolume(cost_model_id=cm.id, uploaded_by=user, year=y, quarter=q,
                                volume=vol, unit="t"))
        models[key] = cm
    db.commit()
    return {"products": products, "models": models, "quarters": quarters, "specs": specs}


# ── Permissions ──────────────────────────────────────────────────────────────

def test_non_member_is_refused_reads_and_writes(tenant_a, tenant_b, client_as):
    c = client_as(tenant_b)
    team = tenant_a["team_id"]
    assert c.get(_url("/categories", team)).status_code == 403
    assert c.get(_url(f"/categories/{SLUG}", team)).status_code == 403
    assert c.get(_url(f"/categories/{SLUG}/levers", team)).status_code == 403
    assert c.post(_url(f"/categories/{SLUG}/adopt", team)).status_code == 403
    assert c.post(_url(f"/categories/{SLUG}/actions", team),
                  json={"title": "x"}).status_code == 403


def test_member_without_edit_reads_but_cannot_write(db, tenant_a, member_of_a, client_as):
    c = client_as(member_of_a)
    team = tenant_a["team_id"]
    lever = _lever_id(db, "coagulants-1a")
    for path in ("/categories", f"/categories/{SLUG}", f"/categories/{SLUG}/analysis",
                 f"/categories/{SLUG}/spend", f"/categories/{SLUG}/levers",
                 f"/categories/{SLUG}/actions"):
        assert c.get(_url(path, team)).status_code == 200, path
    refused = [
        c.post(_url(f"/categories/{SLUG}/adopt", team)),
        c.put(_url(f"/categories/{SLUG}", team), json={"status": "On hold"}),
        c.put(_url(f"/levers/{lever}", team), json={"ease": 2}),
        c.post(_url(f"/categories/{SLUG}/levers", team),
               json={"gemstone": "VC", "title": "x"}),
        c.put(_url(f"/categories/{SLUG}/objectives", team),
              json=[{"code": "quality", "selected": True}]),
        c.post(_url(f"/categories/{SLUG}/actions", team), json={"title": "x"}),
    ]
    assert [r.status_code for r in refused] == [403] * len(refused)
    assert db.query(StrategyRecord).filter(StrategyRecord.team_id == team).count() == 0
    assert db.query(LeverScore).filter(LeverScore.team_id == team).count() == 0


# ── Landing and adoption ─────────────────────────────────────────────────────

def test_landing_suggests_reachable_playbooks_and_adopt_is_idempotent(
        db, tenant_a, portfolio_a, client_as):
    c = client_as(tenant_a)
    team = tenant_a["team_id"]
    body = c.get(_url("/categories", team)).json()
    assert body["adopted"] == []
    suggested = {r["playbook_slug"]: r for r in body["suggested"]}
    # Exactly the playbooks the two products' lines reach in the drop. Alum's
    # line has more than one report (line ↔ report is many-to-many).
    expected = _playbooks_of(FERRIC) | _playbooks_of(ALUM)
    assert SLUG in _playbooks_of(FERRIC) and SLUG in _playbooks_of(ALUM)
    assert len(_playbooks_of(ALUM)) > 1
    assert set(suggested) == expected
    for slug, row in suggested.items():
        assert {p["pid"] for p in row["products"]} == {
            pid for pid in (FERRIC, ALUM) if slug in _playbooks_of(pid)}
    coag = suggested[SLUG]
    assert coag["adopted"] is False and coag["status"] == "Not started"
    assert coag["kraljic"]["badge"] == _playbook(SLUG)["category"]["kraljic"]["badge"]
    # Authored levers still in play: open status and not ruled out.
    assert coag["open_opportunities"] == _open_opportunities(SLUG)

    first = c.post(_url(f"/categories/{SLUG}/adopt", team))
    second = c.post(_url(f"/categories/{SLUG}/adopt", team))
    assert first.status_code == second.status_code == 200
    assert first.json()["created"] is True and second.json()["created"] is False
    assert db.query(StrategyRecord).filter(
        StrategyRecord.team_id == team, StrategyRecord.playbook_slug == SLUG).count() == 1

    body = c.get(_url("/categories", team)).json()
    assert [r["playbook_slug"] for r in body["adopted"]] == [SLUG]
    assert SLUG not in {r["playbook_slug"] for r in body["suggested"]}
    row = body["adopted"][0]
    assert row["status"] == "Active" and row["owner"] is None

    upd = c.put(_url(f"/categories/{SLUG}", team),
                json={"status": "On hold", "owner_user_id": str(tenant_a["user_id"])})
    assert upd.status_code == 200
    assert upd.json()["status"] == "On hold"
    assert upd.json()["owner"]["id"] == str(tenant_a["user_id"])
    assert c.put(_url(f"/categories/{SLUG}", team),
                 json={"status": "Nonsense"}).status_code == 400


def test_unknown_category_is_404(tenant_a, client_as):
    c = client_as(tenant_a)
    assert c.get(_url("/categories/no_such_playbook", tenant_a["team_id"])).status_code == 404
    assert c.post(_url("/categories/no_such_playbook/adopt",
                       tenant_a["team_id"])).status_code == 404


def _every_route(lever_id: int) -> list[tuple[str, str, object]]:
    """(method, path, body) for all 16 strategy routes, with bodies that would
    be accepted on a real team."""
    other = uuid.uuid4()
    return [
        ("GET", "/categories", None),
        ("POST", f"/categories/{SLUG}/adopt", None),
        ("PUT", f"/categories/{SLUG}", {"status": "On hold"}),
        ("GET", f"/categories/{SLUG}", None),
        ("GET", f"/categories/{SLUG}/analysis", None),
        ("GET", f"/categories/{SLUG}/spend", None),
        ("GET", f"/categories/{SLUG}/levers", None),
        ("PUT", f"/levers/{lever_id}", {"ease": 2}),
        ("POST", f"/categories/{SLUG}/levers", {"gemstone": "VC", "title": "x"}),
        ("PUT", f"/custom-levers/{other}", {"ease": 2}),
        ("DELETE", f"/custom-levers/{other}", None),
        ("PUT", f"/categories/{SLUG}/objectives", [{"code": "quality", "selected": True}]),
        ("GET", f"/categories/{SLUG}/actions", None),
        ("POST", f"/categories/{SLUG}/actions", {"title": "x"}),
        ("PUT", f"/actions/{other}", {"title": "y"}),
        ("DELETE", f"/actions/{other}", None),
    ]


def test_unknown_team_is_404_for_a_super_admin_and_403_for_anyone_else(
        db, user_factory, tenant_a, client_as):
    """A super-admin passes the permission check for any team id; a made-up
    one must answer 404, not fail on the team_id foreign key (a 500). Anyone
    else still gets 403, so the answer never says which team ids exist."""
    lever = _lever_id(db, "coagulants-1a")
    ghost = uuid.uuid4()
    admin = client_as(user_factory(is_super_admin=True))
    routes = _every_route(lever)
    got = {(m, p): admin.request(m, _url(p, ghost), json=body).status_code
           for m, p, body in routes}
    assert set(got.values()) == {404}, got
    assert admin.get(_url("/categories", ghost)).json() == {"detail": "Team not found"}

    member = client_as(tenant_a)
    assert member.get(_url("/categories", ghost)).status_code == 403
    assert member.post(_url(f"/categories/{SLUG}/adopt", ghost)).status_code == 403
    assert db.query(StrategyRecord).filter(StrategyRecord.team_id == ghost).count() == 0


def test_nul_characters_are_refused_not_500(db, tenant_a, client_as):
    """Postgres text cannot hold NUL; psycopg2 raises before the query. A NUL
    in the slug is an unknown category (404); in any text field of a body it
    is a validation error (422). Nothing is written."""
    a = client_as(tenant_a)
    team = tenant_a["team_id"]
    lever = _lever_id(db, "coagulants-1a")
    for path in ("/categories/a%00b", "/categories/%00", "/categories/a%00b/levers",
                 "/categories/a%00b/analysis", "/categories/a%00b/spend",
                 "/categories/a%00b/actions"):
        assert a.get(_url(path, team)).status_code == 404, path
    assert a.post(_url("/categories/a%00b/adopt", team)).status_code == 404

    nul = "a\x00b"
    refused = [
        a.put(_url(f"/levers/{lever}", team), json={"notes": nul}),
        a.put(_url(f"/levers/{lever}", team), json={"status": nul}),
        a.post(_url(f"/categories/{SLUG}/levers", team), json={"gemstone": "VC", "title": nul}),
        a.post(_url(f"/categories/{SLUG}/levers", team),
               json={"gemstone": "VC", "title": "t", "guidance": nul}),
        a.post(_url(f"/categories/{SLUG}/levers", team), json={"gemstone": nul, "title": "t"}),
        a.post(_url(f"/categories/{SLUG}/levers", team),
               json={"gemstone": "VC", "title": "t", "objectives": ["quality", nul]}),
        a.put(_url(f"/categories/{SLUG}/objectives", team),
              json=[{"code": "quality", "selected": True, "note": nul}]),
        a.put(_url(f"/categories/{SLUG}", team), json={"status": nul}),
        a.post(_url(f"/categories/{SLUG}/actions", team), json={"title": nul}),
        a.post(_url(f"/categories/{SLUG}/actions", team),
               json={"title": "t", "description": nul}),
    ]
    assert [r.status_code for r in refused] == [422] * len(refused)
    assert refused[0].json()["detail"][0]["loc"] == ["body", "notes"]

    created = a.post(_url(f"/categories/{SLUG}/levers", team),
                     json={"gemstone": "VC", "title": "Custom"})
    assert created.status_code == 201
    cid = created.json()["custom_lever_id"]
    assert a.put(_url(f"/custom-levers/{cid}", team), json={"notes": nul}).status_code == 422
    action = a.post(_url(f"/categories/{SLUG}/actions", team), json={"title": "Real"}).json()
    assert a.put(_url(f"/actions/{action['id']}", team),
                 json={"title": nul}).status_code == 422
    assert db.query(LeverScore).filter(LeverScore.team_id == team).count() == 0
    assert db.query(TeamObjective).filter(TeamObjective.team_id == team).count() == 0


# ── Overlay semantics ────────────────────────────────────────────────────────

def test_new_team_sees_authored_objectives_and_an_override_is_team_local(
        db, tenant_a, tenant_b, client_as):
    authored = {o.objective_code: o for o in db.query(PlaybookObjective).filter(
        PlaybookObjective.playbook_slug == SLUG)}
    assert authored, "coagulants ships authored objectives"
    a, b = client_as(tenant_a), client_as(tenant_b)

    fresh = a.get(_url(f"/categories/{SLUG}", tenant_a["team_id"])).json()["objectives"]
    assert [o["code"] for o in fresh][:2] == ["cost_reduction", "cash_working_capital"]
    assert len(fresh) == 7
    for o in fresh:
        if o["code"] in authored:
            assert o["selected"] is True
            assert o["priority"] == authored[o["code"]].priority
            assert o["authored"]["note"] == authored[o["code"]].note
        else:
            assert o["selected"] is False and o["authored"] is None
        assert o["overridden"] is False

    saved = a.put(_url(f"/categories/{SLUG}/objectives", tenant_a["team_id"]), json=[
        {"code": "cost_reduction", "selected": False, "priority": "High", "note": None},
        {"code": "innovation", "selected": True, "priority": "Low", "note": "Joint trials"},
    ])
    assert saved.status_code == 200
    mine = {o["code"]: o for o in saved.json()["objectives"]}
    assert mine["cost_reduction"]["selected"] is False
    assert mine["cost_reduction"]["authored"] is not None      # the pre-fill still shown
    assert mine["innovation"] == {**mine["innovation"], "selected": True, "priority": "Low",
                                  "note": "Joint trials", "overridden": True}

    theirs = {o["code"]: o for o in b.get(
        _url(f"/categories/{SLUG}", tenant_b["team_id"])).json()["objectives"]}
    assert theirs["cost_reduction"]["selected"] is True
    assert theirs["innovation"]["selected"] is False

    bad = a.put(_url(f"/categories/{SLUG}/objectives", tenant_a["team_id"]),
                json=[{"code": "quality", "selected": True, "priority": "Urgent"}])
    assert bad.status_code == 400


def test_lever_override_is_team_local_and_null_resets(db, tenant_a, tenant_b, client_as):
    lever = _lever_id(db, "coagulants-1a")
    a, b = client_as(tenant_a), client_as(tenant_b)

    r = a.put(_url(f"/levers/{lever}", tenant_a["team_id"]),
              json={"ease": 1, "status": "Approved", "savings_value": 25000})
    assert r.status_code == 200
    row = r.json()
    assert (row["ease"], row["status"], row["savings_value"]) == (1, "Approved", 25000.0)
    authored = _lever(SLUG, "coagulants-1a")
    assert row["overridden"] is True and row["defaults"]["ease"] == authored["ease"]
    assert row["savings_score"] == row["defaults"]["savings_score"]   # untouched field

    other = next(x for x in b.get(_url(f"/categories/{SLUG}/levers", tenant_b["team_id"]))
                 .json()["levers"] if x["lever_id"] == lever)
    assert (other["ease"], other["status"], other["overridden"]) == (
        authored["ease"], authored["status"], False)

    reset = a.put(_url(f"/levers/{lever}", tenant_a["team_id"]),
                  json={"ease": None, "status": None, "savings_value": None}).json()
    assert (reset["ease"], reset["status"], reset["overridden"]) == (
        authored["ease"], authored["status"], False)
    assert db.query(LeverScore).filter(LeverScore.team_id == tenant_a["team_id"]).count() == 0

    assert a.put(_url(f"/levers/{lever}", tenant_a["team_id"]),
                 json={"status": "Done"}).status_code == 400
    assert a.put(_url(f"/levers/{lever}", tenant_a["team_id"]),
                 json={"ease": 6}).status_code == 422
    assert a.put(_url("/levers/999999999", tenant_a["team_id"]),
                 json={"ease": 2}).status_code == 404


def test_levers_grouped_by_gemstone_and_numbered_across_the_list(db, tenant_a, client_as):
    c = client_as(tenant_a)
    body = c.get(_url(f"/categories/{SLUG}/levers", tenant_a["team_id"])).json()
    n = expect.playbook_lever_count(SLUG)
    assert body["total"] == n
    assert [g["code"] for g in body["gemstones"]] == GEM_ORDER
    flat = body["levers"]
    assert [r["number"] for r in flat] == list(range(1, n + 1))
    # Gemstone order, then the authored order within a gemstone (the file's).
    assert [r["code"] for r in flat] == _lever_order(SLUG)
    assert flat[0]["title"] == _lever(SLUG, flat[0]["code"])["title"]
    grouped = [r["number"] for g in body["gemstones"] for r in g["levers"]]
    assert grouped == list(range(1, n + 1))
    assert all(r["plotted"] == bool(r["applies"] and r["ease"] and r["savings_score"])
               for r in flat)


def test_custom_lever_lifecycle(db, tenant_a, tenant_b, client_as):
    a = client_as(tenant_a)
    team = tenant_a["team_id"]
    created = a.post(_url(f"/categories/{SLUG}/levers", team), json={
        "gemstone": "Volume Concentration", "title": "Pool with the neighbouring utility",
        "ease": 3, "savings_score": 4, "objectives": ["cost_reduction", "simplification"]})
    assert created.status_code == 201
    row = created.json()
    assert row["custom"] is True and row["gemstone"] == "VC" and row["plotted"] is True
    vc = sum(1 for o in _playbook(SLUG)["opportunities"] if GEM_CODE[o["gemstone"]] == "VC")
    assert row["number"] == vc + 1     # after the authored VC levers
    assert [o["code"] for o in row["objectives"]] == ["cost_reduction", "simplification"]
    cid = row["custom_lever_id"]

    n = expect.playbook_lever_count(SLUG)
    levers = a.get(_url(f"/categories/{SLUG}/levers", team)).json()
    assert levers["total"] == n + 1
    assert client_as(tenant_b).get(
        _url(f"/categories/{SLUG}/levers", tenant_b["team_id"])).json()["total"] == n

    upd = a.put(_url(f"/custom-levers/{cid}", team), json={"status": "Approved", "ease": 5})
    assert upd.status_code == 200 and upd.json()["ease"] == 5
    assert a.post(_url(f"/categories/{SLUG}/levers", team),
                  json={"gemstone": "Nope", "title": "x"}).status_code == 400
    # Another team cannot reach it, even by id.
    assert client_as(tenant_b).put(_url(f"/custom-levers/{cid}", tenant_b["team_id"]),
                                   json={"ease": 1}).status_code == 404
    assert a.delete(_url(f"/custom-levers/{cid}", team)).status_code == 200
    assert a.get(_url(f"/categories/{SLUG}/levers", team)).json()["total"] == n


# ── Actions ──────────────────────────────────────────────────────────────────

def test_actions_overdue_and_validation(db, tenant_a, tenant_b, client_as):
    a = client_as(tenant_a)
    team = tenant_a["team_id"]
    lever = _lever_id(db, "coagulants-4a")
    today = date.today()

    def make(title, due, status):
        r = a.post(_url(f"/categories/{SLUG}/actions", team), json={
            "title": title, "lever_id": lever, "assignee_user_id": str(tenant_a["user_id"]),
            "start_date": (today - timedelta(days=30)).isoformat(),
            "due_date": due.isoformat(), "status": status, "pct_complete": 40})
        assert r.status_code == 201, r.text
        return r.json()

    late = make("Jar test round 1", today - timedelta(days=1), "In progress")
    done = make("Jar test report", today - timedelta(days=1), "Done")
    future = make("Jar test round 2", today + timedelta(days=10), "Not started")
    assert (late["overdue"], done["overdue"], future["overdue"]) == (True, False, False)
    assert late["lever"]["code"] == "coagulants-4a" and late["lever"]["gemstone"] == "JP"
    assert late["assignee"]["id"] == str(tenant_a["user_id"])

    body = a.get(_url(f"/categories/{SLUG}/actions", team)).json()
    assert body["counts"]["total"] == 3 and body["counts"]["overdue"] == 1
    assert a.post(_url(f"/categories/{SLUG}/adopt", team)).status_code == 200
    landing = a.get(_url("/categories", team)).json()
    row = next(r for r in landing["adopted"] if r["playbook_slug"] == SLUG)
    assert (row["actions_overdue"], row["actions_in_progress"]) == (1, 1)
    levers = a.get(_url(f"/categories/{SLUG}/levers", team)).json()["levers"]
    assert next(r for r in levers if r["lever_id"] == lever)["actions_count"] == 3

    # Marking it done clears overdue.
    fixed = a.put(_url(f"/actions/{late['id']}", team), json={"status": "Done",
                                                              "pct_complete": 100})
    assert fixed.status_code == 200 and fixed.json()["overdue"] is False

    # Validation: assignee must be a member; lever must be in the category.
    assert a.post(_url(f"/categories/{SLUG}/actions", team), json={
        "title": "x", "assignee_user_id": str(tenant_b["user_id"])}).status_code == 400
    assert a.post(_url(f"/categories/{SLUG}/actions", team), json={
        "title": "x", "lever_id": _lever_id(db, "alkalis-1a")}).status_code == 400
    assert a.post(_url(f"/categories/{SLUG}/actions", team), json={
        "title": "x", "status": "Finished"}).status_code == 400
    assert a.post(_url(f"/categories/{SLUG}/actions", team), json={
        "title": "x", "pct_complete": 120}).status_code == 422

    assert a.delete(_url(f"/actions/{future['id']}", team)).status_code == 200
    assert a.get(_url(f"/categories/{SLUG}/actions", team)).json()["counts"]["total"] == 2


# ── RLS isolation ────────────────────────────────────────────────────────────

def test_rls_isolation_between_teams(db, tenant_a, tenant_b, client_as):
    a, b = client_as(tenant_a), client_as(tenant_b)
    ta, tb = tenant_a["team_id"], tenant_b["team_id"]
    lever = _lever_id(db, "coagulants-1b")
    assert a.post(_url(f"/categories/{SLUG}/adopt", ta)).status_code == 200
    assert a.put(_url(f"/levers/{lever}", ta), json={"notes": "team A only"}).status_code == 200
    action = a.post(_url(f"/categories/{SLUG}/actions", ta), json={"title": "A's action"}).json()
    assert a.put(_url(f"/categories/{SLUG}/objectives", ta),
                 json=[{"code": "quality", "selected": False}]).status_code == 200

    # Through the API: B sees none of it, and cannot reach A's rows by id.
    landing = b.get(_url("/categories", tb)).json()
    assert landing["adopted"] == []
    assert b.get(_url(f"/categories/{SLUG}/actions", tb)).json()["actions"] == []
    assert next(r for r in b.get(_url(f"/categories/{SLUG}/levers", tb)).json()["levers"]
                if r["lever_id"] == lever)["notes"] == ""
    assert b.put(_url(f"/actions/{action['id']}", tb), json={"title": "hijack"}).status_code == 404
    assert b.delete(_url(f"/actions/{action['id']}", tb)).status_code == 404

    # At the database, policies on: each user sees exactly their team's rows.
    def visible(user_id):
        bypass_rls_var.set(False)
        current_user_id_var.set(str(user_id))
        s = SessionLocal()
        try:
            return {
                "records": {r.team_id for r in s.query(StrategyRecord).all()},
                "scores": {r.team_id for r in s.query(LeverScore).all()},
                "actions": {r.team_id for r in s.query(StrategyAction).all()},
                "objectives": {r.team_id for r in s.query(TeamObjective).all()},
            }
        finally:
            s.close()
            current_user_id_var.set(None)
            bypass_rls_var.set(True)

    seen_by_a, seen_by_b = visible(tenant_a["user_id"]), visible(tenant_b["user_id"])
    assert all(v == {ta} for v in seen_by_a.values()), seen_by_a
    assert all(ta not in v for v in seen_by_b.values()), seen_by_b


# ── Spend and analysis ───────────────────────────────────────────────────────

def test_spend_is_latest_four_priced_quarters_times_volume(db, tenant_a, portfolio_a,
                                                           client_as):
    c = client_as(tenant_a)
    body = c.get(_url(f"/categories/{SLUG}/spend", tenant_a["team_id"])).json()
    assert body["currency"] == "EUR" and body["illustrative"] is False

    def ttm(price, vol):
        # Six quarters priced at price + 10 * i; the latest four are i = 2..5.
        return sum((price + 10 * i) * vol for i in range(2, 6))

    expected = {key: ttm(price, vol) for key, _c, _s, _site, price, vol in portfolio_a["specs"]}
    assert body["total"] == pytest.approx(sum(expected.values()))
    by_product = {r["pid"]: r["value"] for r in body["by_product"]}
    assert by_product[FERRIC] == pytest.approx(expected["fecl3_rtm"] + expected["fecl3_ant"])
    by_supplier = {r["name"]: r["value"] for r in body["by_supplier"]}
    assert by_supplier["Supplier one (test)"] == pytest.approx(
        expected["fecl3_rtm"] + expected["alum_rtm"])
    assert {r["name"] for r in body["by_site"]} == {"Rotterdam, NL", "Antwerp, BE"}
    assert body["concentration"]["suppliers"] == 2
    assert body["concentration_note"].startswith("100% of spend concentrated in the top 2")
    assert round(sum(r["pct"] for r in body["by_product"])) == 100

    evo = body["evolution"]
    assert [p["period"] for p in evo] == ["2025Q1", "2025Q2", "2025Q3", "2025Q4",
                                          "2026Q1", "2026Q2"]
    assert body["evolution_base"] == "2025Q1"
    assert evo[0]["actual_price_index"] == 100.0
    assert evo[0]["should_cost_index"] == 100.0
    assert evo[-1]["actual_price_index"] > 100.0

    landing = c.get(_url("/categories", tenant_a["team_id"])).json()
    coag = next(r for r in landing["suggested"] if r["playbook_slug"] == SLUG)
    assert coag["annual_spend"] == pytest.approx(body["total"])
    assert landing["fx_gaps"] == []          # everything is already in EUR


def test_landing_reports_fx_gaps_like_spend(tenant_a, portfolio_a, client_as):
    """Asked for another currency, the landing says which rates it lacked, as
    /spend does, instead of silently labelling unconverted money. Every cost
    model in the fixture is in coagulants, so the two lists are the same
    (empty if the database happens to hold the EUR→USD rates)."""
    c = client_as(tenant_a)
    team = tenant_a["team_id"]
    landing = c.get(_url("/categories", team) + "&reporting_currency=USD").json()
    spend = c.get(_url(f"/categories/{SLUG}/spend", team) + "&reporting_currency=USD").json()
    assert landing["currency"] == spend["currency"] == "USD"
    assert landing["fx_gaps"] == spend["fx_gaps"]
    assert all(g["currency"] == "EUR" for g in landing["fx_gaps"])


def test_analysis_shapes_the_report_for_the_strategic_tab(tenant_a, client_as):
    from app.services.strategy import PANEL_ORDER

    c = client_as(tenant_a)
    body = c.get(_url(f"/categories/{SLUG}/analysis", tenant_a["team_id"])).json()
    assert body["available"] is True and body["report_slug"] == SLUG
    panels = body["panels"]
    assert body["panel_order"] == [p for p in PANEL_ORDER if panels.get(p)]
    assert {"overview", "pestel", "porter", "kraljic"} <= set(body["panel_order"])
    assert len(panels["pestel"]["cards"]) == 6          # the six PESTEL factors
    assert len(panels["porter"]["rows"]) == 5 and panels["porter"]["implication"] is None
    k = panels["kraljic"]
    authored = _playbook(SLUG)["category"]["kraljic"]
    assert (k["cx"], k["cy"], k["badge"], k["boundary"]) == (
        authored["cx"], authored["cy"], authored["badge"], authored["boundary"])
    assert k["narrative"] and 'class="src"' in k["html"]
    keys = _report_line_keys(SLUG)
    n = len(keys - {None}) + (None in keys)
    assert body["caveat"]["split_into_n_lines"] == n
    if n > 1:
        assert f"split into {n} product lines" in body["caveat"]["text"]
    assert not panels["overview"]["html"].lstrip().startswith("<h2")


def test_playbook_without_report_is_an_empty_state(tenant_a, client_as):
    reported = {r["slug"] for r in expect.report_manifest()}
    without = sorted(_playbook_slugs() - reported)
    if not without:
        pytest.skip("every playbook in this drop has a report")
    c = client_as(tenant_a)
    r = c.get(_url(f"/categories/{without[0]}/analysis", tenant_a["team_id"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False and body["caveat"] is None
    assert body["panel_order"] == [] and body["kraljic"] is not None


# ── Input bounds and first-write races ───────────────────────────────────────

def test_savings_value_bounds_are_a_422_not_a_500(db, tenant_a, client_as):
    """`savings_value` sits in a Numeric(14, 2) column: out of range, negative
    or non-finite is refused before it reaches the database. NaN is sent as a
    raw JSON literal (the stdlib parser accepts it; a browser never sends it)."""
    a = client_as(tenant_a)
    team = tenant_a["team_id"]
    lever = _lever_id(db, "coagulants-1a")
    raw = {"headers": {"content-type": "application/json"}}
    for body in ('{"savings_value": 1e15}', '{"savings_value": -1}',
                 '{"savings_value": NaN}', '{"savings_value": Infinity}'):
        assert a.put(_url(f"/levers/{lever}", team), content=body, **raw).status_code == 422
        custom = body[:-1] + ', "gemstone": "VC", "title": "t"}'
        assert a.post(_url(f"/categories/{SLUG}/levers", team),
                      content=custom, **raw).status_code == 422
    ok = a.put(_url(f"/levers/{lever}", team), json={"savings_value": 999_999_999_999.99})
    assert ok.status_code == 200 and ok.json()["savings_value"] == 999_999_999_999.99
    created = a.post(_url(f"/categories/{SLUG}/levers", team),
                     json={"gemstone": "VC", "title": "Bounded", "savings_value": 0})
    assert created.status_code == 201
    cid = created.json()["custom_lever_id"]
    assert a.put(_url(f"/custom-levers/{cid}", team), content='{"savings_value": NaN}',
                 **raw).status_code == 422
    # Nothing unreadable was stored: the list still loads.
    assert a.get(_url(f"/categories/{SLUG}/levers", team)).status_code == 200


def test_blank_titles_and_bad_currency_are_refused(tenant_a, client_as):
    a = client_as(tenant_a)
    team = tenant_a["team_id"]
    assert a.post(_url(f"/categories/{SLUG}/levers", team),
                  json={"gemstone": "VC", "title": "   "}).status_code == 400
    assert a.post(_url(f"/categories/{SLUG}/actions", team),
                  json={"title": "   "}).status_code == 400
    assert a.get(_url("/categories", team) + "&reporting_currency=XYZQ").status_code == 422
    assert a.get(_url(f"/categories/{SLUG}/spend", team)
                 + "&reporting_currency=12").status_code == 422
    assert a.get(_url("/categories", team) + "&reporting_currency=usd").status_code == 200
    empty = a.get(_url("/categories", team) + "&reporting_currency=")   # = the team default
    assert empty.status_code == 200


def test_reset_with_no_override_writes_nothing(db, tenant_a, client_as):
    from app.models.audit_log import AuditLog
    a = client_as(tenant_a)
    lever = _lever_id(db, "coagulants-1b")
    r = a.put(_url(f"/levers/{lever}", tenant_a["team_id"]), json={"ease": None})
    assert r.status_code == 200 and r.json()["overridden"] is False
    assert db.query(AuditLog).filter(AuditLog.team_id == tenant_a["team_id"],
                                     AuditLog.entity_type == "lever_score").count() == 0


def _first_call_misses(monkeypatch, name: str, miss=None):
    """Make the router's pre-check return `miss` once, as if a concurrent
    request inserted the row between the check and the insert."""
    from app.routers import strategy as router_mod
    real = getattr(router_mod, name)
    calls = {"n": 0}

    def fake(*args, **kwargs):
        calls["n"] += 1
        return miss if calls["n"] == 1 else real(*args, **kwargs)
    monkeypatch.setattr(router_mod, name, fake)


def test_first_writes_that_lose_a_race_pick_up_the_winner(db, tenant_a, client_as,
                                                          monkeypatch):
    """The insert that hits the unique constraint rolls back to its savepoint
    and continues on the existing row — 200, never a 500."""
    a = client_as(tenant_a)
    team = tenant_a["team_id"]
    lever = _lever_id(db, "coagulants-1c")

    assert a.post(_url(f"/categories/{SLUG}/adopt", team)).json()["created"] is True
    _first_call_misses(monkeypatch, "_find_record")
    r = a.post(_url(f"/categories/{SLUG}/adopt", team))
    assert r.status_code == 200 and r.json()["created"] is False
    monkeypatch.undo()

    assert a.put(_url(f"/levers/{lever}", team), json={"ease": 2}).status_code == 200
    _first_call_misses(monkeypatch, "_find_score")
    r = a.put(_url(f"/levers/{lever}", team), json={"ease": 5})
    assert r.status_code == 200 and r.json()["ease"] == 5
    monkeypatch.undo()

    body = [{"code": "quality", "selected": True, "priority": "High", "note": "first"}]
    assert a.put(_url(f"/categories/{SLUG}/objectives", team), json=body).status_code == 200
    _first_call_misses(monkeypatch, "_find_objectives", miss={})
    body[0]["note"] = "second"
    r = a.put(_url(f"/categories/{SLUG}/objectives", team), json=body)
    assert r.status_code == 200
    quality = next(o for o in r.json()["objectives"] if o["code"] == "quality")
    assert quality["note"] == "second"
    monkeypatch.undo()

    db.expire_all()
    assert db.query(StrategyRecord).filter(StrategyRecord.team_id == team).count() == 1
    assert db.query(LeverScore).filter(LeverScore.team_id == team).count() == 1
    assert db.query(TeamObjective).filter(TeamObjective.team_id == team,
                                          TeamObjective.objective_code == "quality").count() == 1


def test_parallel_first_writes_all_succeed(db, tenant_a):
    """A double click, for real: three requests at once, each on its own
    client. Every one answers 200 and exactly one row exists afterwards."""
    import threading
    from fastapi.testclient import TestClient
    from app.main import app

    team = tenant_a["team_id"]
    lever = _lever_id(db, "coagulants-2a")
    codes: list = []
    lock = threading.Lock()

    def hammer(method: str, url: str, json=None):
        barrier = threading.Barrier(3)

        def worker(k: int):
            c = TestClient(app, raise_server_exceptions=False)
            c.cookies.set("ca_token", tenant_a["token"])
            barrier.wait()
            r = c.request(method, url, json=(json(k) if callable(json) else json))
            with lock:
                codes.append((url, r.status_code))
        threads = [threading.Thread(target=worker, args=(k,)) for k in range(3)]
        [t.start() for t in threads]
        [t.join() for t in threads]

    hammer("POST", _url("/categories/alkalis/adopt", team))
    hammer("PUT", _url(f"/levers/{lever}", team), json=lambda k: {"ease": k + 1})
    hammer("PUT", _url(f"/categories/{SLUG}/objectives", team),
           json=lambda k: [{"code": "innovation", "selected": True, "note": f"n{k}"}])
    assert [c for _u, c in codes] == [200] * 9, codes
    db.expire_all()
    assert db.query(StrategyRecord).filter(StrategyRecord.team_id == team,
                                           StrategyRecord.playbook_slug == "alkalis").count() == 1
    assert db.query(LeverScore).filter(LeverScore.team_id == team,
                                       LeverScore.lever_id == lever).count() == 1
    assert db.query(TeamObjective).filter(TeamObjective.team_id == team,
                                          TeamObjective.objective_code == "innovation").count() == 1


# ── Team industry, custom lever defaults, currency and FX completeness ───────

NAOH = "BCI-NAOH-SOL"        # caustic soda — reaches alkalis, not coagulants


def test_custom_lever_null_status_starts_identified(tenant_a, client_as):
    a = client_as(tenant_a)
    team = tenant_a["team_id"]
    for body in ({"gemstone": "VC", "title": "Explicit null", "status": None},
                 {"gemstone": "VC", "title": "Left out"}):
        r = a.post(_url(f"/categories/{SLUG}/levers", team), json=body)
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "Identified"
    bad = a.post(_url(f"/categories/{SLUG}/levers", team),
                 json={"gemstone": "VC", "title": "x", "status": "Maybe"})
    assert bad.status_code == 400


def test_reporting_currency_must_be_a_known_code(tenant_a, portfolio_a, client_as):
    a = client_as(tenant_a)
    team = tenant_a["team_id"]
    for path in ("/categories", f"/categories/{SLUG}/spend"):
        r = a.get(_url(path, team) + "&reporting_currency=XYZ")
        assert r.status_code == 422, path
        assert r.json()["detail"].startswith("Unknown reporting currency: XYZ. One of: ")
    assert a.post(_url(f"/categories/{SLUG}/adopt", team)
                  + "&reporting_currency=QQQ").status_code == 422
    # The team's own currency and the app's fallback are always known.
    assert a.get(_url("/categories", team) + "&reporting_currency=eur").status_code == 200
    assert a.get(_url("/categories", team) + "&reporting_currency=USD").status_code == 200


def test_fx_gaps_flag_the_totals_they_touch(db, tenant_a, portfolio_a, client_as, monkeypatch):
    """With no FX rate at all, every converted total is flagged — and only
    the totals whose money needed the missing rate: a USD product in another
    category leaves the EUR coagulants spend complete."""
    from app.services import strategy as svc
    monkeypatch.setattr(svc, "get_fx_rate", lambda *a, **k: None)
    team, user = tenant_a["team_id"], tenant_a["user_id"]
    p = Product(team_id=team, created_by=user, name="Caustic soda (test)", unit="t",
                formula_template_id=_template_id(db, NAOH))
    db.add(p)
    db.flush()
    cm = CostModel(team_id=team, product_id=p.id, region="Europe", currency="USD",
                   created_by=user)
    db.add(cm)
    db.flush()
    db.add(ActualPrice(cost_model_id=cm.id, uploaded_by=user, year=2026, quarter=2, price=500))
    db.add(ActualVolume(cost_model_id=cm.id, uploaded_by=user, year=2026, quarter=2,
                        volume=10, unit="t"))
    db.commit()
    c = client_as(tenant_a)

    eur = c.get(_url("/categories", team) + "&reporting_currency=EUR").json()
    rows = {r["playbook_slug"]: r for r in eur["adopted"] + eur["suggested"]}
    assert rows[SLUG]["fx_incomplete"] is False
    assert rows["alkalis"]["fx_incomplete"] is True
    assert eur["fx_incomplete"] is True
    assert eur["fx_gaps"] == [{"currency": "USD", "period": "2026Q2"}]
    spend = c.get(_url(f"/categories/{SLUG}/spend", team) + "&reporting_currency=EUR").json()
    assert spend["fx_gaps"] == [] and spend["fx_incomplete"] is False

    usd = c.get(_url(f"/categories/{SLUG}/spend", team) + "&reporting_currency=USD").json()
    assert usd["fx_incomplete"] is True
    assert {g["currency"] for g in usd["fx_gaps"]} == {"EUR"}
    landing = c.get(_url("/categories", team) + "&reporting_currency=USD").json()
    assert landing["fx_incomplete"] is True
    assert {r["playbook_slug"]: r["fx_incomplete"] for r in landing["suggested"]}[SLUG] is True


def _servable_leader(db, pids) -> str | None:
    """The inference rule, written as SQL: most servable placements, ties by name."""
    from sqlalchemy import text
    return db.execute(text(
        "SELECT i.slug FROM category_placements cp "
        "JOIN categories c ON c.id = cp.category_id JOIN industries i ON i.id = cp.industry_id "
        "WHERE cp.pid = ANY(:pids) AND c.status = 'servable' "
        "GROUP BY i.slug, i.name ORDER BY count(*) DESC, lower(i.name) LIMIT 1"),
        {"pids": list(pids)}).scalar()


def test_team_industry_is_inferred_from_the_portfolio(db, tenant_a, portfolio_a, client_as):
    c = client_as(tenant_a)
    team = tenant_a["team_id"]
    expected = _servable_leader(db, [FERRIC, ALUM])
    landing = c.get(_url("/categories", team)).json()
    if expected is None:
        assert landing["team_industry"] is None
        return
    assert landing["team_industry"]["slug"] == expected
    assert landing["team_industry"]["source"] == "inferred"
    detail = c.get(_url(f"/categories/{SLUG}", team)).json()
    assert detail["team_industry"] == landing["team_industry"]
    demand = detail["demand_categories"]
    lead = [d["industry_slug"] == expected for d in demand]
    assert lead[0] and lead == sorted(lead, reverse=True)     # that industry's rows first


def test_team_industry_declared_on_products_leads_the_demand_categories(
        db, tenant_a, portfolio_a, client_as):
    from app.models.taxonomy_v2 import Industry
    from app.services.demo_buyer import BUYER_INDUSTRY

    industry = db.query(Industry).filter(Industry.slug == BUYER_INDUSTRY).one()
    for product in portfolio_a["products"].values():
        product.custom_attributes = {"buyer_industry": BUYER_INDUSTRY}
    db.commit()
    c = client_as(tenant_a)
    team = tenant_a["team_id"]
    landing = c.get(_url("/categories", team)).json()
    assert landing["team_industry"] == {"slug": BUYER_INDUSTRY, "name": industry.name,
                                        "source": "declared"}
    demand = c.get(_url(f"/categories/{SLUG}", team)).json()["demand_categories"]
    mw = [d for d in demand if d["industry_slug"] == BUYER_INDUSTRY]
    assert mw and demand[:len(mw)] == mw
    # Within the rest, what the team buys still comes before what it does not.
    rest = [bool(d["team_pids"]) for d in demand[len(mw):]]
    assert rest == sorted(rest, reverse=True)


def test_team_industry_is_null_without_catalogue_products(tenant_a, client_as):
    c = client_as(tenant_a)
    team = tenant_a["team_id"]
    assert c.get(_url("/categories", team)).json()["team_industry"] is None
    assert c.get(_url(f"/categories/{SLUG}", team)).json()["team_industry"] is None


# ── Effective lines, unpublished keys and the listed catalogue ───────────────

def _coverage_id(db, code: str, region: str = "Europe") -> uuid.UUID:
    cov = db.query(FormulaRegionCoverage).filter(
        FormulaRegionCoverage.template_id == _template_id(db, code),
        FormulaRegionCoverage.region == region,
        FormulaRegionCoverage.withdrawn_at.is_(None)).first()
    assert cov is not None, f"{code} has no {region} coverage: rebuild the test database"
    return cov.id


def _line_of(db, code: str) -> ProductLine:
    line = db.get(ProductLine, db.get(FormulaTemplate, _template_id(db, code)).product_line_id)
    assert line is not None and line.line_key == expect.template_line(code)
    return line


def test_a_builder_tracked_product_reaches_its_playbook(db, tenant_a, client_as):
    """A product with no catalogue link, tracked from the cost-model builder
    (its formula version is priced from a catalogue combo), reaches the
    playbooks of that combo's line: effective_lines() path 2."""
    team, user = tenant_a["team_id"], tenant_a["user_id"]
    p = Product(team_id=team, created_by=user, name="Tracked product (test)", unit="t")
    db.add(p)
    db.flush()
    cm = CostModel(team_id=team, product_id=p.id, region="Europe", currency="EUR",
                   created_by=user)
    db.add(cm)
    db.flush()
    db.add(FormulaVersion(cost_model_id=cm.id, base_price=100, base_year=2023, base_quarter=1,
                          source_coverage_id=_coverage_id(db, FERRIC), link_mode="tracking"))
    db.commit()

    c = client_as(tenant_a)
    suggested = {r["playbook_slug"]: r for r in c.get(_url("/categories", team)).json()["suggested"]}
    assert set(suggested) == _playbooks_of(FERRIC)
    assert [x["pid"] for x in suggested[SLUG]["products"]] == [FERRIC]
    (tp,) = c.get(_url(f"/categories/{SLUG}", team)).json()["team_products"]
    line = _line_of(db, FERRIC)
    assert (tp["pid"], tp["taxonomy_source"], tp["product_line_id"], tp["line_key"]) == (
        FERRIC, "cost_model", line.id, line.line_key)


def test_a_custom_product_reaches_the_playbooks_of_its_own_line(db, tenant_a, client_as):
    """A custom product (no catalogue link, no cost model) with a line picked
    by hand reaches that line's playbooks: effective_lines() path 3."""
    team, user = tenant_a["team_id"], tenant_a["user_id"]
    line = _line_of(db, ALUM)
    db.add(Product(team_id=team, created_by=user, name="Custom product (test)", unit="t",
                   product_line_id=line.id))
    db.commit()

    c = client_as(tenant_a)
    suggested = {r["playbook_slug"] for r in c.get(_url("/categories", team)).json()["suggested"]}
    assert suggested == _playbooks_of_line(line.line_key) and SLUG in suggested
    (tp,) = c.get(_url(f"/categories/{SLUG}", team)).json()["team_products"]
    assert (tp["pid"], tp["taxonomy_source"], tp["product_line_id"], tp["line_name"]) == (
        None, "manual", line.id, line.name)


def test_an_off_axis_product_reaches_no_playbook(db, tenant_a, client_as):
    """A catalogue product whose record key is no current line has no line
    ("Product line not yet published"). It reaches no playbook, even when a
    report with a playbook is joined to that key, and the key is not served."""
    off_axis = expect.off_axis_record_keys()
    joined = {k for keys in _unpublished_joins().values() for k in keys}
    pid = next((p for p, k in sorted(off_axis.items()) if k in joined), None) \
        or sorted(off_axis)[0]
    team, user = tenant_a["team_id"], tenant_a["user_id"]
    db.add(Product(team_id=team, created_by=user, name="Off-axis product (test)", unit="t",
                   formula_template_id=_template_id(db, pid)))
    db.commit()

    c = client_as(tenant_a)
    landing = c.get(_url("/categories", team)).json()
    assert landing["adopted"] == [] and landing["suggested"] == []
    assert off_axis[pid] not in json.dumps(landing, ensure_ascii=False)


def test_a_report_key_that_is_no_current_line_is_never_served(tenant_a, client_as):
    """Design §4.1 (decision 37): a report row whose key resolves to no
    current line is one entry, "Product line not yet published", with no id
    and no key — in the category's lines and in the analysis caveat. Neither
    the stored key nor its tail is a line there, and the stored key appears
    nowhere in either payload. (The report's and playbook's own titles are
    theirs, shown as authored.)"""
    joins = _unpublished_joins()
    if not joins:
        pytest.skip("no report in this drop is joined to a key that is no current line")
    c = client_as(tenant_a)
    team = tenant_a["team_id"]
    for slug, keys in sorted(joins.items()):
        hidden = keys | {expect.line_tail(k) for k in keys}
        detail = c.get(_url(f"/categories/{slug}", team)).json()
        analysis = c.get(_url(f"/categories/{slug}/analysis", team)).json()

        unpublished = [ln for ln in detail["lines"] if not ln["published"]]
        assert len(unpublished) == 1, (slug, detail["lines"])
        assert (unpublished[0]["name"], unpublished[0]["line_key"],
                unpublished[0]["product_line_id"]) == (UNPUBLISHED_LINE, None, None)
        assert detail["lines"][-1] is not None and not detail["lines"][-1]["published"]
        published = {ln["line_key"] for ln in detail["lines"] if ln["published"]}
        assert published == _report_line_keys(slug) - {None}
        caveat_lines = (analysis["caveat"] or {}).get("lines") or []
        for ln in detail["lines"] + caveat_lines:
            assert ln.get("line_key") not in hidden and ln["name"] not in hidden, (slug, ln)
        for payload in (detail, analysis):
            text = json.dumps(payload, ensure_ascii=False)
            assert not any(k in text for k in keys), slug


def test_catalogue_products_are_the_listed_cards_on_the_playbooks_lines(tenant_a, client_as):
    detail = client_as(tenant_a).get(_url(f"/categories/{SLUG}", tenant_a["team_id"])).json()
    lines = _report_line_keys(SLUG) - {None}
    expected = {pid for pid in expect.listed_codes()
                if expect.classify(pid).kind == "product" and expect.template_line(pid) in lines}
    got = {p["pid"]: p for p in detail["catalogue_products"]}
    assert set(got) == expected
    for pid, p in got.items():
        assert p["line_key"] == expect.template_line(pid)
        assert p["supply_status"] == expect.classify(pid).supply_status
    # Every demand category row places this category's products as the tree does.
    placed = expect.placements()
    assert detail["demand_categories"]
    for row in detail["demand_categories"]:
        assert all((row["code"], pid) in placed for pid in row["pids"]), row["code"]


def test_the_intelligence_caveat_is_used_only_when_it_names_no_unpublished_line(
        db, tenant_a, client_as, monkeypatch):
    """The analysis tab shows `intel_reference.caveat_for`'s caveat (one
    wording on both pages) unless one of its lines carries a stored key that
    is no current line, or that key's tail; then the strategy service builds
    its own from the resolved lines."""
    from app.services import intel_reference

    joins = _unpublished_joins()
    if not joins:
        pytest.skip("no report in this drop is joined to a key that is no current line")
    slug, keys = sorted(joins.items())[0]
    key = sorted(keys)[0]
    clean = {"as_of": None, "text": "Made-up caveat text.", "old_line_name": "Made-up old line",
             "split_into_n_lines": 1,
             "lines": [{"line_key": None, "name": UNPUBLISHED_LINE, "visible": False}]}
    dirty = {**clean, "lines": [{"line_key": key, "name": expect.line_tail(key),
                                 "visible": True}]}
    monkeypatch.setattr(intel_reference, "get_snapshot", lambda _db: None)
    c = client_as(tenant_a)
    url = _url(f"/categories/{slug}/analysis", tenant_a["team_id"])

    monkeypatch.setattr(intel_reference, "caveat_for", lambda _snap, _slug: clean)
    assert c.get(url).json()["caveat"]["text"] == clean["text"]

    monkeypatch.setattr(intel_reference, "caveat_for", lambda _snap, _slug: dirty)
    caveat = c.get(url).json()["caveat"]
    assert caveat["text"] != dirty["text"]
    assert all(ln["line_key"] != key and ln["name"] != expect.line_tail(key)
               for ln in caveat["lines"])
    assert key not in json.dumps(caveat, ensure_ascii=False)
