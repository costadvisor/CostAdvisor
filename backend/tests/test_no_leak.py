"""No licensed or internal content leaves the API (design §4.1, §6 WP-1b, TRIM T2).

What is not stored cannot leak, and what is stored for us only must never be
served. This walks the responses of the endpoints that serve catalogue
content, as a plain team **member** (not a super-admin), for a few cards picked
from the drop at run time, and fails on:

* a key that carries a quote, a source or audit wording: `maker_quote`,
  `maker_source`, `count_why`, `role_changed`, `resolve_note`, `weak_note`,
  `weights_rationale`, `line_moved`, `internal_meta`, `archival_note`;
* any key starting with `_` (the drop's private fields);
* a non-null `share_pct`, or a `share` above 0 (shares are unsourced);
* any quote text of the chosen cards (`maker_quote`, `sites[].quote`), or
  their `_archival` text;
* the name of a deliberately unnamed product line (frozen instruction: such a
  line reads "Product line not yet published", never its old name).

The cards: a listed LIVE card whose supplier rows carry quotes, `count_why`,
`role_changed` and a share above 0 (`content_drop_expect.leak_probe_card`),
and a pointer PID, whose page serves its target. Every expectation comes from
the drop files; no drop text is written here.

The endpoints (one test id each, so each owning package runs its own with
`-k`): `intel_product`, `intel_market`, `intel_pointer` (WP-6);
`intel_suppliers`, `intel_supplier_detail` (WP-7); `editorial_card`,
`formulas_list`, `formulas_by_id` (WP-8); `intelligence_combo` (WP-8/WP-12).
`formulas_by_id` walks `GET /api/formulas/{id}` for the card and for the
pointer's own template (a by-id read answers for cards that are not listed),
then `/coverage`, `/components` and `/resolve` for each of the card's regions.

Needs a database holding a content load of the current drop
(`content_loaded`); it fails, never skips, without one.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.team import Team, TeamMembership
from tests import content_drop_expect as expect

FORBIDDEN_KEYS = frozenset({
    "maker_quote", "maker_source", "count_why", "role_changed", "resolve_note", "weak_note",
    "weights_rationale", "line_moved", "internal_meta", "archival_note",
})
# Quotes shorter than this are not searched for: a short quote ("Yes.") would
# match ordinary text.
MIN_QUOTE_LEN = 20

ENDPOINTS = (
    "intel_product", "intel_market", "intel_pointer",
    "intel_suppliers", "intel_supplier_detail",
    "editorial_card", "formulas_list", "formulas_by_id", "intelligence_combo",
)


# ── The walk ─────────────────────────────────────────────────────────────────

def _walk(value, path: str, texts: list[str], problems: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}"
            name = str(key)
            if name in FORBIDDEN_KEYS:
                problems.append(f"{here}: forbidden key")
            elif name.startswith("_"):
                problems.append(f"{here}: private key")
            if name == "share_pct" and item is not None:
                problems.append(f"{here}: share_pct is {item!r}")
            if (name == "share" and isinstance(item, (int, float)) and not isinstance(item, bool)
                    and item > 0):
                problems.append(f"{here}: share is {item!r}")
            _walk(item, here, texts, problems)
    elif isinstance(value, list):
        for pos, item in enumerate(value):
            _walk(item, f"{path}[{pos}]", texts, problems)
    elif isinstance(value, str):
        # The text itself is not echoed: it is licensed or internal.
        for i, t in enumerate(texts):
            if t in value:
                problems.append(f"{path}: contains forbidden text #{i} ({len(t)} characters)")


def leaks(payload, texts: list[str]) -> list[str]:
    problems: list[str] = []
    _walk(payload, "$", texts, problems)
    return problems


def test_the_walk_catches_what_it_should():
    """The checker itself, on a made-up payload (no database needed)."""
    payload = {"ok": [{"name": "fine", "share_pct": None, "share": 0}],
               "maker_quote": "x", "_private": 1, "rows": [{"share": 3, "share_pct": 1.5}],
               "nested": {"text": "a sentence holding SECRET TEXT here"}}
    found = leaks(payload, ["SECRET TEXT"])
    assert any("$.maker_quote" in p for p in found)
    assert any("$._private" in p for p in found)
    assert any("rows[0].share:" in p for p in found)
    assert any("rows[0].share_pct" in p for p in found)
    assert any("nested.text" in p for p in found)
    assert not any("ok[0]" in p for p in found)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def probe(content_loaded):
    """The chosen cards and the texts that must not appear, from the drop,
    plus their template ids and regions from the database."""
    from app.database import SessionLocal, bypass_rls_var

    card = expect.leak_probe_card()
    pointer = expect.pointer_pids()[0]
    texts = set(expect.unpublished_line_names())
    for pid in (card, pointer):
        texts |= {q for q in expect.card_quotes(pid) if len(q) >= MIN_QUOTE_LEN}
        archival = (expect.raw("CURATED_CONTENT").get(pid) or {}).get("_archival")
        if isinstance(archival, str) and len(archival.strip()) >= MIN_QUOTE_LEN:
            texts.add(archival.strip())

    prior = bypass_rls_var.get()
    bypass_rls_var.set(True)
    s = SessionLocal()
    try:
        tid = s.execute(text("SELECT id FROM formula_templates WHERE team_id IS NULL AND code = :c"),
                        {"c": card}).scalar()
        pointer_tid = s.execute(text(
            "SELECT id FROM formula_templates WHERE team_id IS NULL AND code = :c"),
            {"c": pointer}).scalar()
        regions = [r for (r,) in s.execute(text(
            "SELECT DISTINCT region FROM formula_region_coverage WHERE template_id = :t "
            "AND withdrawn_at IS NULL ORDER BY region"), {"t": tid})] if tid else []
        producers = [p for (p,) in s.execute(text(
            "SELECT DISTINCT producer_id FROM producer_formulas WHERE subject_code = :c"),
            {"c": card})]
        s.rollback()
    finally:
        s.close()
        bypass_rls_var.set(prior)
    assert tid is not None, f"the loaded database has no platform template {card}"
    assert pointer_tid is not None, f"the loaded database has no platform template {pointer}"
    assert regions, f"{card} has no coverage row"
    return {"card": card, "pointer": pointer, "template_id": tid,
            "pointer_template_id": pointer_tid, "regions": regions,
            "producers": producers, "texts": sorted(texts, key=len, reverse=True)}


@pytest.fixture
def member(db, user_factory, client_as):
    """A plain member: of the demo team when the database has one, else of a
    fresh team. Returns (client, team_id)."""
    from app.services.demo_buyer import TEAM_NAME

    user = user_factory()
    demo = db.query(Team).filter(Team.name == TEAM_NAME).order_by(Team.created_at).first()
    team_id = demo.id if demo else None
    if team_id is None:
        team_id = user_factory()["team_id"]
    db.add(TeamMembership(user_id=user["user_id"], team_id=team_id, role="member"))
    db.commit()
    yield client_as(user), team_id
    db.query(TeamMembership).filter(TeamMembership.user_id == user["user_id"],
                                    TeamMembership.team_id == team_id).delete()
    db.commit()


# ── The endpoints ────────────────────────────────────────────────────────────

def _get(client, url: str, **params):
    r = client.get(url, params=params)
    assert r.status_code == 200, f"GET {url} {params} → {r.status_code}: {r.text[:300]}"
    return r.json()


def _payloads(name: str, client, team_id: uuid.UUID, p: dict) -> list[tuple[str, object]]:
    card, tid = p["card"], p["template_id"]
    team = {"team_id": str(team_id)}
    out: list[tuple[str, object]] = []
    if name == "intel_product":
        out.append((f"/api/intel/products/{card}", _get(client, f"/api/intel/products/{card}")))
    elif name == "intel_market":
        url = f"/api/intel/products/{card}/market"
        out.append((url, _get(client, url)))
    elif name == "intel_pointer":
        url = f"/api/intel/products/{p['pointer']}"
        out.append((url, _get(client, url)))
    elif name == "intel_suppliers":
        offset, total = 0, None
        while total is None or offset < total:
            page = _get(client, "/api/intel/suppliers", limit=1000, offset=offset)
            out.append((f"/api/intel/suppliers?offset={offset}", page))
            total = page.get("total", 0)
            if not page.get("items"):
                break
            offset += len(page["items"])
    elif name == "intel_supplier_detail":
        assert p["producers"], f"{card} has no producer link in the loaded database"
        for pid in p["producers"]:
            url = f"/api/intel/suppliers/{pid}"
            out.append((url, _get(client, url)))
    elif name == "editorial_card":
        url = f"/api/editorial/cards/formula/{card}"
        out.append((url, _get(client, url, **team)))
    elif name == "formulas_list":
        out.append(("/api/formulas/", _get(client, "/api/formulas/", **team)))
    elif name == "formulas_by_id":
        for by_id in (tid, p["pointer_template_id"]):
            out.append((f"/api/formulas/{by_id}", _get(client, f"/api/formulas/{by_id}", **team)))
        out.append((f"/api/formulas/{tid}/coverage",
                    _get(client, f"/api/formulas/{tid}/coverage", **team)))
        for region in p["regions"]:
            for sub in ("components", "resolve"):
                url = f"/api/formulas/{tid}/{sub}"
                out.append((f"{url}?region={region}", _get(client, url, region=region, **team)))
    elif name == "intelligence_combo":
        for region in p["regions"]:
            url = f"/api/intelligence/combos/{tid}/{region}"
            out.append((url, _get(client, url, **team)))
    else:  # pragma: no cover
        raise AssertionError(name)
    return out


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_no_leak(endpoint, probe, member):
    client, team_id = member
    problems = []
    for url, payload in _payloads(endpoint, client, team_id, probe):
        problems += [f"{url}: {line}" for line in leaks(payload, probe["texts"])]
    assert not problems, f"{len(problems)} leak(s):\n" + "\n".join(problems[:40])
