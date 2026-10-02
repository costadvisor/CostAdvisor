"""Guided negotiation prep (Scrum 29).

The claim checker is tested as a pure function over hand-built drivers, so the
verdict rules are pinned without standing up a priced cost model for each one.
The API flow uses the brief fixture the brief tests already build.

What these are really asserting: the app never invents the supplier's side, and
no verdict ever adds to the target. The should-cost has already consumed every
verified index movement, so a driver the supplier cites cannot justify anything
on top of it — the verdicts differ only in how a claim fails.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import bypass_rls_var
from app.schemas.costing import BriefDriver
from app.services.negotiation_prep import build_script, check_claim
# Same import style as test_should_cost_breakdown: a fixture defined in
# another test module is only usable once imported into this namespace.
from tests.test_brief import brief_model, CUR_Y, CUR_Q  # noqa: F401


class _Claim:
    def __init__(self, said, driver_label=None, claimed=None, include=True):
        self.id = uuid.uuid4()
        self.said = said
        self.driver_label = driver_label
        self.claimed_change_pct = claimed
        self.include_in_script = include


def _driver(label, index_name, change_pct, cost=100.0, direction="up"):
    return BriefDriver(
        component_label=label, index_name=index_name, index_change_pct=change_pct,
        contribution_to_gap=0.0, component_cost=cost, direction=direction,
    )


DRIVERS = [
    _driver("Butyl acrylate", "BA-EU", 11.4, cost=340.0),
    _driver("Electricity", "ELEC-EU", -4.2, cost=60.0, direction="down"),
    _driver("Labour", "LCI-EU", 3.1, cost=90.0),
    _driver("Catalyst", "CAT-EU", 0.2, cost=10.0, direction="flat"),
]


# ── verdicts ──────────────────────────────────────────────────────────────

def test_a_wildly_overstated_claim_is_called_overstated():
    c = check_claim(_Claim("Acrylate is up 30%", "BA-EU", 30.0), DRIVERS, 1000.0)
    assert c.verdict == "overstated"
    assert "11.4" in c.note and "30.0" in c.note
    # The share is what makes the rebuttal land, so it has to be quoted.
    assert c.weight_pct == 68.0


def test_a_claim_in_the_wrong_direction_is_contradicted():
    c = check_claim(_Claim("Energy has doubled", "ELEC-EU", 100.0), DRIVERS, 1000.0)
    assert c.verdict == "contradicted"
    assert "argues the price down" in c.note


def test_a_roughly_accurate_claim_is_conceded_not_disputed():
    """Arguing with a supplier who is right loses the room. A true claim is
    still not a reason to pay more, because it is already in the number."""
    c = check_claim(_Claim("Labour is up about 3%", "LCI-EU", 3.0), DRIVERS, 1000.0)
    assert c.verdict == "already_priced"
    assert "already carried in the should-cost" in c.note


def test_rounding_is_not_treated_as_overstatement():
    # Suppliers round. 12 against a real 11.4 is not a lie.
    c = check_claim(_Claim("Acrylate up 12%", "BA-EU", 12.0), DRIVERS, 1000.0)
    assert c.verdict == "already_priced"


def test_a_flat_driver_says_there_is_nothing_to_pass_on():
    c = check_claim(_Claim("Catalyst costs are rising", "CAT-EU", 8.0), DRIVERS, 1000.0)
    assert c.verdict == "no_movement"
    assert "nothing here to pass on" in c.note


def test_a_claim_about_something_that_is_not_a_cost_line():
    """The strongest rebuttal in the set, and it needs no index at all."""
    c = check_claim(_Claim("Freight pressure", "Freight", None), DRIVERS, 1000.0)
    assert c.verdict == "out_of_scope"
    assert "not one of this product's cost lines" in c.note


def test_no_driver_chosen_is_a_todo_not_a_rebuttal():
    """Deliberately distinct from out_of_scope: 'I have not said which input
    this is about' is unfinished, 'this is not an input' is an answer."""
    c = check_claim(_Claim("They mentioned something about energy"), DRIVERS, 1000.0)
    assert c.verdict == "unmapped"
    assert c.actual_change_pct is None


def test_a_direction_only_claim_still_checks():
    # No number given, but the driver still did what it did.
    c = check_claim(_Claim("Acrylate is up", "BA-EU", None), DRIVERS, 1000.0)
    assert c.verdict == "already_priced"
    assert c.actual_change_pct == 11.4


def test_a_claim_can_name_the_component_or_the_index():
    by_index = check_claim(_Claim("x", "BA-EU", 30.0), DRIVERS, 1000.0)
    by_label = check_claim(_Claim("x", "Butyl acrylate", 30.0), DRIVERS, 1000.0)
    assert by_index.verdict == by_label.verdict == "overstated"


# ── script ────────────────────────────────────────────────────────────────

class _Brief:
    product_name = "Acrylic Emulsion AE-40"
    currency = "EUR"
    unit = "t"
    period_label = "Q3-26"
    current_should_cost = 1842.0
    current_floor = 1676.22
    current_actual_price = 2040.0
    gap = 198.0
    data_gaps: list = []


def test_the_script_quotes_only_engine_numbers():
    checked = [check_claim(_Claim("Acrylate up 30%", "BA-EU", 30.0), DRIVERS, 1842.0)]
    lines = build_script(_Brief(), checked)
    body = " ".join(lines)
    assert "1,842.00 EUR/t" in body          # the target
    assert "1,676.22 EUR/t" in body          # the floor, a genuinely different number
    assert "198.00 EUR/t above" in body      # their ask, stated as a distance
    assert any("Acrylate up 30%" in ln for ln in lines)


def test_an_excluded_claim_stays_out_of_the_script():
    checked = [
        check_claim(_Claim("in", "BA-EU", 30.0), DRIVERS, 1842.0),
        check_claim(_Claim("out", "LCI-EU", 3.0, include=False), DRIVERS, 1842.0),
    ]
    body = " ".join(build_script(_Brief(), checked))
    assert "in" in body and "“out”" not in body


def test_data_gaps_are_warned_about_in_the_script_itself():
    """A line riding flat because its index is missing is a line you must not
    quote in the room, so the warning belongs in the script and not only on
    the page beside it."""
    class _B(_Brief):
        data_gaps = [object()]
    lines = build_script(_B(), [])
    assert any("riding flat" in ln for ln in lines)


def test_no_verdict_ever_adds_to_the_target():
    """The arithmetic the whole feature rests on. Whatever a supplier cites,
    the should-cost already consumed that movement, so no verdict may present
    a driver as grounds for paying more."""
    claims = [
        _Claim("up 30", "BA-EU", 30.0), _Claim("doubled", "ELEC-EU", 100.0),
        _Claim("labour", "LCI-EU", 3.0), _Claim("freight", "Freight", None),
        _Claim("catalyst", "CAT-EU", 8.0), _Claim("vague"),
    ]
    for c in claims:
        checked = check_claim(c, DRIVERS, 1842.0)
        assert checked.verdict in {
            "overstated", "contradicted", "already_priced",
            "no_movement", "out_of_scope", "unmapped",
        }
        assert "justifies" not in checked.note.lower()
        assert "supports an increase" not in checked.note.lower()


# ── API ───────────────────────────────────────────────────────────────────

def _cleanup(db, cost_model_id):
    bypass_rls_var.set(True)
    db.execute(text("DELETE FROM supplier_claims WHERE cost_model_id = :i"),
               {"i": str(cost_model_id)})
    db.commit()


def test_claims_round_trip_and_prep_checks_them(db, tenant_a, client_as, brief_model):
    c = client_as(tenant_a)
    cm_id = brief_model.id
    try:
        r = c.post(f"/api/negotiation-prep/{cm_id}/claims",
                   json={"said": "Your feedstock is up 40%", "year": CUR_Y, "quarter": CUR_Q,
                         "claimed_change_pct": 40.0})
        assert r.status_code == 201, r.text
        claim_id = r.json()["id"]

        r = c.get(f"/api/negotiation-prep/{cm_id}/claims")
        assert r.status_code == 200 and len(r.json()) == 1

        r = c.get(f"/api/negotiation-prep/{cm_id}/prep")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ladder"]["should_cost"] > 0
        assert len(body["claims"]) == 1
        # With no driver chosen it is a to-do, and the picker offers the real
        # recipe so the buyer is not typing a name that matches nothing.
        assert body["claims"][0]["verdict"] == "unmapped"
        assert isinstance(body["drivers"], list)
        assert body["script"], "a script is always assembled, even with one open claim"

        # Attaching it to a real driver changes the verdict.
        if body["drivers"]:
            label = body["drivers"][0]["index_name"] or body["drivers"][0]["label"]
            r = c.put(f"/api/negotiation-prep/claims/{claim_id}", json={"driver_label": label})
            assert r.status_code == 200
            body = c.get(f"/api/negotiation-prep/{cm_id}/prep").json()
            assert body["claims"][0]["verdict"] != "unmapped"

        r = c.delete(f"/api/negotiation-prep/claims/{claim_id}")
        assert r.status_code == 204
        assert c.get(f"/api/negotiation-prep/{cm_id}/claims").json() == []
    finally:
        _cleanup(db, cm_id)


def test_the_recorded_words_cannot_be_rewritten(db, tenant_a, client_as, brief_model):
    """`said` is a record of what was actually said. Editing it would quietly
    change what the evidence is answering."""
    c = client_as(tenant_a)
    cm_id = brief_model.id
    try:
        claim_id = c.post(f"/api/negotiation-prep/{cm_id}/claims",
                          json={"said": "original wording", "year": CUR_Y, "quarter": CUR_Q}).json()["id"]
        c.put(f"/api/negotiation-prep/claims/{claim_id}", json={"said": "rewritten"})
        assert c.get(f"/api/negotiation-prep/{cm_id}/claims").json()[0]["said"] == "original wording"
    finally:
        _cleanup(db, cm_id)


def test_another_team_cannot_reach_it(db, tenant_a, tenant_b, client_as, brief_model):
    cm_id = brief_model.id
    other = client_as(tenant_b)
    assert other.get(f"/api/negotiation-prep/{cm_id}/prep").status_code in (403, 404)
    assert other.post(f"/api/negotiation-prep/{cm_id}/claims",
                      json={"said": "x", "year": CUR_Y, "quarter": CUR_Q}).status_code in (403, 404)


def test_unauthenticated_is_401(client):
    assert client.get(f"/api/negotiation-prep/{uuid.uuid4()}/prep").status_code == 401
