"""What the team-side catalogue APIs expose (design §2.3, §4.2).

Every list over platform templates applies one visibility rule
(`services/catalog_visibility.py`): a card is **listed** when its kind is
`product` or `group` and it has a combo the source has not withdrawn. A
pointer, duplicate, absorbed or withdrawn card, or a card with no formula, is
in no list; a by-id read still opens it, so a team's existing link keeps
working. A team's own templates are always listed for that team.

The lists checked here: `GET /api/formulas/`, the platform grain of
`GET /api/dimensions/query`, and the coverage-price sheet export (its own
file covers the filters). `FormulaTemplateOut.catalog_meta` keeps the four
keys team screens read, on the list and on every by-id read.

Expectations come from the drop at run time (`content_drop_expect`); the
hand-made rows use made-up names.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.dimension import KIND_COMPLIANCE_FLAG
from app.models.formula_template import FormulaRegionCoverage, FormulaTemplate
from app.schemas.formula_template import SERVED_CATALOG_META_KEYS
from app.services.dimensions import assert_term, upsert_term
from tests import content_drop_expect as expect

UNLISTED_KINDS = ("pointer", "duplicate", "absorbed")


def _list(client, tenant) -> list[dict]:
    r = client.get("/api/formulas/", params={"team_id": str(tenant["team_id"])})
    assert r.status_code == 200, r.text
    return r.json()


def _platform_id(db, code: str) -> uuid.UUID:
    tid = db.query(FormulaTemplate.id).filter(
        FormulaTemplate.code == code, FormulaTemplate.team_id.is_(None)).scalar()
    assert tid is not None, f"the loaded database has no platform template {code}"
    return tid


@pytest.fixture
def made_up_cards(db, tenant_a):
    """Platform templates with made-up codes: a withdrawn card that still has
    a live combo, a product whose only combo is withdrawn, and a product with a
    live combo (the control)."""
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)

    def card(name: str, kind: str, withdrawn_at) -> FormulaTemplate:
        t = FormulaTemplate(team_id=None, created_by=tenant_a["user_id"], name=f"{name}-{suffix}",
                            code=f"ZZ-TEST-{name.upper()}-{suffix}", card_kind=kind)
        db.add(t)
        db.flush()
        db.add(FormulaRegionCoverage(template_id=t.id, region="Europe", base_price=1,
                                     currency="EUR", withdrawn_at=withdrawn_at))
        return t

    cards = {"withdrawn": card("withdrawn", "withdrawn", None),
             "no_live_combo": card("nolive", "product", now),
             "control": card("control", "product", None)}
    db.commit()
    yield cards
    db.rollback()
    bypass_rls_var.set(True)
    for t in cards.values():
        db.execute(text("DELETE FROM formula_templates WHERE id = :i"), {"i": str(t.id)})
    db.commit()


# ── GET /api/formulas/ ───────────────────────────────────────────────────────

def test_the_list_holds_exactly_the_listed_cards(content_loaded, client_as, tenant_a):
    platform = [r for r in _list(client_as(tenant_a), tenant_a) if r["team_id"] is None]
    codes = [r["code"] for r in platform]
    assert len(codes) == len(set(codes))
    assert set(codes) == set(expect.listed_codes())
    assert {r["card_kind"] for r in platform} <= {"product", "group"}


def test_every_unlisted_kind_still_opens_by_id(db, content_loaded, client_as, tenant_a):
    statuses = expect.card_statuses()
    picks = {}
    for pid in sorted(statuses):
        kind = statuses[pid].kind
        if kind in UNLISTED_KINDS and kind not in picks:
            picks[kind] = pid
    # A product card with no formula is not listed either.
    picks["product without a combo"] = next(
        pid for pid in sorted(expect.template_codes())
        if statuses[pid].kind == "product" and not expect.is_listed(pid))
    assert set(UNLISTED_KINDS) <= set(picks)

    c = client_as(tenant_a)
    team = {"team_id": str(tenant_a["team_id"])}
    listed = {r["code"] for r in _list(c, tenant_a)}
    for label, pid in picks.items():
        assert pid not in listed, label
        tid = _platform_id(db, pid)
        r = c.get(f"/api/formulas/{tid}", params=team)
        assert r.status_code == 200, (label, r.text)
        body = r.json()
        assert body["code"] == pid
        assert body["card_kind"] == statuses[pid].kind
        assert body["redirect_to"] == statuses[pid].redirect_to
        assert c.get(f"/api/formulas/{tid}/coverage", params=team).status_code == 200


def test_a_withdrawn_card_or_combo_leaves_the_list(made_up_cards, client_as, tenant_a):
    c = client_as(tenant_a)
    codes = {r["code"] for r in _list(c, tenant_a)}
    assert made_up_cards["control"].code in codes
    assert made_up_cards["withdrawn"].code not in codes
    assert made_up_cards["no_live_combo"].code not in codes
    r = c.get(f"/api/formulas/{made_up_cards['withdrawn'].id}",
              params={"team_id": str(tenant_a["team_id"])})
    assert r.status_code == 200 and r.json()["card_kind"] == "withdrawn"


def test_a_teams_own_template_is_listed_for_that_team_only(db, client_as, tenant_a, tenant_b):
    """A team's hand-built formula has no combo and no card kind of its own
    choosing; it is always listed for its team, and never for another."""
    bypass_rls_var.set(True)
    own = FormulaTemplate(team_id=tenant_a["team_id"], created_by=tenant_a["user_id"],
                          name=f"own-{uuid.uuid4().hex[:8]}")
    db.add(own)
    db.commit()
    assert str(own.id) in {r["id"] for r in _list(client_as(tenant_a), tenant_a)}
    assert str(own.id) not in {r["id"] for r in _list(client_as(tenant_b), tenant_b)}


def test_catalog_meta_is_narrowed_on_the_list_and_by_id(db, content_loaded, client_as, tenant_a):
    c = client_as(tenant_a)
    rows = [r for r in _list(c, tenant_a) if r["team_id"] is None]
    assert all(set(r["catalog_meta"] or {}) <= set(SERVED_CATALOG_META_KEYS) for r in rows)
    # The validator narrows; it does not empty the field.
    assert any("regions" in (r["catalog_meta"] or {}) for r in rows)
    # A by-id read of every kind, a group included.
    statuses = expect.card_statuses()
    for kind in ("group", "pointer", "absorbed"):
        pid = next(p for p in sorted(statuses) if statuses[p].kind == kind)
        body = c.get(f"/api/formulas/{_platform_id(db, pid)}",
                     params={"team_id": str(tenant_a["team_id"])}).json()
        assert set(body["catalog_meta"] or {}) <= set(SERVED_CATALOG_META_KEYS), kind
        assert "internal_meta" not in body and "archival_note" not in body


def test_the_list_carries_each_cards_place_in_the_tree(content_loaded, client_as, tenant_a):
    lines = expect.product_lines()
    rows = {r["code"]: r for r in _list(client_as(tenant_a), tenant_a) if r["team_id"] is None}
    with_line = [pid for pid in sorted(rows) if expect.template_line(pid)]
    without_line = [pid for pid in sorted(rows) if not expect.template_line(pid)]
    assert with_line
    for pid in with_line:
        line = lines[expect.template_line(pid)]
        row = rows[pid]
        assert row["product_line"]["name"] == line.name, pid
        assert row["family"]["name"] == line.family, pid
        assert (row["subfamily"] or {}).get("name") == line.subfamily, pid
    for pid in without_line:
        assert rows[pid]["product_line"] is None, pid


# ── The platform grain of the faceted query ─────────────────────────────────

def test_the_platform_facet_returns_listed_cards_only(db, content_loaded, client_as, tenant_a):
    listed = sorted(expect.listed_codes())[0]
    pointer = expect.pointer_pids()[0]
    term = upsert_term(db, kind=KIND_COMPLIANCE_FLAG, code=f"probe-{uuid.uuid4().hex[:8]}",
                       label="Probe flag", source="test")
    db.flush()
    for pid in (listed, pointer, "NO-SUCH-CARD"):
        assert_term(db, term, subject_type="formula", subject_code=pid, source="test")
    db.commit()
    try:
        r = client_as(tenant_a).get("/api/dimensions/query", params={
            "team_id": str(tenant_a["team_id"]), "kind": KIND_COMPLIANCE_FLAG,
            "code": term.code, "grain": "platform"})
        assert r.status_code == 200, r.text
        assert [h["subject_code"] for h in r.json()["hits"]] == [listed]
    finally:
        db.rollback()
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM dimension_terms WHERE id = :i"), {"i": str(term.id)})
        db.commit()


# ── The coverage-price sheet export ─────────────────────────────────────────

def test_the_sheet_export_holds_listed_cards_only(content_loaded, client_as, user_factory):
    import io

    import openpyxl

    admin = user_factory(is_super_admin=True)
    r = client_as(admin).get("/api/sheets/formula_coverage_price/export")
    assert r.status_code == 200, r.text
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    header = [c.value for c in ws[1]]
    col = next(i for i, v in enumerate(header) if v and str(v).startswith("Formula Code"))
    codes = {row[col].value for row in ws.iter_rows(min_row=2)}
    assert codes == set(expect.listed_codes())
