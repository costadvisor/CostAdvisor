"""SCRUM-214 / NEWS-2: the monthly news sweep.

The ticket's acceptance criteria are the first three tests:
  * a manual run for one family produces items and a run log with its cost,
  * running it again creates no duplicates,
  * hitting the cap stops the run cleanly and says so in the log.

Claude is a fake (canned API responses) and feeds are served by
``httpx.MockTransport``, so nothing here touches the network or costs money.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text

from app.models.chemical_family import ChemicalFamily
from app.models.news import NewsFeed, NewsItem, NewsRun
from app.models.producer import Producer
from app.models.product_line import ProductLine
from app.models.region import Region
from app.schemas.search_profile import ProfileFields
from app.services import anthropic_client as claude
from app.services import news_sweep
from app.services.search_profiles import save_profile

TODAY = datetime.now(timezone.utc).date()
RECENT = (TODAY - timedelta(days=4)).isoformat()


# ── Fixtures and fakes ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def news_on(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "news_preview_emails", "*")
    monkeypatch.setattr(claude, "is_configured", lambda: True)


@pytest.fixture
def world(db):
    """Two families with profiles, a product line override, a producer, a region."""
    tag = uuid.uuid4().hex[:6]
    fg = ChemicalFamily(code=f"F{tag}", name=f"Fiberglass {tag}")
    pe = ChemicalFamily(code=f"P{tag}", name=f"Polyethylene {tag}")
    db.add_all([fg, pe]); db.flush()
    line = ProductLine(family_id=fg.id, platform=f"PL-{tag}", line_key=f"FG {tag}|||Roving", name="E-glass roving")
    owens = Producer(name=f"Owens Corning {tag}", normalized_name=f"owens corning {tag}")
    eu = Region(code=f"E{tag}", name="Europe")
    db.add_all([line, owens, eu]); db.flush()
    save_profile(db, fg.id, ProfileFields(terms=["fiberglass", "glass fibre"], exclude_terms=["boat"],
                                          producer_ids=[owens.id], region_codes=[eu.code]), is_active=True)
    save_profile(db, fg.id, ProfileFields(terms=["roving"]), product_line_id=line.id)
    save_profile(db, pe.id, ProfileFields(terms=["polyethylene"]), is_active=True)
    db.execute(text("UPDATE news_runs SET status = 'completed' WHERE status = 'running'"))
    # Scheduled runs take every active profile: only ours, in this test database.
    db.execute(text("UPDATE search_profiles SET is_active = false WHERE family_id NOT IN (:a, :b)"),
               {"a": fg.id, "b": pe.id})
    db.commit()
    yield dict(fg=fg.id, pe=pe.id, line=line.id, owens=owens.id, tag=tag)
    db.execute(text("DELETE FROM news_feeds WHERE name LIKE :t"), {"t": f"%{tag}%"})
    db.execute(text("DELETE FROM news_runs WHERE family_id IN (:a, :b) OR family_id IS NULL"),
               {"a": fg.id, "b": pe.id})
    db.execute(text("DELETE FROM chemical_families WHERE id IN (:a, :b)"), {"a": fg.id, "b": pe.id})
    db.execute(text("DELETE FROM producers WHERE id = :p"), {"p": str(owens.id)})
    db.execute(text("DELETE FROM regions WHERE code = :c"), {"c": eu.code})
    db.commit()


@pytest.fixture
def editor(user_factory, client_as):
    return client_as(user_factory(is_super_admin=True))


def search_reply(items, searches=2, input_tokens=5000, output_tokens=500):
    """One API response: Claude searched, then reported ``items`` (url, headline, ...)."""
    content = [{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "q"}},
               {"type": "web_search_tool_result", "tool_use_id": "s1",
                "content": [{"type": "web_search_result", "url": i["url"]} for i in items]},
               {"type": "tool_use", "id": "r1", "name": "report_news", "input": {"items": [{
                   "headline": i["headline"], "url": i["url"], "source": i.get("source", "Trade press"),
                   "summary": "Our own short summary.", "event_type": i.get("event_type", "new_plant"),
                   "relevance": i.get("relevance", 0.8), "published_date": i.get("date", RECENT)}
                   for i in items]}}]
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens,
             "server_tool_use": {"web_search_requests": searches}}
    return content, "tool_use", usage


class FakeClaude:
    """Answers each profile's search from ``replies``: a function of the brief."""

    def __init__(self, reply_for):
        self.reply_for = reply_for
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        brief = kwargs["messages"][0]["content"]
        content, stop, usage = self.reply_for(brief)
        return claude.ClaudeResponse(content=content, stop_reason=stop, model="claude-sonnet-5-5",
                                     usage=claude.usage_from_api("claude-sonnet-5-5", usage))


def fiberglass_news(brief):
    if "Polyethylene" in brief:
        return search_reply([{"url": "https://pe.example/cracker", "headline": "New ethane cracker announced"}])
    if "roving" in brief:
        return search_reply([{"url": "https://trade.example/roving", "headline": "Roving line restarts in Spain",
                              "event_type": "capacity_change"}])
    return search_reply([
        {"url": "https://trade.example/poland", "headline": "Glass-fibre maker opens 60 kt furnace in Poland"},
        {"url": "https://other.example/poland-copy", "headline": "Glass fibre maker opens 60kt furnace in Poland!"},
        {"url": "https://trade.example/minor", "headline": "Distributor updates price list", "relevance": 0.3},
        {"url": "https://boats.example/sale", "headline": "Fiberglass boat sale this weekend"},
    ])


@pytest.fixture
def fake(monkeypatch):
    def install(reply_for=fiberglass_news):
        f = FakeClaude(reply_for)
        monkeypatch.setattr(claude, "create_message", f)
        return f
    return install


@pytest.fixture
def run_inline(monkeypatch):
    """POST /api/news/runs queues a Celery task; run it right away instead."""
    from app.tasks import news as news_tasks
    monkeypatch.setattr(news_tasks.execute_news_run, "delay",
                        lambda run_id: news_tasks.execute_news_run(run_id))


# ── Acceptance criteria ──────────────────────────────────────────────────────

def test_manual_run_for_one_family_stores_items_and_a_costed_log(editor, world, fake, run_inline, db):
    fake()
    r = editor.post("/api/news/runs", json={"family_id": world["fg"]})
    assert r.status_code == 202, r.text
    run = editor.get(f"/api/news/runs/{r.json()['id']}").json()

    assert run["status"] == "completed" and run["trigger"] == "manual"
    assert run["profiles_planned"] == run["profiles_run"] == 2          # the family + its roving override
    assert run["searches"] == 4 and run["cost_usd"] > 0
    assert run["input_tokens"] == 10000 and run["output_tokens"] == 1000
    # Poland + price list (kept, below threshold) + roving; the copy is a near-duplicate; the boat is dropped.
    assert run["items_new"] == 3 and run["items_duplicate"] == 1 and run["items_dropped"] == 1
    labels = [d["label"] for d in run["details"]]
    assert labels[0].startswith("Fiberglass") and labels[1].endswith("E-glass roving")
    assert run["details"][0]["dropped"] == {"excluded_term": 1}

    items = editor.get("/api/news/items", params={"run_id": run["id"]}).json()
    by_headline = {i["headline"]: i for i in items}
    poland = by_headline["Glass-fibre maker opens 60 kt furnace in Poland"]
    assert poland["status"] == "unsorted" and poland["origin"] == "web_search"
    assert poland["summary"] == "Our own short summary." and poland["search_relevance"] == 0.8
    assert by_headline["Roving line restarts in Spain"]["product_line_name"] == "E-glass roving"
    # Only the family asked for: polyethylene was not searched.
    assert not any("polyethylene" in h.lower() for h in by_headline)


def test_running_again_creates_no_duplicates(world, fake, db):
    fake()
    first = news_sweep.run_sweep(db, trigger="manual", family_id=world["fg"])
    second = news_sweep.run_sweep(db, trigger="manual", family_id=world["fg"])
    assert first.items_new == 3
    assert second.status == "completed" and second.items_new == 0 and second.items_duplicate == 4
    assert db.query(NewsItem).filter_by(family_id=world["fg"]).count() == 3


def test_hitting_the_cap_stops_cleanly_and_says_so(world, fake, db):
    fake()
    # Each profile can cost up to 8 searches x $0.01 + $0.10 = $0.18; one profile here costs ~$0.04.
    run = news_sweep.run_sweep(db, trigger="scheduled", spend_cap=0.20)
    assert run.status == "stopped_at_cap"
    assert run.profiles_run == 1 and run.profiles_planned == 3
    assert run.cost_usd <= run.spend_cap_usd
    assert "Stopped before this profile" in run.errors[-1]["message"]
    assert "1 of 3 profiles were run" in run.errors[-1]["message"]
    # What was done before the stop is kept.
    assert db.query(NewsItem).filter_by(run_id=run.id).count() == run.items_new > 0


# ── Planning, errors, scheduling ─────────────────────────────────────────────

def test_scheduled_run_takes_active_profiles_only(world, fake, db):
    fake()
    db.execute(text("UPDATE search_profiles SET is_active = false WHERE family_id = :f AND product_line_id IS NULL"),
               {"f": world["pe"]})
    db.commit()
    labels = [t.label for t in news_sweep.plan_targets(db)]
    assert any(l.startswith("Fiberglass") for l in labels)
    assert not any(l.startswith("Polyethylene") for l in labels)
    # A manual run for one family runs it even when inactive: that's how an editor tries it.
    assert [t.label for t in news_sweep.plan_targets(db, world["pe"])][0].startswith("Polyethylene")


def test_a_failing_profile_is_logged_and_the_rest_carry_on(world, fake, db):
    def flaky(brief):
        if "Polyethylene" in brief:
            raise claude.AnthropicError("Claude API error 529: overloaded")
        return fiberglass_news(brief)

    fake(flaky)   # the fake raises for polyethylene, as the real client does on a 529
    run = news_sweep.run_sweep(db, trigger="scheduled")
    assert run.status == "completed" and run.profiles_run == 3
    assert any("overloaded" in e["message"] and e["label"].startswith("Polyethylene") for e in run.errors)
    assert run.items_new == 3


def test_only_one_run_at_a_time(editor, world, db):
    news_sweep.start_run(db, trigger="manual", family_id=world["fg"])
    db.commit()
    assert editor.post("/api/news/runs", json={"family_id": world["fg"]}).status_code == 409


def test_monthly_task_is_off_unless_switched_on(world, fake, monkeypatch, db):
    from app.config import get_settings
    from app.tasks.news import monthly_news_sweep
    fake()
    assert monthly_news_sweep() == {"skipped": "NEWS_SWEEP_ENABLED is off"}
    monkeypatch.setattr(get_settings(), "news_sweep_enabled", True)
    out = monthly_news_sweep()
    assert out["status"] == "completed" and out["new_items"] == 4      # fiberglass 3 + polyethylene 1


def test_beat_schedule_runs_monthly():
    import celeryconfig
    entry = celeryconfig.beat_schedule["news-sweep-monthly"]
    assert entry["task"] == "app.tasks.news.monthly_news_sweep"
    assert entry["schedule"].day_of_month == {1}
    assert "app.tasks.news" in celeryconfig.imports


# ── Feeds ────────────────────────────────────────────────────────────────────

def _rss(items):
    body = "".join(f"<item><title>{t}</title><link>{u}</link><pubDate>{d}</pubDate>"
                   f"<description>{desc}</description></item>" for t, u, d, desc in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>{body}</channel></rss>'


def _atom(entries):
    body = "".join(f'<entry><title>{t}</title><link rel="alternate" href="{u}"/><updated>{d}</updated>'
                   f"<summary>{s}</summary></entry>" for t, u, d, s in entries)
    return f'<feed xmlns="http://www.w3.org/2005/Atom"><title>EDGAR</title>{body}</feed>'


def test_feeds_add_free_news(world, fake, db):
    fake(lambda brief: search_reply([], searches=0))       # Claude finds nothing; feeds do the work
    tag = world["tag"]
    recent_rss = (TODAY - timedelta(days=3)).strftime("%a, %d %b %Y 09:00:00 GMT")
    old_rss = "Mon, 05 Jan 2019 09:00:00 GMT"
    long_teaser = "Capacity news. " * 40
    db.add_all([
        NewsFeed(name=f"Composites press {tag}", url=f"https://press.example/{tag}.rss", kind="rss"),
        NewsFeed(name=f"Owens filings {tag}", url=f"https://sec.example/{tag}.atom", kind="sec",
                 producer_id=world["owens"]),
        NewsFeed(name=f"Broken {tag}", url=f"https://down.example/{tag}.rss", kind="rss"),
    ])
    db.commit()

    def handler(request):
        if "press.example" in str(request.url):
            assert request.headers["user-agent"].startswith("CostAdvisor")
            return httpx.Response(200, text=_rss([
                ("New glass fibre plant in Egypt", "https://press.example/egypt", recent_rss, long_teaser),
                ("Fiberglass boat show", "https://press.example/boats", recent_rss, "boats"),
                ("Fiberglass plant closed", "https://press.example/old", old_rss, "old"),
                ("Unrelated resin news", "https://press.example/resin", recent_rss, "resins"),
            ]))
        if "sec.example" in str(request.url):
            return httpx.Response(200, text=_atom([
                ("8-K - Current report", "https://sec.example/8k-1", f"{TODAY.isoformat()}T10:00:00-04:00", "Item 2.01"),
            ]))
        return httpx.Response(503)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    run = news_sweep.run_sweep(db, trigger="manual", family_id=world["fg"], feed_client=client)

    items = {i.headline: i for i in db.query(NewsItem).filter_by(run_id=run.id).all()}
    assert set(items) == {"New glass fibre plant in Egypt", "8-K - Current report"}
    assert items["New glass fibre plant in Egypt"].origin == "feed"
    assert len(items["New glass fibre plant in Egypt"].source_excerpt) <= 280
    assert items["New glass fibre plant in Egypt"].summary is None          # never the publisher's text
    assert items["8-K - Current report"].origin == "sec"                    # matched by producer, no keyword
    assert run.feed_entries_read == 5
    assert run.details[0]["dropped"] == {"excluded_term": 1, "outside_time_window": 1}
    broken = db.query(NewsFeed).filter(NewsFeed.name == f"Broken {tag}").one()
    assert broken.last_error == "HTTP 503"
    assert any(e["label"] == f"feed: Broken {tag}" for e in run.errors)


def test_feeds_api(editor, world, db):
    tag = world["tag"]
    r = editor.post("/api/news/feeds", json={"name": f"Trade {tag}", "url": f"https://t.example/{tag}"})
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    assert editor.post("/api/news/feeds", json={"name": "dup", "url": f"https://t.example/{tag}"}).status_code == 409
    assert editor.post("/api/news/feeds", json={"name": f"SEC {tag}", "url": f"https://s.example/{tag}",
                                                "kind": "sec"}).status_code == 422          # needs a producer
    assert editor.post("/api/news/feeds", json={"name": "x", "url": "ftp://nope"}).status_code == 422
    r = editor.put(f"/api/news/feeds/{fid}", json={"name": f"Trade {tag}", "url": f"https://t.example/{tag}",
                                                   "enabled": False})
    assert r.json()["enabled"] is False
    assert editor.delete(f"/api/news/feeds/{fid}").status_code == 204


def test_sweep_is_behind_the_preview_flag(editor, world, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "news_preview_emails", "")
    assert editor.get("/api/news/runs").status_code == 404
    assert editor.post("/api/news/runs", json={}).status_code == 404
    assert editor.get("/api/news/feeds").status_code == 404


def test_headline_keys_and_near_duplicates():
    k = news_sweep.headline_key
    assert k("Glass-fibre maker opens 60 kt furnace in Poland") == "glass fibre maker opens 60 kt furnace in poland"
    assert news_sweep._near_duplicate(k("Glass fibre maker opens 60kt furnace in Poland!"),
                                      [k("Glass-fibre maker opens 60 kt furnace in Poland")])
    assert not news_sweep._near_duplicate(k("Glass-fibre maker closes furnace in Spain"),
                                          [k("Glass-fibre maker opens 60 kt furnace in Poland")])
    assert not news_sweep._near_duplicate(k("Q3 results"), [k("Q3 results")])   # too short to judge


def test_run_now_inline_where_no_worker_runs(editor, world, fake, monkeypatch, db):
    """NEWS_RUNS_INLINE: the API runs it after responding, no Celery needed."""
    from app.config import get_settings
    fake()
    monkeypatch.setattr(get_settings(), "news_runs_inline", True)
    r = editor.post("/api/news/runs", json={"family_id": world["fg"]})
    assert r.status_code == 202
    run = editor.get(f"/api/news/runs/{r.json()['id']}").json()
    assert run["status"] == "completed" and run["items_new"] == 3


def test_run_log_keeps_pages_and_notes_per_profile(world, monkeypatch, db):
    def noted(brief):
        content, stop, usage = fiberglass_news(brief)
        content[-1]["input"]["notes"] = "Mostly trade press from this month."
        return content, stop, usage
    monkeypatch.setattr(claude, "create_message", FakeClaude(noted))
    run = news_sweep.run_sweep(db, trigger="manual", family_id=world["fg"])
    first = run.details[0]
    assert first["note"] == "Mostly trade press from this month." and first["pages_seen"] == 4
