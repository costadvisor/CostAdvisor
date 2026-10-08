"""The demo buyer seed (spec D8, design WP-11): `app/services/demo_buyer.py`
+ `seed_demo_buyer.py`.

Every test builds a throwaway demo team — its own name, a test owner from
`user_factory`, colleague emails unique to the run — inside one transaction
and rolls it back. Nothing here commits, and nothing touches the real
"Aquaverde Water Utility (demo)" team or any platform row.

Needs the content drop loaded (catalogue, producers, reports, playbooks):
every test asks for `content_loaded`, which fails (never skips) on a stale
test database. Expected lever counts and card statuses are read from the drop
at run time (`tests/content_drop_expect.py`).
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import date

import pytest

from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.formula_template import FormulaRegionCoverage, FormulaTemplate
from app.models.price_data import ActualPrice
from app.models.actual_volume import ActualVolume
from app.models.product import Product
from app.models.rbac import Plan, Role
from app.models.strategy import LeverScore, StrategyAction, StrategyRecord, TeamObjective
from app.models.supplier import Supplier
from app.models.team import Team, TeamMembership
from app.models.user import User
from app.services import demo_buyer
from app.services.costing_engine import calculate_should_cost
from app.services.drop.report import LoadReport
from app.services.effective_lines import effective_lines
from app.services.permissions import has_permission
from tests import content_drop_expect as expect

pytestmark = pytest.mark.usefixtures("content_loaded")


@pytest.fixture
def config(db, user_factory):
    owner = user_factory()
    email = db.query(User).filter(User.id == owner["user_id"]).one().email
    run = uuid.uuid4().hex[:10]
    colleagues = tuple(
        demo_buyer.Colleague(c.key, f"{c.key}-{run}@aquaverde.example", c.display_name,
                             c.membership_role, c.title)
        for c in demo_buyer.COLLEAGUES
    )
    return demo_buyer.DemoBuyerConfig(
        team_name=f"Aquaverde test {run} (demo)",
        owner_email=email,
        colleagues=colleagues,
        google_id_prefix=f"demo-test-{run}-",
    )


@pytest.fixture
def seeded(db, config):
    """Seed once inside an open transaction; roll everything back afterwards."""
    try:
        report = LoadReport(title="test")
        result = demo_buyer.seed(db, report, config)
        db.expire_all()
        yield result, report
    finally:
        db.rollback()


def test_first_run_creates_the_buyer_and_second_run_changes_nothing(db, config, seeded):
    result, first = seeded
    assert first.changed > 0
    assert first.table("products").created == len(demo_buyer.PRODUCTS)
    n_lines = sum(len(p.lines) for p in demo_buyer.PRODUCTS)
    assert first.table("cost_models").created == n_lines
    assert first.table("actual_prices").created == n_lines * 12
    assert first.table("actual_volumes").created == n_lines * 12

    second = LoadReport(title="again")
    again = demo_buyer.seed(db, second, config)
    assert again.team_id == result.team_id
    assert second.changed == 0, second.render()


def test_team_is_on_the_dream_plan_with_default_roles(db, config, seeded):
    result, _ = seeded
    team = db.get(Team, result.team_id)
    assert team.name == config.team_name
    assert db.get(Plan, team.plan_id).name == "Dream Plan"

    roles = {r.name: {p.key for p in r.permissions}
             for r in db.query(Role).filter(Role.team_id == team.id).all()}
    assert set(roles) == {"Owner", "Admin", "Member"}
    for keys in roles.values():
        assert {"strategy.view", "strategy.edit"} <= keys
    assert "products.delete" in roles["Owner"]
    assert "products.delete" not in roles["Admin"]
    assert "products.edit" not in roles["Member"]

    memberships = {m.user_id: m.role for m in
                   db.query(TeamMembership).filter(TeamMembership.team_id == team.id).all()}
    assert memberships[result.owner_id] == "owner"
    assert len(memberships) == 1 + len(config.colleagues)

    member = db.get(User, result.colleague_ids["pieter"])
    admin = db.get(User, result.colleague_ids["noor"])
    assert member.email.endswith("@aquaverde.example")
    assert member.google_id.startswith(config.google_id_prefix)
    assert has_permission(db, member, team.id, "strategy.edit")
    assert has_permission(db, member, team.id, "products.view")
    assert not has_permission(db, member, team.id, "products.edit")
    assert has_permission(db, admin, team.id, "products.edit")
    assert not has_permission(db, admin, team.id, "products.delete")


def test_products_track_platform_combos_without_forking(db, config):
    platform_before = db.query(FormulaTemplate).filter(FormulaTemplate.team_id.is_(None)).count()
    try:
        result = demo_buyer.seed(db, LoadReport(), config)
        db.expire_all()
        # Linking, not forking: the seed writes no template at all.
        assert db.query(FormulaTemplate).filter(
            FormulaTemplate.team_id == result.team_id).count() == 0
        assert db.query(FormulaTemplate).filter(
            FormulaTemplate.team_id.is_(None)).count() == platform_before

        products = db.query(Product).filter(Product.team_id == result.team_id).all()
        assert {db.get(FormulaTemplate, p.formula_template_id).code for p in products} == {
            p.pid for p in demo_buyer.PRODUCTS}
        lines = effective_lines(db, [p.id for p in products])
        for product in products:
            template = db.get(FormulaTemplate, product.formula_template_id)
            assert template.team_id is None and template.card_kind == "product"
            # Linked by template id; the line comes from the template, never
            # from the product (product_line_id is for custom products).
            assert product.product_line_id is None
            line = lines[product.id]
            assert (line.source, line.product_line_id, line.template_id) == (
                "template", template.product_line_id, template.id)
            assert line.product_line_id is not None
            assert product.custom_attributes["illustrative"] is True

        versions = (db.query(FormulaVersion).join(CostModel)
                    .filter(CostModel.team_id == result.team_id).all())
        assert versions
        for fv in versions:
            assert fv.link_mode == "tracking"
            coverage = db.get(FormulaRegionCoverage, fv.source_coverage_id)
            assert coverage.region == "Europe"
            assert (fv.base_year, fv.base_quarter) == (coverage.base_year, coverage.base_quarter)
            assert float(fv.margin_value) == 0.0
            assert "Illustrative" in fv.notes
            weights = [float(c.weight) for c in db.query(FormulaComponent).filter(
                FormulaComponent.formula_version_id == fv.id).all()]
            assert sum(weights) == pytest.approx(1.0, abs=0.002)

        prices = (db.query(ActualPrice).join(CostModel)
                  .filter(CostModel.team_id == result.team_id).all())
        assert all(p.source_file == demo_buyer.MARKER for p in prices)
        assert {(p.year, p.quarter) for p in prices} == set(demo_buyer.PRICE_QUARTERS)
        volumes = (db.query(ActualVolume).join(CostModel)
                   .filter(CostModel.team_id == result.team_id).all())
        assert all(v.unit == "t" and float(v.volume) > 0 for v in volumes)
    finally:
        db.rollback()


def test_suppliers_are_producers_the_drop_lists_for_each_product(db, config, seeded):
    result, _ = seeded
    names = {s.name for s in db.query(Supplier).filter(Supplier.team_id == result.team_id)}
    assert names == {line.supplier for p in demo_buyer.PRODUCTS for line in p.lines}
    sites = {cm.destination_country for cm in
             db.query(CostModel).filter(CostModel.team_id == result.team_id)}
    assert sites == set(demo_buyer.SITES)

    template = db.query(FormulaTemplate).filter(
        FormulaTemplate.code == "BCI-FECL3-LIQ", FormulaTemplate.team_id.is_(None)).one()
    with pytest.raises(demo_buyer.DemoSeedError):
        demo_buyer._producer_country(db, template, "Not A Producer Of Ferric Chloride")
    # Every supplier is a (non-bucket) producer the loaded drop lists for
    # that product; the seed would have refused otherwise.
    for spec in demo_buyer.PRODUCTS:
        t = demo_buyer._platform_template(db, spec.pid)
        for line in spec.lines:
            demo_buyer._producer_country(db, t, line.supplier)


def test_actuals_follow_should_cost_and_ferric_chloride_drifts_above_it(db, config, seeded):
    result, _ = seeded
    # The one drifting supply line, as the seed defines it (its supplier is the
    # seed's own story, not a name this test repeats).
    (story_key,) = [k for k in demo_buyer.DRIFT if k[0] == "BCI-FECL3-LIQ"]
    last_four = demo_buyer.PRICE_QUARTERS[-4:]
    for key, cm_id in result.cost_model_ids.items():
        cm = db.get(CostModel, cm_id)
        prices = {(p.year, p.quarter): float(p.price) for p in
                  db.query(ActualPrice).filter(ActualPrice.cost_model_id == cm_id)}
        gaps = {}
        for period in demo_buyer.PRICE_QUARTERS:
            # The costing engine's should-cost — what Negotiate and Evolution plot.
            should = calculate_should_cost(db, cm, *period).should_cost
            gaps[period] = (prices[period] - should) / should
        if key == story_key:
            assert all(gaps[p] < 0.03 for p in demo_buyer.PRICE_QUARTERS[:-4])
            assert gaps[last_four[-1]] > 0.10
            assert gaps[last_four[-1]] > gaps[last_four[0]]
            assert cm.negotiation_state == "in_negotiation"
        else:
            # premium (<= 4%) plus noise (<= 1.5%) — never a story of its own
            assert all(-0.02 < g < 0.06 for g in gaps.values()), (key, gaps)
            assert cm.negotiation_state == "none"


def test_portfolio_summary_shows_the_story_not_the_base_price(db, config, seeded):
    """Monitor, the Negotiate list, Forecast and Dashboard read
    /api/portfolio/summary. Its "should-cost today" is the current quarter's
    should-cost, not the 2023Q1 base price, so the only price-drift alert is
    the seed's story line (ferric chloride into Rotterdam)."""
    from app.routers.portfolio import portfolio_summary
    from app.services.costing_engine import _current_quarter

    result, _ = seeded
    owner = db.get(User, result.owner_id)
    summary = portfolio_summary(team_id=result.team_id, reporting_currency="EUR",
                                db=db, current_user=owner)
    by_id = {m.cost_model_id: m for m in summary.models}
    assert set(by_id) == set(result.cost_model_ids.values())
    now = _current_quarter()
    for cm_id, m in by_id.items():
        cm = db.get(CostModel, cm_id)
        today = calculate_should_cost(db, cm, *now).should_cost
        assert m.current_should_cost == pytest.approx(today, abs=1e-3)
        assert m.current_should_cost != pytest.approx(float(cm.current_formula.base_price))
    (story_key,) = [k for k in demo_buyer.DRIFT if k[0] == "BCI-FECL3-LIQ"]
    story = by_id[result.cost_model_ids[story_key]]
    drifting = [m for m in summary.models if m.flag_price_drift]
    assert drifting == [story] and story.gap_pct > 10
    assert summary.models[0] is story          # the largest exposure


def test_strategy_state_has_one_overdue_action(db, config, seeded):
    result, _ = seeded
    records = {r.playbook_slug: r for r in db.query(StrategyRecord).filter(
        StrategyRecord.team_id == result.team_id)}
    assert set(records) == {slug for slug, _, _ in demo_buyer.STRATEGY_RECORDS}
    assert records["coagulants"].status == "Active"
    assert records["coagulants"].owner_user_id == result.owner_id

    objectives = db.query(TeamObjective).filter(
        TeamObjective.team_id == result.team_id,
        TeamObjective.playbook_slug == "coagulants").all()
    assert len(objectives) == 7
    selected = {o.objective_code: o.priority for o in objectives if o.selected}
    assert selected == {"cost_reduction": "High", "supply_security": "High",
                        "sustainability_esg": "Medium"}

    assert db.query(LeverScore).filter(LeverScore.team_id == result.team_id).count() == 6

    actions = db.query(StrategyAction).filter(StrategyAction.team_id == result.team_id).all()
    assert len(actions) == 5
    assert sum(a.status == "Done" for a in actions) == 1
    assert all(a.assignee_user_id is not None and a.lever_id is not None for a in actions)
    # Exactly one overdue on the seed date and on demo day a week later.
    for day in (demo_buyer.DEMO_TODAY, date(2026, 10, 2)):
        overdue = demo_buyer.overdue_actions(db, result.team_id, today=day)
        assert len(overdue) == 1
        assert overdue[0].status == "In progress"


def test_buyer_declares_its_industry_for_the_strategy_pages(db, config, seeded):
    """`teams` has no settings column: the buyer's industry is declared on its
    products, and the Strategy service reads it from there."""
    from app.services.strategy import team_industry, team_portfolio

    result, _ = seeded
    products = db.query(Product).filter(Product.team_id == result.team_id).all()
    assert products and all(
        p.custom_attributes["buyer_industry"] == demo_buyer.BUYER_INDUSTRY for p in products)
    from app.models.taxonomy_v2 import Industry

    name = db.query(Industry.name).filter(Industry.slug == demo_buyer.BUYER_INDUSTRY).scalar()
    assert name, "the buyer's industry is loaded"
    industry = team_industry(db, team_portfolio(db, result.team_id))
    assert industry == {"slug": demo_buyer.BUYER_INDUSTRY, "name": name, "source": "declared"}


def test_reset_removes_the_team_and_its_fictional_colleagues(db, config):
    try:
        result = demo_buyer.seed(db, LoadReport(), config)
        report = LoadReport()
        assert demo_buyer.reset(db, report, config) == 1
        assert db.get(Team, result.team_id) is None
        assert db.query(CostModel).filter(CostModel.team_id == result.team_id).count() == 0
        assert db.query(User).filter(
            User.id.in_(list(result.colleague_ids.values()))).count() == 0
        assert db.get(User, result.owner_id) is not None
        # and it comes back
        again = demo_buyer.seed(db, LoadReport(), config)
        assert again.team_id != result.team_id
        assert len(demo_buyer.find_demo_teams(db, config)) == 1
    finally:
        db.rollback()


def test_cli_dry_run_writes_nothing(db, config):
    import seed_demo_buyer

    assert seed_demo_buyer.run(dry_run=True, config=config) == 0
    db.expire_all()
    assert demo_buyer.find_demo_teams(db, config) == []
    emails = [c.email for c in config.colleagues]
    assert db.query(User).filter(User.email.in_(emails)).count() == 0


# ── Lines, playbooks, statuses (design WP-11, §8) ────────────────────────────

def _expected_playbooks(pid: str) -> set[str]:
    """The playbooks a catalogue product reaches in the drop: its line's
    report joins (current key, or a former key of that line)."""
    import json

    slugs = set()
    for path in expect.playbook_files():
        with open(path, encoding="utf-8") as fh:
            slugs.add(json.load(fh)["category"]["id"])
    line = expect.template_line(pid)
    return {s for s, k in expect.report_joins()
            if line is not None and expect.resolve_line_key(k) == line} & slugs


def test_the_adopted_categories_resolve_through_the_products_lines(db, config, seeded):
    """Every adopted category is reached by the buyer's products through
    their templates' lines, with exactly the products the drop puts there;
    the coagulants playbook carries all its authored levers."""
    from app.models.strategy import Playbook
    from app.services import strategy as svc

    result, _ = seeded
    ctx = svc.TeamContext(db, result.team_id)
    landing = svc.landing(ctx)
    adopted = {r["playbook_slug"]: r for r in landing["adopted"]}
    assert set(adopted) == {slug for slug, _s, _o in demo_buyer.STRATEGY_RECORDS}
    for slug, row in adopted.items():
        expected = {p.pid for p in demo_buyer.PRODUCTS if slug in _expected_playbooks(p.pid)}
        assert expected, slug
        assert {p["pid"] for p in row["products"]} == expected, slug
        assert row["annual_spend"] > 0, slug
    # What the portfolio reaches beyond the adopted four is suggested.
    reached = {s for p in demo_buyer.PRODUCTS for s in _expected_playbooks(p.pid)}
    assert {r["playbook_slug"] for r in landing["suggested"]} == reached - set(adopted)

    detail = svc.category_detail(ctx, db.get(Playbook, "coagulants"))
    assert detail["lever_count"] == expect.playbook_lever_count("coagulants")
    assert all(tp["taxonomy_source"] == "template" and tp["product_line_id"]
               for tp in detail["team_products"])


def test_the_buyer_products_carry_the_drops_card_statuses(db, config, seeded):
    """Design §8: the buyer holds cards of several statuses (some verified,
    some not yet audited or pending) — whatever the drop says each one is."""
    result, _ = seeded
    products = db.query(Product).filter(Product.team_id == result.team_id).all()
    got = {db.get(FormulaTemplate, p.formula_template_id).code:
           db.get(FormulaTemplate, p.formula_template_id).supply_status for p in products}
    want = {p.pid: expect.classify(p.pid).supply_status for p in demo_buyer.PRODUCTS}
    assert got == want
    assert Counter(want.values())["live"] < len(want)     # not only verified cards


def test_a_card_that_is_not_a_product_on_a_line_is_refused(db):
    pointer = expect.pointer_pids()[0]
    with pytest.raises(demo_buyer.DemoSeedError, match="pointer"):
        demo_buyer._platform_template(db, pointer)
    off_axis = sorted(expect.off_axis_record_keys())[0]
    with pytest.raises(demo_buyer.DemoSeedError, match="no product line"):
        demo_buyer._platform_template(db, off_axis)
    with pytest.raises(demo_buyer.DemoSeedError, match="not loaded"):
        demo_buyer._platform_template(db, "ZZZ-NOT-A-CARD")


def test_cli_takes_the_owner_email(db, config):
    """`--owner-email` names the owner (any case, as sign-in compares it);
    an unknown owner is refused with exit 2. Dry runs only: nothing is
    written."""
    import seed_demo_buyer

    owner = config.owner_email
    mine = demo_buyer.DemoBuyerConfig(owner_email=owner)
    assert seed_demo_buyer.main(["--dry-run", "--owner-email", owner.upper()]) == 0
    db.expire_all()
    assert demo_buyer.find_demo_teams(db, mine) == []
    assert seed_demo_buyer.main(
        ["--dry-run", "--owner-email", f"nobody-{uuid.uuid4().hex}@example.invalid"]) == 2
