"""The live-number picker API (Jira 210 S7).

* signed-in editors only: `content.edit` on the team (401 signed out, 403 for
  a team you cannot edit);
* series search by name or key, with each series' key and health;
* a platform recipe's lines, for a weight token; unknown recipe 404;
* preview: what a token shows now, its state and record — or why it cannot
  be read.

Series and recipes are made up (resolver tests' fixtures).
"""
from __future__ import annotations

import uuid

from app.models.formula_template import FormulaTemplate, FormulaTemplateComponent
from tests.test_live_resolver import _series, made  # noqa: F401 — fixture


def _client(client_as, tenant):
    return client_as(tenant), {"team_id": str(tenant["team_id"])}


def test_the_picker_is_for_editors_of_the_team(client, client_as, tenant_a, tenant_b):
    assert client.get("/api/live/series", params={"team_id": str(tenant_a["team_id"])}).status_code == 401
    c = client_as(tenant_a)
    other = {"team_id": str(tenant_b["team_id"])}
    assert c.get("/api/live/series", params=other).status_code == 403
    assert c.post("/api/live/preview", params=other, json={"token": "{{index:x|last}}"}).status_code == 403


def test_series_search_finds_by_name_and_returns_the_key(db, made, client_as, tenant_a):
    s = _series(db, made, [(2026, 6, 100, "actual")])
    c, team = _client(client_as, tenant_a)
    r = c.get("/api/live/series", params={**team, "q": s.name})
    assert r.status_code == 200, r.text
    [hit] = r.json()
    assert hit["key"] == s.commodity_key and hit["last_actual"] == "2026-06"
    assert c.get("/api/live/series", params={**team, "q": s.commodity_key}).json()[0]["name"] == s.name


def test_recipe_lines_for_a_weight_token(db, tenant_a, made, client_as):
    t = FormulaTemplate(name=f"Picker {uuid.uuid4().hex[:6]}", code=f"TST-{uuid.uuid4().hex[:6]}",
                        team_id=None, created_by=tenant_a["user_id"])
    db.add(t)
    db.flush()
    db.add(FormulaTemplateComponent(template_id=t.id, name="Sulfur feed", component_type="fixed",
                                    region="Europe", weight_pct=22))
    db.commit()
    made["templates"].append(t)
    c, team = _client(client_as, tenant_a)
    r = c.get("/api/live/recipe-lines", params={**team, "code": t.code})
    assert r.status_code == 200, r.text
    assert r.json() == [{"region": "Europe", "variant": "", "name": "Sulfur feed", "weight_pct": 22.0}]
    assert c.get("/api/live/recipe-lines", params={**team, "code": "NO-SUCH"}).status_code == 404


def test_preview_shows_what_readers_will_see(db, made, client_as, tenant_a):
    s = _series(db, made, [(2025, 6, 100, "actual"), (2026, 6, 125, "actual")])
    c, team = _client(client_as, tenant_a)
    token = f"{{{{index:{s.commodity_key}|change12m}}}}"
    body = c.post("/api/live/preview", params=team, json={"token": token}).json()
    assert body["valid"] and body["display"].startswith("+25.0% · Jun 2026")
    assert body["derived_from"]["series"] == [s.commodity_key]

    bad = c.post("/api/live/preview", params=team,
                 json={"token": "{{index:Bad|last|fallback=about 130}}"}).json()
    assert not bad["valid"] and "bad series key" in bad["error"]
    assert bad["display"] == "about 130" and bad["state"] == "missing"
