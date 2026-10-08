"""Card kind and supply status (app/services/content_drop/status.py).

Two kinds of check, neither needs a database:

* against the drop: the loader's decision table agrees, card by card, with the
  independent reading in `tests/content_drop_expect.py` (kinds, statuses,
  status detail, redirect targets, which PIDs get a template);
* against made-up cards: each rule of the table, and its order, on PIDs and
  texts invented here (no drop text in this file).
"""
from __future__ import annotations

import pytest

from app.services.content_drop import reader, status
from tests import content_drop_expect as expect


@pytest.fixture(scope="module", autouse=True)
def _drop():
    # Fails loudly (never skips) when the drop is missing.
    reader.drop_dir()
    expect.drop_dir()


# ── Against the drop ─────────────────────────────────────────────────────────

def test_every_template_code_is_a_card_the_table_loads():
    assert sorted(status.card_codes()) == expect.template_codes()
    assert len(status.card_codes()) == len(set(status.card_codes()))
    assert status.shells() == expect.shells()
    for pid in status.shells():
        assert status.classify(pid) is None


def test_the_decision_table_agrees_with_the_expectation_card_by_card():
    want = expect.card_statuses()
    differ = []
    for pid in status.card_codes():
        got = status.classify(pid)
        exp = want[pid]
        if (got.kind, got.supply_status, got.detail, got.redirect_to) != \
                (exp.kind, exp.supply_status, exp.detail, exp.redirect_to):
            differ.append(pid)
    assert differ == []


def test_redirects_and_merges():
    assert status.redirects() == expect.redirects()
    codes = set(status.card_codes())
    for pid, target in status.redirects().items():
        assert target and target in codes, pid
    assert status.merged_from() == expect.merged_from()


def test_kind_and_status_are_separate_facts():
    for pid in status.card_codes():
        s = status.classify(pid)
        if s.kind in ("product", "group"):
            assert s.supply_status in ("live", "supply_exception", "supply_pending",
                                       "not_audited"), pid
        else:
            assert s.supply_status is None and s.detail is None, pid
        if s.kind == "group":
            assert s.supply_status == "not_audited", pid
        if s.detail is not None:
            assert set(s.detail) == {"source", "value", "date"}, pid


def test_absorbed_is_decided_by_the_set_not_by_the_text():
    """Some maintained cards still carry stale "absorbed" wording; only the
    ABSORBED_FORMULA_IDS set decides."""
    absorbed = set(reader.raw("ABSORBED_FORMULA_IDS"))
    for pid, card in reader.raw("CURATED_CONTENT").items():
        archival = card.get("_archival")
        if not isinstance(archival, str) or status.is_shell(pid):
            continue
        if archival.startswith("ABSORBED") and pid not in absorbed:
            assert status.classify(pid).kind in ("product", "group"), pid
        if pid in absorbed and not archival.startswith(("POINTER", "DUPLICATE")):
            assert status.classify(pid).kind == "absorbed", pid


# ── Against made-up cards ────────────────────────────────────────────────────

FAKE = {
    "CURATED_CONTENT": {
        "ZZA-PTR-LIQ": {"_archival": "POINTER to ZZA-TGT-LIQ, merged by a made-up ruling",
                        "maker_audit": {"outcome": "LIVE", "date": "2001-01-01"}},
        "ZZA-DUP-LIQ": {"_archival": "DUPLICATE: this product is already priced as "
                                     "ZZA-TWIN-PWD in a made-up note"},
        "ZZA-DUPX-LIQ": {"_archival": "DUPLICATE with no twin named"},
        "ZZA-ABS-LIQ": {"maker_audit": {"outcome": "LIVE"}},
        "ZZA-STALE-LIQ": {"_archival": "ABSORBED: stale made-up text",
                          "maker_audit": {"outcome": "LIVE", "date": "2001-02-03",
                                          "batch": 7, "why": "x", "by": "someone"}},
        "GRP-ZZA-ONE": {},
        "GRP-ZZA-SHELL": {},
        "ZZA-EXC-LIQ": {"supply_exception": {"status": "SUPPLY_EXCEPTION"},
                        "supply_pending": {"status": "SUPPLY_PENDING"},
                        "maker_audit": {"outcome": "LIVE"}},
        "ZZA-PEND-LIQ": {"supply_pending": {"status": "GRADE_PENDING", "date": "2001-03-04"},
                         "maker_audit": {"outcome": "LIVE"}},
        "ZZA-ODD-LIQ": {"supply_pending": {"status": "SOMETHING_ELSE"}},
        "ZZA-NONE-LIQ": {},
        "ZZA-MRG-LIQ": {"merged_from": [{"pid": "ZZA-OLD-LIQ", "date": "2001-01-01"}]},
    },
    "FORMULA_COMBOS": {"ZZA-REC-LIQ": {"name": "made up", "combos": []}},
    "AUTO_GROUPS": {"GRP-ZZA-ONE": {"name": "made-up group", "combos": []}},
    "ABSORBED_FORMULA_IDS": ["ZZA-ABS-LIQ"],
}


@pytest.fixture
def fake_drop(monkeypatch):
    monkeypatch.setattr(status, "raw", lambda name: FAKE[name])


def test_each_rule_on_made_up_cards(fake_drop):
    c = status.classify
    assert c("ZZA-PTR-LIQ") == status.CardStatus("pointer", None, None, "ZZA-TGT-LIQ")
    assert c("ZZA-DUP-LIQ") == status.CardStatus("duplicate", None, None, "ZZA-TWIN-PWD")
    assert c("ZZA-DUPX-LIQ") == status.CardStatus("duplicate", None, None, None)
    assert c("ZZA-ABS-LIQ").kind == "absorbed"          # the set, despite a LIVE audit
    assert c("GRP-ZZA-ONE") == status.CardStatus("group", "not_audited", None, None)
    assert c("GRP-ZZA-SHELL") is None
    # The exception outranks pending and LIVE; pending outranks LIVE.
    assert c("ZZA-EXC-LIQ") == status.CardStatus(
        "product", "supply_exception",
        {"source": "supply_exception", "value": "SUPPLY_EXCEPTION", "date": None}, None)
    assert c("ZZA-PEND-LIQ") == status.CardStatus(
        "product", "supply_pending",
        {"source": "supply_pending", "value": "GRADE_PENDING", "date": "2001-03-04"}, None)
    # Stale "absorbed" text does not make a card absorbed; no audit field leaks
    # into the detail.
    assert c("ZZA-STALE-LIQ") == status.CardStatus(
        "product", "live", {"source": "maker_audit", "value": "LIVE", "date": "2001-02-03"},
        None)
    assert c("ZZA-ODD-LIQ") == status.CardStatus("product", "not_audited", None, None)
    assert c("ZZA-NONE-LIQ") == status.CardStatus("product", "not_audited", None, None)


def test_card_codes_and_merges_on_made_up_cards(fake_drop):
    codes = status.card_codes()
    assert codes[0] == "ZZA-REC-LIQ" and codes[1] == "GRP-ZZA-ONE"
    assert "GRP-ZZA-SHELL" not in codes
    assert set(codes) == (set(FAKE["CURATED_CONTENT"]) | {"ZZA-REC-LIQ"}) - {"GRP-ZZA-SHELL"}
    assert status.shells() == ["GRP-ZZA-SHELL"]
    assert status.redirects() == {"ZZA-PTR-LIQ": "ZZA-TGT-LIQ", "ZZA-DUP-LIQ": "ZZA-TWIN-PWD",
                                  "ZZA-DUPX-LIQ": None}
    assert status.merged_from() == {"ZZA-MRG-LIQ": ["ZZA-OLD-LIQ"]}
