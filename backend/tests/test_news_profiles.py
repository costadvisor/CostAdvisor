"""SCRUM-213 / NEWS-1: search profile API, Claude drafts and the test run.

Runs against the dev Postgres like the rest of the suite. Claude is never
called: ``anthropic_client.create_message`` is replaced with a fake that
returns canned API responses, so these tests are free and deterministic.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.audit_log import AuditLog
from app.models.chemical_family import ChemicalFamily
from app.models.producer import Producer, ProducerAlias
from app.models.region import Region
from app.models.search_profile import SearchProfile
from app.models.product_line import ProductLine
from app.models.subfamily import Subfamily
from app.services import anthropic_client as claude
from app.services.news_profile_drafts import Draft, save_draft
from app.schemas.search_profile import ProfileFields

TODAY = datetime.now(timezone.utc).date()


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def catalogue(db):
    tag = uuid.uuid4().hex[:6]
    fam = ChemicalFamily(code=f"T{tag}", name=f"Fiberglass {tag}")
    other = ChemicalFamily(code=f"U{tag}", name=f"Other {tag}")
    db.add_all([fam, other]); db.flush()
    sub = Subfamily(family_id=fam.id, name="Continuous filament")
    db.add(sub); db.flush()
    line = ProductLine(family_id=fam.id, subfamily_id=sub.id, platform=f"PLAT-{tag}-ROV",
                       line_key=f"Fiberglass {tag}|||E-glass roving", name="E-glass roving")
    retired = ProductLine(family_id=fam.id, platform=f"PLAT-{tag}-OLD",
                          line_key=f"Fiberglass {tag}|||Old line", name="Old line",
                          retired_at=datetime.now(timezone.utc))
    other_line = ProductLine(family_id=other.id, platform=f"PLAT-{tag}-ELS",
                             line_key=f"Other {tag}|||Elsewhere", name="Elsewhere")
    owens = Producer(name=f"Owens Corning {tag}", normalized_name=f"owens corning {tag}")
    jushi = Producer(name=f"Jushi {tag}", normalized_name=f"jushi {tag}")
    eu = Region(code=f"EU{tag}", name="Europe")
    nwe = Region(code=f"NW{tag}", name="NW Europe")
    db.add_all([line, retired, other_line, owens, jushi, eu, nwe]); db.flush()
    db.add(ProducerAlias(producer_id=owens.id, raw_value="OC", normalized="oc", match_key="oc"))
    db.commit()
    data = dict(family=fam.id, other=other.id, line=line.id, retired=retired.id, other_line=other_line.id,
                owens=owens.id, jushi=jushi.id, eu=eu.code, nwe=nwe.code)
    yield data
    db.execute(text("DELETE FROM chemical_families WHERE id IN (:a, :b)"), {"a": fam.id, "b": other.id})
    db.execute(text("DELETE FROM producers WHERE id IN (:a, :b)"), {"a": str(owens.id), "b": str(jushi.id)})
    db.execute(text("DELETE FROM regions WHERE code IN (:a, :b)"), {"a": eu.code, "b": nwe.code})
    db.commit()


@pytest.fixture(autouse=True)
def news_switched_on(monkeypatch):
    """The feature is behind NEWS_PREVIEW_EMAILS; these tests run with it on
    for everyone ("*"). The flag itself is tested at the end of the file."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "news_preview_emails", "*")


@pytest.fixture
def editor(user_factory, client_as):
    return client_as(user_factory(is_super_admin=True))


def _form(c, **over):
    body = dict(terms=["fiberglass", "glass fibre"], synonyms=["E-glass"], cas_numbers=["65997-17-3"],
                exclude_terms=["boat"], event_types=["new_plant", "capacity_change", "m_and_a"],
                preferred_sites=["https://www.compositesworld.com/news"],
                producer_ids=[str(c["owens"])], region_codes=[c["eu"]], min_relevance=0.6,
                lookback_days=31)
    body.update(over)
    return body


class FakeClaude:
    """Replays canned API responses, in order, and records each request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        content, stop, usage = self.responses.pop(0)
        return claude.ClaudeResponse(content=content, stop_reason=stop, model="claude-sonnet-test",
                                     usage=claude.usage_from_api("claude-sonnet-test", usage))


@pytest.fixture
def fake_claude(monkeypatch):
    monkeypatch.setattr(claude, "is_configured", lambda: True)

    def install(*responses):
        fake = FakeClaude(*responses)
        monkeypatch.setattr(claude, "create_message", fake)
        return fake
    return install


# ── Sub-task 2: the editor API ───────────────────────────────────────────────

def test_requires_platform_content_edit(user_factory, client_as, catalogue):
    plain = client_as(user_factory())
    assert plain.get("/api/news/profiles").status_code == 403
    assert plain.put(f"/api/news/profiles/{catalogue['family']}", json=_form(catalogue)).status_code == 403


def test_create_and_edit_family_profile(editor, catalogue, db):
    fid = catalogue["family"]
    r = editor.get(f"/api/news/profiles/{fid}")
    assert r.status_code == 200 and r.json()["profile"] is None
    lines = r.json()["product_lines"]
    assert [(l["name"], l["subfamily_name"]) for l in lines] == [("E-glass roving", "Continuous filament")]

    r = editor.put(f"/api/news/profiles/{fid}", json=_form(catalogue, is_active=True))
    assert r.status_code == 200, r.text
    p = r.json()["profile"]
    assert p["provenance"] == "human_edited" and p["is_active"] is True
    assert p["preferred_sites"] == ["compositesworld.com"]
    assert [x["name"] for x in p["producers"]][0].startswith("Owens Corning")
    assert p["regions"] == [{"code": catalogue["eu"], "name": "Europe"}]

    # Edit: the whole form is sent, so a cleared list is really cleared.
    r = editor.put(f"/api/news/profiles/{fid}", json=_form(catalogue, exclude_terms=[], is_active=True))
    assert r.json()["profile"]["exclude_terms"] == []

    row = db.query(SearchProfile).filter_by(family_id=fid, product_line_id=None).one()
    logs = db.query(AuditLog).filter_by(entity_type="search_profile", entity_id=str(row.id)).all()
    assert sorted(l.event_type for l in logs) == ["create", "update"]
    update = next(l for l in logs if l.event_type == "update")
    assert update.previous_value["exclude_terms"] == ["boat"] and update.new_value["exclude_terms"] == []

    listed = {f["family_id"]: f for f in editor.get("/api/news/profiles").json()}
    assert listed[fid]["has_profile"] and listed[fid]["is_active"]
    assert listed[catalogue["other"]]["has_profile"] is False


def test_override_merges_and_can_be_removed(editor, catalogue):
    fid, sid = catalogue["family"], catalogue["line"]
    url = f"/api/news/profiles/{fid}/lines/{sid}"
    # An override needs a family profile first.
    assert editor.put(url, json={"terms": ["roving"]}).status_code == 422

    editor.put(f"/api/news/profiles/{fid}", json=_form(catalogue))
    r = editor.put(url, json={"terms": ["roving", "Fiberglass"], "region_codes": [catalogue["nwe"]],
                              "min_relevance": 0.75})
    assert r.status_code == 200, r.text
    assert r.json()["product_lines"][0]["has_override"] is True

    eff = editor.get(f"/api/news/profiles/{fid}/effective", params={"product_line_id": sid}).json()
    assert eff["terms"] == ["fiberglass", "glass fibre", "roving"]
    assert eff["region_codes"] == [catalogue["eu"], catalogue["nwe"]]
    assert eff["min_relevance"] == 0.75 and eff["lookback_days"] == 31

    r = editor.delete(url)
    assert r.status_code == 200 and r.json()["overrides"] == []
    eff = editor.get(f"/api/news/profiles/{fid}/effective", params={"product_line_id": sid}).json()
    assert eff["min_relevance"] == 0.6
    assert editor.delete(url).status_code == 404


def test_rejects_bad_input(editor, catalogue, db):
    fid = catalogue["family"]
    assert editor.put(f"/api/news/profiles/{fid}",
                      json=_form(catalogue, producer_ids=[str(uuid.uuid4())])).status_code == 422
    assert editor.put(f"/api/news/profiles/{fid}",
                      json=_form(catalogue, region_codes=["NOPE-XX"])).status_code == 422
    assert editor.put(f"/api/news/profiles/{fid}",
                      json=_form(catalogue, cas_numbers=["65997-17-4"])).status_code == 422
    editor.put(f"/api/news/profiles/{fid}", json=_form(catalogue))
    # A line from another family, and a retired line.
    assert editor.put(f"/api/news/profiles/{fid}/lines/{catalogue['other_line']}",
                      json={"terms": ["x"]}).status_code == 422
    assert editor.put(f"/api/news/profiles/{fid}/lines/{catalogue['retired']}",
                      json={"terms": ["x"]}).status_code == 422


def test_unknown_family_is_404(editor, catalogue):
    assert editor.get("/api/news/profiles/999999999").status_code == 404
    assert editor.put("/api/news/profiles/999999999", json=_form(catalogue)).status_code == 404


def test_producer_picker(editor, catalogue):
    names = [p["name"] for p in editor.get("/api/news/producers", params={"q": "owens corning"}).json()]
    assert any(n.startswith("Owens Corning") for n in names)


# ── Sub-task 3: Claude drafts ────────────────────────────────────────────────

def _draft_response(c):
    return ([{"type": "tool_use", "id": "t1", "name": "save_profile", "input": {
        "terms": ["fiberglass", "glass fiber"], "synonyms": ["E-glass", "GRP"],
        "cas_numbers": ["65997-17-3", "12345-67-8"],
        "exclude_terms": ["boat", "pool"],
        "producers": [f"Jushi {c['eu'][2:]}", "Made Up Fibre Co"],
        "region_codes": [c["eu"], "ATLANTIS"],
        "event_types": ["new_plant", "closure", "bogus"],
        "preferred_sites": ["compositesworld.com"], "min_relevance": 0.65, "lookback_days": 31,
        "rationale": "Check the producer list.",
    }}], "tool_use", {"input_tokens": 2000, "output_tokens": 400})


def test_draft_is_returned_not_saved(editor, catalogue, fake_claude, db):
    fake = fake_claude(_draft_response(catalogue))
    r = editor.post(f"/api/news/profiles/{catalogue['family']}/draft")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["fields"]["terms"] == ["fiberglass", "glass fiber"]
    assert d["fields"]["cas_numbers"] == ["65997-17-3"]           # bad check digit dropped
    assert [p["id"] for p in d["producers"]] == [str(catalogue["jushi"])]
    assert d["fields"]["region_codes"] == [catalogue["eu"]]        # unknown region dropped
    assert d["fields"]["event_types"] == ["new_plant", "closure"]
    assert any("12345-67-8" in x for x in d["dropped"])
    assert any("Made Up Fibre Co" in x for x in d["dropped"])
    assert d["cost_usd"] > 0
    # The prompt is built from our data: our producers are listed for Claude.
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "Producers we track" in prompt or "no producers linked" in prompt
    assert db.query(SearchProfile).filter_by(family_id=catalogue["family"]).count() == 0


def test_save_draft_never_overwrites_a_human_edit(editor, catalogue, db):
    fid = catalogue["family"]
    draft = Draft(fields=ProfileFields(terms=["drafted"]), rationale="r")
    assert save_draft(db, fid, draft) == "created"
    db.commit()
    row = db.query(SearchProfile).filter_by(family_id=fid, product_line_id=None).one()
    assert row.provenance == "ai_draft" and row.is_active is False

    assert save_draft(db, fid, Draft(fields=ProfileFields(terms=["redrafted"]))) == "updated"
    db.commit()
    editor.put(f"/api/news/profiles/{fid}", json=_form(catalogue))   # a person edits it
    assert save_draft(db, fid, Draft(fields=ProfileFields(terms=["again"]))) == "skipped (edited by a person)"
    db.expire_all()
    assert db.query(SearchProfile).filter_by(family_id=fid, product_line_id=None).one().terms[0] == "fiberglass"


def test_claude_not_configured(editor, catalogue, monkeypatch):
    monkeypatch.setattr(claude, "is_configured", lambda: False)
    assert editor.post(f"/api/news/profiles/{catalogue['family']}/draft").status_code == 503
    assert editor.post(f"/api/news/profiles/{catalogue['family']}/test", json={}).status_code == 503


# ── Sub-task 4: the test run ─────────────────────────────────────────────────

def _search_turn(urls):
    return [
        {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "fiberglass new plant"}},
        {"type": "web_search_tool_result", "tool_use_id": "s1",
         "content": [{"type": "web_search_result", "url": u, "title": "t"} for u in urls]},
    ]


def _report(items):
    return {"type": "tool_use", "id": "r1", "name": "report_news", "input": {"items": items}}


def _item(url, headline="Glass-fibre maker opens furnace in Poland", **over):
    base = dict(headline=headline, url=url, source="Trade press",
                summary="A new furnace adds EU capacity.", event_type="new_plant",
                relevance=0.9, published_date=(TODAY - timedelta(days=5)).isoformat())
    base.update(over)
    return base


def test_test_run_filters_and_saves_nothing(editor, catalogue, fake_claude, db):
    fid = catalogue["family"]
    editor.put(f"/api/news/profiles/{fid}",
               json=_form(catalogue, blocked_sites=["spam.example"]))
    good, low, boat, old, blocked, unwanted = (
        "https://www.trade.example/a/", "https://trade.example/b", "https://trade.example/c",
        "https://trade.example/d", "https://news.spam.example/e", "https://trade.example/f")
    fake = fake_claude(
        (_search_turn([good, low, boat, old, blocked, unwanted]), "pause_turn",
         {"input_tokens": 5000, "output_tokens": 100, "server_tool_use": {"web_search_requests": 2}}),
        ([_report([
            _item("https://trade.example/a"),                      # kept (url normalised)
            _item(low, headline="Minor note", relevance=0.3),       # below threshold
            _item(boat, headline="Fiberglass boat sale"),           # excluded term
            _item(old, published_date="2020-01-01"),                # outside the window
            _item(blocked),                                          # blocked site (subdomain)
            _item(unwanted, event_type="regulation"),               # event type not wanted
            _item("https://invented.example/x"),                     # never in search results
            _item("https://trade.example/a"),                        # duplicate
        ])], "tool_use", {"input_tokens": 3000, "output_tokens": 800}),
    )
    before = db.execute(text("SELECT count(*) FROM search_profiles")).scalar()
    audit_before = db.execute(text("SELECT count(*) FROM audit_logs")).scalar()

    r = editor.post(f"/api/news/profiles/{fid}/test", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["saved"] is False and out["error"] is None
    assert [i["url"] for i in out["kept"]] == ["https://trade.example/a"]
    assert [i["headline"] for i in out["below_threshold"]] == ["Minor note"]
    reasons = sorted(i["drop_reason"] for i in out["dropped"])
    assert reasons == sorted(["excluded_term", "outside_time_window", "blocked_site",
                              "event_type_not_wanted", "link_not_in_search_results", "duplicate"])
    assert out["queries"] == ["fiberglass new plant"]
    assert out["searches"] == 2 and out["cost_usd"] > 0.02

    # pause_turn continued the same conversation, and the blocked site went to the search tool.
    assert len(fake.calls) == 2 and fake.calls[1]["messages"][-1]["role"] == "assistant"
    search_tool = fake.calls[0]["tools"][0]
    assert search_tool["blocked_domains"] == ["spam.example"] and search_tool["max_uses"] <= 5
    assert "Owens Corning" in fake.calls[0]["messages"][0]["content"]
    assert "also: OC" in fake.calls[0]["messages"][0]["content"]

    assert db.execute(text("SELECT count(*) FROM search_profiles")).scalar() == before
    assert db.execute(text("SELECT count(*) FROM audit_logs")).scalar() == audit_before


def test_test_run_with_unsaved_form(editor, catalogue, fake_claude, db):
    fid = catalogue["family"]
    fake = fake_claude(([_report([])], "tool_use", {"input_tokens": 100, "output_tokens": 10}))
    r = editor.post(f"/api/news/profiles/{fid}/test",
                    json={"fields": {"terms": ["unsaved term"], "lookback_days": 14}})
    assert r.status_code == 200, r.text
    assert "unsaved term" in fake.calls[0]["messages"][0]["content"]
    assert r.json()["window_start"] == (TODAY - timedelta(days=14)).isoformat()
    assert db.query(SearchProfile).filter_by(family_id=fid).count() == 0


def test_test_run_without_any_profile(editor, catalogue, fake_claude):
    fake_claude()
    r = editor.post(f"/api/news/profiles/{catalogue['family']}/test", json={})
    assert r.status_code == 404


def test_test_run_forces_a_report_when_claude_just_answers(editor, catalogue, fake_claude):
    editor.put(f"/api/news/profiles/{catalogue['family']}", json=_form(catalogue))
    fake = fake_claude(
        ([{"type": "text", "text": "I found nothing much."}], "end_turn", {"input_tokens": 10, "output_tokens": 5}),
        ([_report([])], "tool_use", {"input_tokens": 10, "output_tokens": 5}),
    )
    out = editor.post(f"/api/news/profiles/{catalogue['family']}/test", json={}).json()
    assert out["error"] is None and out["kept"] == []
    assert fake.calls[1]["tool_choice"] == {"type": "tool", "name": "report_news"}


def test_test_run_reports_api_failure(editor, catalogue, monkeypatch):
    editor.put(f"/api/news/profiles/{catalogue['family']}", json=_form(catalogue))
    monkeypatch.setattr(claude, "is_configured", lambda: True)

    def boom(**kwargs):
        raise claude.AnthropicError("Claude API error 529: overloaded")
    monkeypatch.setattr(claude, "create_message", boom)
    out = editor.post(f"/api/news/profiles/{catalogue['family']}/test", json={}).json()
    assert "overloaded" in out["error"] and out["kept"] == []


def test_usage_cost_includes_searches():
    u = claude.usage_from_api("claude-sonnet-x", {"input_tokens": 1_000_000, "output_tokens": 0,
                                                  "server_tool_use": {"web_search_requests": 100}})
    assert u.cost_usd == pytest.approx(3.0 + 1.0)
    unknown = claude.usage_from_api("some-new-model", {"input_tokens": 1_000_000})
    assert unknown.cost_usd >= 3.0   # unknown models are over-, never under-estimated


# ── Refinements: empty profiles, dates, time budget, rate limits, editor aids ─

def test_family_profile_needs_something_to_search_for(editor, catalogue):
    fid = catalogue["family"]
    r = editor.put(f"/api/news/profiles/{fid}", json=_form(catalogue, terms=[], synonyms=[], cas_numbers=[]))
    assert r.status_code == 422 and "search term" in r.json()["detail"]
    # A synonym alone is enough; an override may be empty (it inherits).
    assert editor.put(f"/api/news/profiles/{fid}", json=_form(catalogue, terms=[], cas_numbers=[])).status_code == 200
    assert editor.put(f"/api/news/profiles/{fid}/lines/{catalogue['line']}", json={}).status_code == 200


def test_test_run_refuses_an_empty_form(editor, catalogue, fake_claude):
    fake = fake_claude()
    r = editor.post(f"/api/news/profiles/{catalogue['family']}/test", json={"fields": {"terms": []}})
    assert r.status_code == 422 and fake.calls == []


def test_undated_stories_use_the_search_page_age(editor, catalogue, fake_claude):
    fid = catalogue["family"]
    editor.put(f"/api/news/profiles/{fid}", json=_form(catalogue))
    fresh, stale, unknown = ("https://trade.example/fresh", "https://trade.example/stale",
                             "https://trade.example/unknown")
    fake_claude(([
        {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "q"}},
        {"type": "web_search_tool_result", "tool_use_id": "s1", "content": [
            {"type": "web_search_result", "url": fresh, "page_age": "3 days ago"},
            {"type": "web_search_result", "url": stale, "page_age": "January 5, 2019"},
            {"type": "web_search_result", "url": unknown}]},
        _report([_item(fresh, published_date=None), _item(stale, published_date=None),
                 _item(unknown, published_date=None)]),
    ], "tool_use", {"input_tokens": 10, "output_tokens": 5}))
    out = editor.post(f"/api/news/profiles/{fid}/test", json={}).json()
    kept = {i["url"]: i for i in out["kept"]}
    assert kept[fresh]["date_source"] == "search_result"
    assert kept[fresh]["published_date"] == (TODAY - timedelta(days=3)).isoformat()
    assert kept[unknown]["published_date"] is None and kept[unknown]["date_source"] is None
    assert [i["drop_reason"] for i in out["dropped"]] == ["outside_time_window"]   # the 2019 article


def test_parse_page_age_formats():
    from app.services.news_search import parse_page_age
    today = date(2026, 11, 4)
    assert parse_page_age("2 weeks ago", today) == date(2026, 10, 21)
    assert parse_page_age("April 30, 2025", today) == date(2025, 4, 30)
    assert parse_page_age("30 Apr 2025", today) == date(2025, 4, 30)
    assert parse_page_age("2026-10-01T09:00:00Z", today) == date(2026, 10, 1)
    assert parse_page_age("sometime", today) is None


def test_time_budget_asks_for_the_report_early_and_never_overruns(monkeypatch, catalogue, db):
    """With most of the budget gone, Claude is told to report now; with none
    left, no further call starts and the editor gets a clear message."""
    from app.services import news_search
    from app.services.search_profiles import effective_from_fields

    profile = effective_from_fields(db, catalogue["family"], ProfileFields(terms=["fiberglass"]))
    ctx = news_search.BriefContext(family_name="Fiberglass", line_name=None, producers=[], regions=[])
    # start, before call 1 (t=0), before call 2 (t=50: 25s of 75 left, under 40%), then t=200.
    clock = iter([0.0, 0.0, 50.0, 200.0])
    monkeypatch.setattr(news_search.time, "monotonic", lambda: next(clock))
    turn = ([{"type": "text", "text": "searching"}], "pause_turn", {"input_tokens": 1})
    fake = FakeClaude(turn, turn)
    monkeypatch.setattr(claude, "create_message", fake)
    out = news_search.search_news(profile, ctx, max_searches=5, time_budget=75)

    first, second = fake.calls
    assert first["timeout"] == 75 and first["tool_choice"] is None          # normal search
    assert second["timeout"] == 25                                           # never past the budget
    assert second["tool_choice"] == {"type": "tool", "name": "report_news"}  # "report what you have"
    assert "ran out of time" in out.error                                    # no third call at t=200


def test_time_budget_exhausted_before_start(monkeypatch, catalogue, db):
    from app.services import news_search
    from app.services.search_profiles import effective_from_fields
    profile = effective_from_fields(db, catalogue["family"], ProfileFields(terms=["fiberglass"]))
    ctx = news_search.BriefContext(family_name="F", line_name=None, producers=[], regions=[])
    fake = FakeClaude()
    monkeypatch.setattr(claude, "create_message", fake)
    out = news_search.search_news(profile, ctx, max_searches=5, time_budget=5)
    assert fake.calls == [] and "ran out of time" in out.error


def test_test_runs_are_rate_limited(editor, catalogue, fake_claude):
    from app.routers.news import TEST_LIMIT
    limit = int(TEST_LIMIT.split("/")[0])
    fid = catalogue["family"]
    editor.put(f"/api/news/profiles/{fid}", json=_form(catalogue))
    fake_claude(*[([_report([])], "tool_use", {"input_tokens": 1, "output_tokens": 1})] * (limit + 1))
    codes = [editor.post(f"/api/news/profiles/{fid}/test", json={}).status_code for _ in range(limit + 1)]
    assert codes[:limit] == [200] * limit and codes[-1] == 429


def test_list_warnings_and_editor_picker(editor, catalogue):
    fid = catalogue["family"]
    rows = {r["family_id"]: r for r in editor.get("/api/news/profiles").json()}
    assert rows[fid]["warnings"] == ["No profile yet"]
    editor.put(f"/api/news/profiles/{fid}",
               json=_form(catalogue, producer_ids=[], exclude_terms=[], is_active=True))
    rows = {r["family_id"]: r for r in editor.get("/api/news/profiles").json()}
    assert set(rows[fid]["warnings"]) == {"No producers to watch", "No exclusions yet (expect noise)",
                                          "Active but nobody owns it"}
    me = editor.get("/auth/me").json()["id"]
    assert me in [e["id"] for e in editor.get("/api/news/editors").json()]
    r = editor.put(f"/api/news/profiles/{fid}", json=_form(catalogue, is_active=True, owner_editor_id=me))
    assert r.json()["profile"]["owner_editor_id"] == me


def test_draft_uses_product_lines_and_reports(editor, catalogue, fake_claude, db):
    from app.models.market_report import MarketReport, MarketReportLine, MarketReportSection
    slug = f"fg-{uuid.uuid4().hex[:6]}"
    db.add(MarketReport(slug=slug, report_file="x.html", name="Glass fibre"))
    db.flush()
    db.add_all([MarketReportLine(slug=slug, line_key=f"k-{slug}", product_line_id=catalogue["line"], source="report_map"),
                MarketReportSection(slug=slug, section_id="supply", ordinal=6,
                                    html="<p>Leading makers: <b>Jushi</b> &amp; Owens Corning.</p>")])
    db.commit()
    try:
        fake = fake_claude(_draft_response(catalogue))
        assert editor.post(f"/api/news/profiles/{catalogue['family']}/draft").status_code == 200
        prompt = fake.calls[0]["messages"][0]["content"]
        assert "Product lines: E-glass roving" in prompt
        assert "Leading makers: Jushi & Owens Corning." in prompt      # HTML stripped
    finally:
        db.execute(text("DELETE FROM market_reports WHERE slug = :s"), {"s": slug})
        db.commit()


# ── The preview flag: visible only to the people testing it ──────────────────

def test_flag_off_hides_everything(editor, catalogue, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "news_preview_emails", "")
    assert editor.get("/api/news/enabled").json() == {"enabled": False}
    assert editor.get("/api/news/profiles").status_code == 404          # as if it did not exist
    assert editor.put(f"/api/news/profiles/{catalogue['family']}", json=_form(catalogue)).status_code == 404


def test_flag_lists_who_can_see_it(user_factory, client_as, catalogue, monkeypatch, db):
    from app.config import get_settings
    from app.models.user import User
    tester, colleague = user_factory(is_super_admin=True), user_factory(is_super_admin=True)
    email = db.get(User, tester["user_id"]).email
    monkeypatch.setattr(get_settings(), "news_preview_emails", f" {email.upper()} , someone@else.example")
    me, them = client_as(tester), client_as(colleague)
    assert me.get("/api/news/enabled").json() == {"enabled": True}       # case and spaces don't matter
    assert me.get("/api/news/profiles").status_code == 200
    assert them.get("/api/news/enabled").json() == {"enabled": False}    # same role, not on the list
    assert them.get("/api/news/profiles").status_code == 404


def test_on_the_list_still_needs_the_editor_role(user_factory, client_as, monkeypatch, db):
    from app.config import get_settings
    from app.models.user import User
    plain = user_factory()
    monkeypatch.setattr(get_settings(), "news_preview_emails", db.get(User, plain["user_id"]).email)
    c = client_as(plain)
    assert c.get("/api/news/enabled").json() == {"enabled": False}
    assert c.get("/api/news/profiles").status_code == 403


# ── Claude reports candidates, not verdicts (real-search tuning fix) ─────────

def test_instructions_ask_for_every_candidate_with_an_honest_score():
    from app.services import news_search
    prompt = news_search.SYSTEM_PROMPT
    assert "Report every item that is plausibly relevant" in prompt
    assert "Relevance scale" in prompt and "0.85-1.0" in prompt
    assert "report an item even when its page shows no date" in prompt
    assert news_search.REPORT_TOOL_DEF["input_schema"]["required"] == ["items", "notes"]


def test_test_run_explains_itself(editor, catalogue, fake_claude):
    """Pages looked at and Claude's note come back, so an empty result has a reason."""
    fid = catalogue["family"]
    editor.put(f"/api/news/profiles/{fid}", json=_form(catalogue))
    fake = fake_claude(([*_search_turn(["https://a.example/1", "https://b.example/2", "https://a.example/1/"]),
                         {"type": "tool_use", "id": "r1", "name": "report_news",
                          "input": {"items": [], "notes": "Results were mostly from 2025."}}],
                        "tool_use", {"input_tokens": 10, "output_tokens": 5}))
    out = editor.post(f"/api/news/profiles/{fid}/test", json={}).json()
    assert out["pages_seen"] == 2                       # the same page twice counts once
    assert out["claude_note"] == "Results were mostly from 2025."
    brief = fake.calls[0]["messages"][0]["content"]
    assert "including items whose date you cannot see" in brief


def test_without_a_note_claudes_last_words_explain(editor, catalogue, fake_claude):
    editor.put(f"/api/news/profiles/{catalogue['family']}", json=_form(catalogue))
    fake_claude(
        ([{"type": "text", "text": "Nothing recent turned up for these terms."}], "end_turn",
         {"input_tokens": 10, "output_tokens": 5}),
        ([_report([])], "tool_use", {"input_tokens": 10, "output_tokens": 5}),
    )
    out = editor.post(f"/api/news/profiles/{catalogue['family']}/test", json={}).json()
    assert out["claude_note"] == "Nothing recent turned up for these terms."
