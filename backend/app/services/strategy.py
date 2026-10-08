"""Strategy service — a team's category strategy over the platform playbooks
(design §2.6, §2.8 and §4.2 "Strategy").

**The strategy unit is the playbook** (a product line's category as its market
report names it, e.g. "Coagulants"). A team reaches a playbook through its
portfolio:

    product ─► effective_lines()  (app/services/effective_lines.py)
                 1. the linked template's line (a team fork → its origin's)
                 2. the template behind one of its cost models
                    (formula_versions.source_coverage_id → coverage.template_id)
                 3. products.product_line_id (a custom product's line)
             ─► every line the product reaches (EffectiveLine.line_ids)
             ─► market_report_lines.product_line_id ─► slug ─► playbooks.slug

Line ↔ report is many-to-many, and one product can reach two lines (a linked
template and a different cost-model template), so one product can reach
several playbooks — that is why the landing separates **adopted** categories
(`strategy_records`) from **suggested** ones. A report join whose key is no
current line (`product_line_id` NULL) reaches nothing, and its `line_key` is
never served: it can hold a line name that must stay unpublished (design
§4.1). Such a line is shown as "Product line not yet published".
`playbooks.legacy_line_key` is never used to join.

**Team state overlays the defaults; nothing is copied.** Levers merge
`playbook_levers` with the team's `lever_scores` (a NULL field = the default
stands) and add the team's `custom_levers`; objectives take the team's
`team_objectives` row when there is one, else the playbook's authored
objective (pre-selected), else unselected.

**Spend is the team's own money, never platform data.** Annual spend = per
cost model, the latest four quarters that carry an actual price, each price ×
that quarter's volume (volume converted to the product's unit, money to the
reporting currency at that quarter's rate). A quarter with a price and no
volume adds nothing — the number is never filled in. A quarter with no FX rate
is added unconverted and recorded against its cost model, so every total says
whether it is complete (`fx_gaps`, `fx_incomplete`) for exactly the money it
sums.

**The team's own industry** (`team_industry`) leads a category's demand
categories. `teams` has no settings column, so it is read, in this order, from
(1) what the team's products declare, `products.custom_attributes.buyer_industry`
= an industry slug (the most common one; the demo-buyer seed sets
`municipal_water`), else (2) inferred: the industry with the most category
placements of the team's catalogue products among `servable` categories, ties
by industry name, else null. Inference alone is a weak signal: a product is
placed in many industries, and a water utility's coagulants can sit mostly in
`partial` categories of its own industry.

Pure reads: nothing here commits. Everything is computed inside the caller's
request transaction (the RLS GUCs are transaction-local — build responses
before the router commits).
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.actual_volume import ActualVolume
from app.models.cost_model import CostModel
from app.models.custom_fx_rate import CustomFxRate
from app.models.fx_pair import FxPair
from app.models.fx_rate import FxRate
from app.models.formula_template import FormulaRegionCoverage, FormulaTemplate
from app.models.market_report import MarketReport, MarketReportLine, MarketReportPanel
from app.models.price_data import ActualPrice
from app.models.product import Product
from app.models.strategy import (
    ACTION_STATUSES, GEMSTONES, LEVER_STATUSES, OBJECTIVES, PRIORITIES, CustomLever,
    LeverScore, Playbook, PlaybookLever, PlaybookLeverObjective, PlaybookObjective,
    StrategyAction, StrategyRecord, TeamObjective,
)
from app.models.product_line import LINE_KEY_SEP, ProductLine
from app.models.supplier import Supplier
from app.models.taxonomy_v2 import Category, CategoryPlacement, Industry
from app.models.team import Team, TeamMembership
from app.models.user import User
from app.services.catalog_visibility import listed_clause
from app.services.effective_lines import NONE as NO_LINE
from app.services.effective_lines import effective_lines
from app.services.fx_converter import get_fx_rate
from app.services.intelligence import combo_for_cost_model, derive
from app.services.unit_converter import convert_unit

# A team's own lifecycle for a category. "Active" is the column default.
RECORD_STATUSES = ("Not started", "Active", "On hold", "Closed")
# A lever counts as an open opportunity while it is still in play.
OPEN_LEVER_STATUSES = ("Identified", "Under evaluation", "Approved")
# The demo-buyer seed names its team "... (demo)"; its prices are illustrative.
DEMO_TEAM_SUFFIX = "(demo)"
TTM_QUARTERS = 4
EVOLUTION_QUARTERS = 12
FALLBACK_CURRENCY = "USD"
# `products.custom_attributes` key a team uses to declare the industry it buys
# for (an industry slug). See "The team's own industry" above.
BUYER_INDUSTRY_ATTR = "buyer_industry"

GEM_ORDER = {code: i for i, (code, _n, _c) in enumerate(GEMSTONES)}
GEM_NAME = {code: name for code, name, _c in GEMSTONES}
GEM_COLOUR = {code: colour for code, _n, colour in GEMSTONES}
GEM_CODE_ANY = {**{code: code for code in GEM_NAME},
                **{name.lower(): code for code, name in GEM_NAME.items()}}
OBJ_ORDER = {code: i for i, (code, _n, _d) in enumerate(OBJECTIVES)}
OBJ_NAME = {code: name for code, name, _d in OBJECTIVES}
OBJ_DESC = {code: desc for code, _n, desc in OBJECTIVES}

logger = logging.getLogger(__name__)

# What a screen shows for a line key that is no current line (design §4.1,
# decision 37): never the key or its tail.
UNPUBLISHED_LINE = "Product line not yet published"

PANEL_ORDER = ("overview", "how", "applications", "tech", "process", "supply",
               "pestel", "porter", "market_drivers", "kraljic", "outlook")


# ── Small helpers ────────────────────────────────────────────────────────────

def period_label(period: tuple[int, int]) -> str:
    return f"{period[0]}Q{period[1]}"


def user_ref(user: User | None) -> dict | None:
    if user is None:
        return None
    return {"id": user.id, "name": user.display_name or user.email}


def users_by_id(db: Session, ids) -> dict[uuid.UUID, User]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()}


def is_member(db: Session, team_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return db.query(TeamMembership).filter(
        TeamMembership.team_id == team_id, TeamMembership.user_id == user_id,
    ).first() is not None


def is_demo_team(team: Team | None) -> bool:
    """The demo buyer's prices are illustrative. The seed marks its team by
    name ("Aquaverde Water Utility (demo)"); there is no flag column."""
    return bool(team and (team.name or "").rstrip().endswith(DEMO_TEAM_SUFFIX))


def known_currencies(db: Session, team_id: uuid.UUID) -> set[str]:
    """Currency codes the app can report in for this team: every currency in
    the platform FX data (pairs and quarterly rates), the team's own custom
    rates, the team's cost-model currencies, and the fallback default."""
    codes: set[str] = {FALLBACK_CURRENCY}
    for model in (FxPair, FxRate):
        for a, b in db.query(model.from_currency, model.to_currency).distinct():
            codes.update((a, b))
    for a, b in db.query(CustomFxRate.from_currency, CustomFxRate.to_currency).filter(
            CustomFxRate.team_id == team_id).distinct():
        codes.update((a, b))
    codes.update(c for (c,) in db.query(CostModel.currency).filter(
        CostModel.team_id == team_id).distinct())
    return {c.strip().upper() for c in codes if c and c.strip()}


def gemstone_code(value: str | None) -> str | None:
    """A gemstone given as its code ("VC") or display name."""
    if not value:
        return None
    return GEM_CODE_ANY.get(value) or GEM_CODE_ANY.get(value.strip().lower())


def kraljic_of(playbook: Playbook) -> dict | None:
    k = playbook.kraljic
    if not k:
        return None
    return {key: k.get(key) for key in ("cx", "cy", "badge", "label", "boundary")}


def _as_date(value: datetime | date | None) -> date | None:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


# ── Portfolio → playbooks ────────────────────────────────────────────────────

@dataclass
class TeamProduct:
    product: Product
    template: FormulaTemplate | None          # the platform template it stands for
    # The product's effective line (effective_lines.py). None when it has no
    # line, or its line is retired (hidden); the key and name follow.
    product_line_id: int | None
    line_key: str | None
    line_name: str | None
    family_id: int | None
    subfamily_id: int | None                  # the line's sub-family
    taxonomy_source: str                      # template | cost_model | manual | none
    slugs: set[str] = field(default_factory=set)   # playbooks it reaches
    cost_models: list[CostModel] = field(default_factory=list)

    @property
    def pid(self) -> str | None:
        return self.template.code if self.template else None

    def ref(self) -> dict:
        return {"product_id": self.product.id, "pid": self.pid, "name": self.product.name}


def _platform_templates(db: Session, ids: set) -> dict[uuid.UUID, FormulaTemplate]:
    """template id (platform, or a team fork) → the platform template it
    stands for. A team template with no platform origin stands for itself."""
    if not ids:
        return {}
    rows = db.query(FormulaTemplate).filter(FormulaTemplate.id.in_(ids)).all()
    origin_ids = {t.origin_id for t in rows if t.team_id is not None and t.origin_id}
    origins = {
        t.id: t for t in db.query(FormulaTemplate).filter(
            FormulaTemplate.id.in_(origin_ids), FormulaTemplate.team_id.is_(None)).all()
    } if origin_ids else {}
    out = {}
    for t in rows:
        if t.team_id is not None and t.origin_id in origins:
            out[t.id] = origins[t.origin_id]
        else:
            out[t.id] = t
    return out


def playbooks_by_line(db: Session, line_ids) -> dict[int, set[str]]:
    """product line id → the playbooks its market reports reach
    (`market_report_lines.product_line_id` → slug → `playbooks.slug`). A
    retired line reaches nothing (it is hidden, design §1.4)."""
    line_ids = {i for i in line_ids if i is not None}
    if not line_ids:
        return {}
    out: dict[int, set[str]] = defaultdict(set)
    for line_id, slug in db.query(MarketReportLine.product_line_id, MarketReportLine.slug).join(
            Playbook, Playbook.slug == MarketReportLine.slug).join(
            ProductLine, ProductLine.id == MarketReportLine.product_line_id).filter(
            MarketReportLine.product_line_id.in_(line_ids), ProductLine.retired_at.is_(None)):
        out[line_id].add(slug)
    return out


def team_portfolio(db: Session, team_id: uuid.UUID) -> list[TeamProduct]:
    """Every team product with its catalogue template, effective line and the
    playbooks it reaches. A bounded number of queries whatever the portfolio
    size."""
    products = db.query(Product).filter(Product.team_id == team_id).order_by(
        Product.name, Product.id).all()
    if not products:
        return []
    cost_models = db.query(CostModel).filter(CostModel.team_id == team_id).order_by(
        CostModel.created_at, CostModel.id).all()
    cms_by_product: dict[uuid.UUID, list[CostModel]] = defaultdict(list)
    for cm in cost_models:
        cms_by_product[cm.product_id].append(cm)

    eff = effective_lines(db, [p.id for p in products])

    # The template a product stands for (its `pid`): the linked one, else the
    # first cost-model template — the candidate order effective_lines uses.
    coverage_ids = {fv.source_coverage_id for cm in cost_models
                    for fv in cm.formula_versions if fv.source_coverage_id}
    coverage_template = dict(db.query(
        FormulaRegionCoverage.id, FormulaRegionCoverage.template_id,
    ).filter(FormulaRegionCoverage.id.in_(coverage_ids)).all()) if coverage_ids else {}
    primary_tid: dict[uuid.UUID, uuid.UUID | None] = {}
    for p in products:
        tid = p.formula_template_id
        if tid is None:
            tid = next((coverage_template[fv.source_coverage_id]
                        for cm in cms_by_product.get(p.id, [])
                        for fv in cm.formula_versions
                        if fv.source_coverage_id in coverage_template), None)
        primary_tid[p.id] = tid
    platform = _platform_templates(db, {t for t in primary_tid.values() if t})

    line_ids = {lid for e in eff.values() for lid in e.line_ids}
    lines = {ln.id: ln for ln in db.query(ProductLine).filter(
        ProductLine.id.in_(line_ids)).all()} if line_ids else {}
    slugs_by_line = playbooks_by_line(db, line_ids)

    out = []
    for p in products:
        e = eff.get(p.id, NO_LINE)
        line = lines.get(e.product_line_id) if e.product_line_id is not None else None
        shown = line if line is not None and line.retired_at is None else None
        slugs: set[str] = set()
        for lid in e.line_ids:
            slugs |= slugs_by_line.get(lid, set())
        tid = primary_tid[p.id]
        out.append(TeamProduct(
            product=p, template=platform.get(tid) if tid else None,
            product_line_id=shown.id if shown else None,
            line_key=shown.line_key if shown else None,
            line_name=shown.name if shown else None,
            family_id=e.family_id, subfamily_id=e.subfamily_id if shown else None,
            taxonomy_source=e.source,
            slugs=slugs, cost_models=cms_by_product.get(p.id, []),
        ))
    return out


# ── Spend ────────────────────────────────────────────────────────────────────

class Fx:
    """Quarter-rate conversion into one reporting currency, cached. A missing
    rate leaves the value unconverted (the portfolio router's rule) and is
    recorded so the response can say so."""

    def __init__(self, db: Session, team_id: uuid.UUID, to_ccy: str):
        self.db, self.team_id, self.to = db, team_id, to_ccy
        self._rates: dict[tuple, float | None] = {}
        self.gaps: set[tuple[str, tuple[int, int]]] = set()

    def __call__(self, value: float, from_ccy: str | None, period: tuple[int, int]) -> float:
        if not from_ccy or from_ccy == self.to:
            return value
        key = (from_ccy, period)
        if key not in self._rates:
            self._rates[key] = get_fx_rate(self.db, from_ccy, self.to, period[0], period[1],
                                           team_id=self.team_id)
        rate = self._rates[key]
        if rate is None:
            self.gaps.add(key)
            return value
        return value * rate

    def is_gap(self, from_ccy: str | None, period: tuple[int, int]) -> bool:
        """True if a conversion of `from_ccy` at `period` was left unconverted."""
        return (from_ccy, period) in self.gaps

    def gap_list(self, gaps=None) -> list[dict]:
        """`gaps` (default: every gap met) as `{currency, period}` rows."""
        gaps = self.gaps if gaps is None else gaps
        return [{"currency": c, "period": period_label(p)} for c, p in sorted(gaps)]


@dataclass
class ModelSpend:
    cost_model: CostModel
    value: float                       # TTM, reporting currency
    has_volume: bool
    prices: dict[tuple[int, int], float]
    # (currency, period) summed unconverted into `value` (no FX rate).
    fx_gaps: frozenset = frozenset()


def default_currency(cost_models: list[CostModel]) -> str:
    """The team's most common cost-model currency (ties alphabetical)."""
    counts = Counter(cm.currency for cm in cost_models if cm.currency)
    if not counts:
        return FALLBACK_CURRENCY
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


class TeamContext:
    """What every strategy read needs about one team, loaded once."""

    def __init__(self, db: Session, team_id: uuid.UUID, currency: str | None = None):
        self.db, self.team_id = db, team_id
        self.team = db.query(Team).filter(Team.id == team_id).first()
        self.portfolio = team_portfolio(db, team_id)
        all_cms = [cm for tp in self.portfolio for cm in tp.cost_models]
        self.currency = (currency or default_currency(all_cms)).upper()
        self.fx = Fx(db, team_id, self.currency)
        # The demo-buyer seed marks both its team name and every product it
        # writes (`custom_attributes.illustrative`); either is enough.
        self.illustrative = is_demo_team(self.team) or any(
            (tp.product.custom_attributes or {}).get("illustrative") for tp in self.portfolio)
        self._spend: dict[uuid.UUID, ModelSpend] | None = None
        self._team_industry: dict | None = None
        self._team_industry_done = False

    def team_industry(self) -> dict | None:
        if not self._team_industry_done:
            self._team_industry = team_industry(self.db, self.portfolio)
            self._team_industry_done = True
        return self._team_industry

    def products_for(self, slug: str) -> list[TeamProduct]:
        return [tp for tp in self.portfolio if slug in tp.slugs]

    def reachable_slugs(self) -> set[str]:
        return {s for tp in self.portfolio for s in tp.slugs}

    def spend(self) -> dict[uuid.UUID, ModelSpend]:
        if self._spend is None:
            self._spend = self._load_spend()
        return self._spend

    def _load_spend(self) -> dict[uuid.UUID, ModelSpend]:
        cms = {cm.id: (cm, tp) for tp in self.portfolio for cm in tp.cost_models}
        if not cms:
            return {}
        prices: dict[uuid.UUID, dict] = defaultdict(dict)
        for cm_id, y, q, price in self.db.query(
            ActualPrice.cost_model_id, ActualPrice.year, ActualPrice.quarter, ActualPrice.price,
        ).filter(ActualPrice.cost_model_id.in_(cms)):
            prices[cm_id][(y, q)] = float(price)
        volumes: dict[uuid.UUID, dict] = defaultdict(dict)
        for cm_id, y, q, vol, unit in self.db.query(
            ActualVolume.cost_model_id, ActualVolume.year, ActualVolume.quarter,
            ActualVolume.volume, ActualVolume.unit,
        ).filter(ActualVolume.cost_model_id.in_(cms)):
            volumes[cm_id][(y, q)] = (float(vol), unit)

        out = {}
        for cm_id, (cm, tp) in cms.items():
            product_unit = tp.product.unit
            total, has_volume = 0.0, False
            gaps: set[tuple] = set()
            for period in sorted(prices[cm_id], reverse=True)[:TTM_QUARTERS]:
                vol = volumes[cm_id].get(period)
                if vol is None:
                    continue
                qty, unit = vol
                if unit and product_unit and unit != product_unit:
                    try:
                        qty = convert_unit(qty, unit, product_unit)
                    except ValueError:
                        pass
                has_volume = True
                total += self.fx(prices[cm_id][period] * qty, cm.currency, period)
                if self.fx.is_gap(cm.currency, period):
                    gaps.add((cm.currency, period))
            out[cm_id] = ModelSpend(cost_model=cm, value=total, has_volume=has_volume,
                                    prices=prices[cm_id], fx_gaps=frozenset(gaps))
        return out

    def category_spend(self, slug: str) -> float:
        spend = self.spend()
        return sum(spend[cm.id].value for tp in self.products_for(slug)
                   for cm in tp.cost_models if cm.id in spend)

    def category_fx_gaps(self, slug: str) -> set[tuple]:
        """The FX gaps inside `category_spend(slug)`."""
        spend = self.spend()
        return {g for tp in self.products_for(slug) for cm in tp.cost_models
                if cm.id in spend for g in spend[cm.id].fx_gaps}


def team_industry(db: Session, portfolio: list[TeamProduct]) -> dict | None:
    """`{slug, name, source}` of the industry the team buys for, or None.
    Declared on its products first, else inferred (module docstring)."""
    declared = Counter(
        str(v).strip() for tp in portfolio
        if (v := (tp.product.custom_attributes or {}).get(BUYER_INDUSTRY_ATTR))
        and str(v).strip())
    if declared:
        names = dict(db.query(Industry.slug, Industry.name).filter(
            Industry.slug.in_(declared)).all())
        known = sorted((slug for slug in declared if slug in names),
                       key=lambda slug: (-declared[slug], names[slug].lower()))
        if known:
            return {"slug": known[0], "name": names[known[0]], "source": "declared"}
    pids = {tp.pid for tp in portfolio if tp.pid}
    if not pids:
        return None
    counts = db.query(Industry.slug, Industry.name, func.count(CategoryPlacement.id)).join(
        CategoryPlacement, CategoryPlacement.industry_id == Industry.id,
    ).join(Category, Category.id == CategoryPlacement.category_id).filter(
        CategoryPlacement.pid.in_(pids), Category.status == "servable",
    ).group_by(Industry.slug, Industry.name).all()
    if not counts:
        return None
    slug, name, _n = min(counts, key=lambda r: (-r[2], r[1].lower()))
    return {"slug": slug, "name": name, "source": "inferred"}


# ── Levers (authored ⊕ team scores ⊕ custom) ─────────────────────────────────

def _objective_refs(codes) -> list[dict]:
    known = [c for c in (codes or []) if c in OBJ_NAME]
    return [{"code": c, "name": OBJ_NAME[c]} for c in sorted(set(known), key=OBJ_ORDER.get)]


def _pick(override, default):
    return default if override is None else override


def _authored_row(lever: PlaybookLever, score: LeverScore | None, objectives: list[str],
                  actions_count: int) -> dict:
    s = score
    applies = _pick(s.applies if s else None, lever.default_applies)
    ease = _pick(s.ease if s else None, lever.default_ease)
    savings = _pick(s.savings_score if s else None, lever.savings_score)
    overridden = s is not None and any(
        getattr(s, f) is not None
        for f in ("applies", "ease", "savings_score", "savings_value", "status", "notes"))
    return {
        "lever_id": lever.id, "custom_lever_id": None, "code": lever.lever_code,
        "gemstone": lever.gemstone_code, "gemstone_name": GEM_NAME.get(lever.gemstone_code,
                                                                        lever.gemstone_code),
        "gemstone_colour": GEM_COLOUR.get(lever.gemstone_code),
        "title": lever.title, "guidance": lever.guidance,
        "applies": applies, "ease": ease, "savings_score": savings,
        "savings_value": float(s.savings_value) if s and s.savings_value is not None else None,
        "status": _pick(s.status if s else None, lever.default_status),
        "notes": _pick(s.notes if s else None, lever.default_notes),
        "objectives": _objective_refs(objectives), "scales": lever.scales,
        "custom": False, "overridden": overridden,
        "plotted": bool(applies and ease and savings),
        "actions_count": actions_count,
        "defaults": {"applies": lever.default_applies, "ease": lever.default_ease,
                     "savings_score": lever.savings_score, "status": lever.default_status,
                     "notes": lever.default_notes},
        "_sort": (GEM_ORDER.get(lever.gemstone_code, 99), 0, lever.sort_order, ""),
    }


def _custom_row(c: CustomLever, actions_count: int) -> dict:
    return {
        "lever_id": None, "custom_lever_id": c.id, "code": None,
        "gemstone": c.gemstone_code, "gemstone_name": GEM_NAME.get(c.gemstone_code,
                                                                    c.gemstone_code),
        "gemstone_colour": GEM_COLOUR.get(c.gemstone_code),
        "title": c.title, "guidance": c.guidance,
        "applies": c.applies, "ease": c.ease, "savings_score": c.savings_score,
        "savings_value": float(c.savings_value) if c.savings_value is not None else None,
        "status": c.status, "notes": c.notes,
        "objectives": _objective_refs(c.objectives), "scales": None,
        "custom": True, "overridden": False,
        "plotted": bool(c.applies and c.ease and c.savings_score),
        "actions_count": actions_count, "defaults": None,
        "_sort": (GEM_ORDER.get(c.gemstone_code, 99), 1, 0,
                  f"{c.created_at.isoformat() if c.created_at else ''}{c.id}"),
    }


def merged_levers(db: Session, team_id: uuid.UUID, slugs) -> dict[str, list[dict]]:
    """slug → its levers, merged and numbered. Order (§9): gemstone in
    `GEMSTONES` order; within a gemstone the authored levers by `sort_order`,
    then the team's custom levers by creation. `number` runs 1..N over that
    whole list — the number the bubble chart and the list share."""
    slugs = sorted(set(slugs))
    if not slugs:
        return {}
    levers = db.query(PlaybookLever).filter(PlaybookLever.playbook_slug.in_(slugs)).all()
    lever_ids = [lv.id for lv in levers]
    objectives: dict[int, list[str]] = defaultdict(list)
    scores: dict[int, LeverScore] = {}
    if lever_ids:
        for lid, code in db.query(PlaybookLeverObjective.lever_id,
                                  PlaybookLeverObjective.objective_code).filter(
                PlaybookLeverObjective.lever_id.in_(lever_ids)):
            objectives[lid].append(code)
        scores = {s.lever_id: s for s in db.query(LeverScore).filter(
            LeverScore.team_id == team_id, LeverScore.lever_id.in_(lever_ids)).all()}
    customs = db.query(CustomLever).filter(
        CustomLever.team_id == team_id, CustomLever.playbook_slug.in_(slugs)).all()
    counts: Counter = Counter()
    for lid, cid in db.query(StrategyAction.lever_id, StrategyAction.custom_lever_id).filter(
            StrategyAction.team_id == team_id, StrategyAction.playbook_slug.in_(slugs)):
        if lid:
            counts[("a", lid)] += 1
        if cid:
            counts[("c", cid)] += 1

    rows: dict[str, list[dict]] = {slug: [] for slug in slugs}
    for lv in levers:
        rows[lv.playbook_slug].append(_authored_row(
            lv, scores.get(lv.id), objectives.get(lv.id, []), counts[("a", lv.id)]))
    for c in customs:
        rows[c.playbook_slug].append(_custom_row(c, counts[("c", c.id)]))
    for slug, items in rows.items():
        items.sort(key=lambda r: r["_sort"])
        for i, r in enumerate(items, start=1):
            r.pop("_sort")
            r["number"] = i
    return rows


def lever_groups(levers: list[dict]) -> list[dict]:
    """The 8 gemstones in order, each with its levers (possibly none)."""
    by_gem: dict[str, list[dict]] = defaultdict(list)
    for r in levers:
        by_gem[r["gemstone"]].append(r)
    return [{
        "code": code, "name": name, "colour": colour,
        "count": len(by_gem[code]),
        "applying": sum(1 for r in by_gem[code] if r["applies"]),
        "levers": by_gem[code],
    } for code, name, colour in GEMSTONES]


def is_open_opportunity(row: dict) -> bool:
    return row["status"] in OPEN_LEVER_STATUSES and row["applies"] is not False


# ── Objectives overlay ───────────────────────────────────────────────────────

def objectives_overlay(db: Session, team_id: uuid.UUID, slug: str) -> list[dict]:
    """The 7 objectives in order. A team row wins (its `selected=false` is how
    a team unticks an authored pre-fill); else the playbook's authored
    objective, pre-selected; else unselected with no priority."""
    authored = {o.objective_code: o for o in db.query(PlaybookObjective).filter(
        PlaybookObjective.playbook_slug == slug).all()}
    team = {o.objective_code: o for o in db.query(TeamObjective).filter(
        TeamObjective.team_id == team_id, TeamObjective.playbook_slug == slug).all()}
    out = []
    for code, name, desc in OBJECTIVES:
        a, t = authored.get(code), team.get(code)
        authored_out = {"priority": a.priority, "note": a.note} if a else None
        if t is not None:
            selected, priority, note = t.selected, t.priority, t.note
        elif a is not None:
            selected, priority, note = True, a.priority, a.note
        else:
            selected, priority, note = False, None, None
        out.append({"code": code, "type": name, "desc": desc, "selected": selected,
                    "priority": priority, "note": note, "authored": authored_out,
                    "overridden": t is not None})
    return out


# ── Landing ──────────────────────────────────────────────────────────────────

def _last_activity(db: Session, team_id: uuid.UUID, slugs) -> dict[str, datetime]:
    """slug → the latest time the team touched anything in that category."""
    slugs = list(slugs)
    latest: dict[str, datetime] = {}
    if not slugs:
        return latest

    def fold(rows):
        for slug, ts in rows:
            if ts is not None and (slug not in latest or ts > latest[slug]):
                latest[slug] = ts

    for model in (StrategyRecord, TeamObjective, CustomLever, StrategyAction):
        fold(db.query(model.playbook_slug, func.max(model.updated_at)).filter(
            model.team_id == team_id, model.playbook_slug.in_(slugs),
        ).group_by(model.playbook_slug).all())
    fold(db.query(PlaybookLever.playbook_slug, func.max(LeverScore.updated_at)).join(
        LeverScore, LeverScore.lever_id == PlaybookLever.id).filter(
        LeverScore.team_id == team_id, PlaybookLever.playbook_slug.in_(slugs),
    ).group_by(PlaybookLever.playbook_slug).all())
    return latest


def action_is_overdue(action: StrategyAction, today: date | None = None) -> bool:
    today = today or date.today()
    return bool(action.due_date and action.due_date < today and action.status != "Done")


def category_rows(ctx: TeamContext, slugs) -> dict[str, dict]:
    """Landing rows for the given playbooks, keyed by slug."""
    db, team_id = ctx.db, ctx.team_id
    slugs = sorted(set(slugs))
    if not slugs:
        return {}
    playbooks = {p.slug: p for p in db.query(Playbook).filter(Playbook.slug.in_(slugs)).all()}
    slugs = [s for s in slugs if s in playbooks]
    records = {r.playbook_slug: r for r in db.query(StrategyRecord).filter(
        StrategyRecord.team_id == team_id, StrategyRecord.playbook_slug.in_(slugs)).all()}
    owners = users_by_id(db, [r.owner_user_id for r in records.values()])
    levers = merged_levers(db, team_id, slugs)
    today = date.today()
    in_progress: Counter = Counter()
    overdue: Counter = Counter()
    for a in db.query(StrategyAction).filter(
            StrategyAction.team_id == team_id, StrategyAction.playbook_slug.in_(slugs)).all():
        if a.status == "In progress":
            in_progress[a.playbook_slug] += 1
        if action_is_overdue(a, today):
            overdue[a.playbook_slug] += 1
    activity = _last_activity(db, team_id, slugs)
    report_slugs = {slug for (slug,) in db.query(MarketReport.slug).filter(
        MarketReport.slug.in_([p.report_slug for p in playbooks.values() if p.report_slug]))}

    rows = {}
    for slug in slugs:
        pb, rec = playbooks[slug], records.get(slug)
        rows[slug] = {
            "playbook_slug": slug, "name": pb.name, "family": pb.family,
            "adopted": rec is not None,
            "status": rec.status if rec else "Not started",
            "kraljic": kraljic_of(pb),
            "has_report": pb.report_slug in report_slugs,
            "annual_spend": round(ctx.category_spend(slug), 2),
            "currency": ctx.currency,
            # Some of `annual_spend` was summed unconverted (no FX rate).
            "fx_incomplete": bool(ctx.category_fx_gaps(slug)),
            "open_opportunities": sum(1 for r in levers.get(slug, []) if is_open_opportunity(r)),
            "actions_in_progress": in_progress[slug],
            "actions_overdue": overdue[slug],
            "owner": user_ref(owners.get(rec.owner_user_id)) if rec else None,
            # The team's latest touch on the category; the playbook's own
            # date until the team has done anything.
            "last_updated": _as_date(activity.get(slug)) or pb.last_updated,
            "playbook_last_updated": pb.last_updated,
            "products": [tp.ref() for tp in ctx.products_for(slug)],
        }
    return rows


def landing(ctx: TeamContext) -> dict:
    db = ctx.db
    adopted_slugs = {slug for (slug,) in db.query(StrategyRecord.playbook_slug).filter(
        StrategyRecord.team_id == ctx.team_id)}
    reachable = ctx.reachable_slugs()
    rows = category_rows(ctx, adopted_slugs | reachable)
    adopted = [rows[s] for s in adopted_slugs if s in rows]
    suggested = [rows[s] for s in reachable - adopted_slugs if s in rows]
    # The mockup's default sort: overdue first, then spend.
    adopted.sort(key=lambda r: (-r["actions_overdue"], -r["annual_spend"], r["name"]))
    suggested.sort(key=lambda r: (-r["annual_spend"], r["name"]))
    # The rates missing from the spend these rows show.
    gaps = {g for slug in rows for g in ctx.category_fx_gaps(slug)}
    return {"team_id": ctx.team_id, "currency": ctx.currency,
            "illustrative": ctx.illustrative, "team_industry": ctx.team_industry(),
            "adopted": adopted, "suggested": suggested,
            "fx_gaps": ctx.fx.gap_list(gaps), "fx_incomplete": bool(gaps)}


# ── Category detail ─────────────────────────────────────────────────────────

def playbook_unpublished(db: Session, playbook: Playbook) -> bool:
    """A playbook whose report was written for a product line that is not
    published (every report join is off the current axis). Its title and its
    report name that line, which must never be shown (design §4.1, Laurent's
    frozen instruction), so Strategy does not serve it - the same rule
    Intelligence applies to the report itself (`intel_reference.report_unpublished`)."""
    from app.services.intel_reference import get_snapshot, report_unpublished
    snap = get_snapshot(db)
    if snap is None:  # no catalogue snapshot (only in tests that stub it out)
        return False
    return report_unpublished(snap, playbook.report_slug or playbook.slug)


def report_lines(db: Session, slug: str) -> list[dict]:
    """The product lines a report (and so its playbook) is written for: its
    `market_report_lines` rows, resolved to current lines. A row whose key is
    no current line — or whose line is retired — is one entry named
    "Product line not yet published", with no key: the stored key can be a
    line name that must stay unpublished (design §4.1). Lines are listed by
    name, each once; the unpublished entry comes last."""
    direct_first = case((MarketReportLine.source == "report_map", 0), else_=1)
    rows = db.query(MarketReportLine.product_line_id, MarketReportLine.source).filter(
        MarketReportLine.slug == slug).order_by(direct_first, MarketReportLine.line_key).all()
    ids = {lid for lid, _src in rows if lid is not None}
    current = {ln.id: ln for ln in db.query(ProductLine).filter(
        ProductLine.id.in_(ids), ProductLine.retired_at.is_(None)).all()} if ids else {}
    published: dict[int, dict] = {}
    unpublished: dict | None = None
    for line_id, source in rows:
        ln = current.get(line_id) if line_id is not None else None
        if ln is None:
            if unpublished is None:
                unpublished = {"product_line_id": None, "line_key": None,
                               "name": UNPUBLISHED_LINE, "family_id": None,
                               "subfamily_id": None, "published": False, "source": source}
            continue
        if ln.id not in published:
            published[ln.id] = {"product_line_id": ln.id, "line_key": ln.line_key,
                                "name": ln.name, "family_id": ln.family_id,
                                "subfamily_id": ln.subfamily_id, "published": True,
                                "source": source}
    out = sorted(published.values(), key=lambda r: (r["name"].lower(), r["product_line_id"]))
    return out + ([unpublished] if unpublished else [])


def category_detail(ctx: TeamContext, playbook: Playbook) -> dict:
    db, slug = ctx.db, playbook.slug
    rec = db.query(StrategyRecord).filter(
        StrategyRecord.team_id == ctx.team_id, StrategyRecord.playbook_slug == slug).first()
    owner = users_by_id(db, [rec.owner_user_id]).get(rec.owner_user_id) if rec else None

    lines = report_lines(db, slug)
    line_ids = {ln["product_line_id"] for ln in lines if ln["product_line_id"]}

    # The listed catalogue products on those lines (groups left out: a group
    # is a family of grades, not something a team buys).
    catalogue = db.query(
        FormulaTemplate.code, FormulaTemplate.name, FormulaTemplate.product_line_id,
        FormulaTemplate.supply_status,
    ).filter(
        FormulaTemplate.team_id.is_(None), FormulaTemplate.product_line_id.in_(line_ids),
        FormulaTemplate.code.isnot(None), FormulaTemplate.card_kind == "product",
        listed_clause(),
    ).order_by(FormulaTemplate.name, FormulaTemplate.code).all() if line_ids else []
    key_of = {ln["product_line_id"]: ln["line_key"] for ln in lines if ln["product_line_id"]}

    team_products = ctx.products_for(slug)
    team_pids = {tp.pid for tp in team_products if tp.pid}
    placements = db.query(
        CategoryPlacement.pid, Category.code, Category.name, Category.fn, Category.status,
        Industry.name, Industry.slug,
    ).join(Category, Category.id == CategoryPlacement.category_id).join(
        Industry, Industry.id == CategoryPlacement.industry_id,
    ).filter(CategoryPlacement.product_line_id.in_(line_ids)).all() if line_ids else []
    cats: dict[str, dict] = {}
    for pid, code, name, fn, status, industry, industry_slug in placements:
        c = cats.setdefault(code, {"industry": industry, "industry_slug": industry_slug,
                                   "code": code, "name": name, "fn": fn, "status": status,
                                   "pids": set()})
        c["pids"].add(pid)
    demand = []
    for c in cats.values():
        pids = sorted(c["pids"])
        demand.append({**c, "pids": pids, "team_pids": [p for p in pids if p in team_pids]})
    # The team's own industry first, then what this team buys, then by
    # industry and code.
    industry = ctx.team_industry()
    own = industry["slug"] if industry else None
    demand.sort(key=lambda c: (c["industry_slug"] != own, not c["team_pids"],
                               c["industry"], c["code"]))

    levers = merged_levers(db, ctx.team_id, [slug]).get(slug, [])
    groups = lever_groups(levers)
    return {
        "playbook_slug": slug, "name": playbook.name, "family": playbook.family,
        "report_slug": playbook.report_slug,
        "playbook_last_updated": playbook.last_updated,
        "kraljic": kraljic_of(playbook),
        "adopted": rec is not None,
        "record": {"status": rec.status, "owner": user_ref(owner),
                   "created_at": rec.created_at, "updated_at": rec.updated_at} if rec else None,
        "lines": lines,
        "team_industry": industry,
        "demand_categories": demand,
        "team_products": [{
            "product_id": tp.product.id, "name": tp.product.name, "pid": tp.pid,
            "template_name": tp.template.name if tp.template else None,
            "product_line_id": tp.product_line_id,
            "line_key": tp.line_key, "line_name": tp.line_name,
            "taxonomy_source": tp.taxonomy_source,
            "cost_model_count": len(tp.cost_models),
        } for tp in team_products],
        "catalogue_products": [{"pid": code, "name": name, "product_line_id": line_id,
                                "line_key": key_of.get(line_id), "supply_status": status}
                               for code, name, line_id, status in catalogue],
        "objectives": objectives_overlay(db, ctx.team_id, slug),
        "gemstones": [{k: g[k] for k in ("code", "name", "colour", "count", "applying")}
                      for g in groups],
        "lever_count": len(levers),
    }


# ── Strategic analysis ───────────────────────────────────────────────────────

def report_caveat(db: Session, report: MarketReport) -> dict:
    """REP-3's caveat — the same one the Intelligence report tab shows
    (`intel_reference.caveat_for`), so the two pages cannot word it
    differently. That answer is used only if its lines name no unpublished
    line (design §4.1: neither the stored key nor its tail); otherwise, or
    should that module fail, the same shape is built here from
    `report_lines()`, which never carries a stored key that is no current
    line.

    The report's own title and `old_line_name` are the report's, not a line's,
    and are shown as written (for a report whose old line was left unnamed
    they read the same as that line's old key tail)."""
    lines = report_lines(db, report.slug)
    hidden = _unpublished_texts(db, report.slug)
    try:
        from app.services.intel_reference import caveat_for, get_snapshot
        caveat = caveat_for(get_snapshot(db), report.slug)
        if not _mentions(caveat.get("lines"), hidden):
            return caveat
        logger.warning("intel_reference.caveat_for names an unpublished line for %s; "
                       "local caveat used", report.slug)
    except Exception:  # noqa: BLE001 — the tab must still render its caveat
        logger.warning("intel_reference.caveat_for failed for %s; local caveat used",
                       report.slug, exc_info=True)
    shown = [{"line_key": ln["line_key"], "product_line_id": ln["product_line_id"],
              "name": ln["name"], "visible": ln["published"]} for ln in lines]
    old = report.old_line_name or report.name
    when = f" in {report.as_of.strftime('%B %Y')}" if report.as_of else ""
    n = len(shown)
    names = [ln["name"] for ln in shown if ln["visible"]]
    if len(names) < n:                      # the one unpublished entry, last
        names.append("one not yet published")
    text = f"This report was written{when} for the product line then called {old}."
    if n == 0:
        text += " No current product line maps to it."
    elif n == 1 and shown[0]["visible"]:
        text += f" That line is now called {names[0]}."
    elif n == 1:
        text += " Its current product line is not yet published."
    else:
        text += f" That line is now split into {n} product lines: " + ", ".join(names) + "."
    text += (" The report has not been regenerated since, and the industry names in it"
             + (f" are the {report.as_of.strftime('%B %Y')} vocabulary." if report.as_of
                else " are the vocabulary it was written in."))
    return {"as_of": report.as_of.strftime("%Y-%m") if report.as_of else None, "text": text,
            "old_line_name": old, "split_into_n_lines": n, "lines": shown}


def _unpublished_texts(db: Session, slug: str) -> set[str]:
    """The stored keys of a report's rows that resolve to no current line,
    and their line-name tails: text no strategy payload may carry."""
    out: set[str] = set()
    for (key,) in db.query(MarketReportLine.line_key).outerjoin(
            ProductLine, ProductLine.id == MarketReportLine.product_line_id).filter(
            MarketReportLine.slug == slug,
            (MarketReportLine.product_line_id.is_(None)) | ProductLine.retired_at.isnot(None)):
        out.add(key)
        tail = key.partition(LINE_KEY_SEP)[2].strip()
        if tail:
            out.add(tail)
    return out


def _mentions(value, texts: set[str]) -> bool:
    """True if any string inside `value` (walked) contains one of `texts`."""
    if not texts:
        return False
    if isinstance(value, str):
        return any(t in value for t in texts)
    if isinstance(value, dict):
        return any(_mentions(v, texts) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_mentions(v, texts) for v in value)
    return False


def analysis(db: Session, playbook: Playbook) -> dict:
    report = db.query(MarketReport).filter(
        MarketReport.slug == playbook.report_slug).first() if playbook.report_slug else None
    kraljic = kraljic_of(playbook)
    out = {"playbook_slug": playbook.slug, "name": playbook.name, "family": playbook.family,
           "report_slug": playbook.report_slug, "available": report is not None,
           "report_name": report.name if report else None,
           # "YYYY-MM", the same form as the caveat and /intel/reports.
           "as_of": report.as_of.strftime("%Y-%m") if report and report.as_of else None,
           "caveat": report_caveat(db, report) if report else None,
           "panel_order": [], "panels": {}, "kraljic": kraljic}
    if report is None:
        return out
    panels = db.query(MarketReportPanel).filter(MarketReportPanel.slug == report.slug).order_by(
        MarketReportPanel.ordinal).all()
    for p in panels:
        data = p.data or {}
        body = {"heading": p.heading, "html": p.html}
        if p.panel == "pestel":
            body.update(columns=data.get("columns") or [], cards=data.get("rows") or [])
        elif p.panel == "porter":
            body.update(columns=data.get("columns") or [], rows=data.get("rows") or [],
                        implication=None)
        elif p.panel == "market_drivers":
            body.update(cards=data.get("cards") or [])
        elif p.panel == "kraljic":
            # Position and badge from the playbook (the strategy source); the
            # narrative from the report. They agree on 165 of 167 playbooks.
            src = kraljic or {}
            body.update(
                cx=src.get("cx", data.get("cx")), cy=src.get("cy", data.get("cy")),
                badge=src.get("badge"), label=src.get("label") or data.get("quadrant"),
                boundary=src.get("boundary"), quadrant=data.get("quadrant"),
                lead=data.get("lead"), note=data.get("note"),
                narrative=data.get("narrative") or [])
        if p.panel in PANEL_ORDER:
            out["panels"][p.panel] = body
            out["panel_order"].append(p.panel)
    return out


# ── Spend analysis ───────────────────────────────────────────────────────────

def _share_rows(values: dict, total: float) -> list[tuple]:
    items = sorted(values.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return [(k, v, round(100.0 * v / total, 1) if total else 0.0) for k, v in items]


def concentration(by_supplier: list[dict]) -> tuple[dict | None, str | None]:
    """Top-supplier share and a Herfindahl–Hirschman index over supplier
    shares, with the usual 1,500 / 2,500 bands, and one plain sentence."""
    total = sum(s["value"] for s in by_supplier)
    if total <= 0:
        return None, None
    shares = [(s["name"] or "Unassigned supplier", 100.0 * s["value"] / total)
              for s in by_supplier]
    hhi = round(sum(sh * sh for _n, sh in shares))
    level = ("highly concentrated" if hhi > 2500 else
             "moderately concentrated" if hhi >= 1500 else "unconcentrated")
    top_name, top = shares[0]
    top2 = shares[0][1] + shares[1][1] if len(shares) > 1 else None
    data = {"suppliers": len(shares), "top_supplier": top_name,
            "top_share_pct": round(top, 1),
            "top2_share_pct": round(top2, 1) if top2 is not None else None,
            "hhi": hhi, "level": level}
    if len(shares) == 1:
        note = f"All spend is with one supplier ({top_name}); HHI {hhi:,} — {level}."
    else:
        note = (f"{top2:.0f}% of spend concentrated in the top 2 suppliers "
                f"({shares[0][0]}, {shares[1][0]}); {top_name} alone holds {top:.0f}%. "
                f"HHI {hhi:,} — {level}.")
    return data, note


def _weighted(points: dict[uuid.UUID, float], weights: dict[uuid.UUID, float]) -> float | None:
    """Weighted mean over the cost models that have a value, renormalised."""
    if not points:
        return None
    w = {k: weights.get(k, 0.0) for k in points}
    total = sum(w.values())
    if total <= 0:
        return sum(points.values()) / len(points)
    return sum(points[k] * w[k] for k in points) / total


def category_spend(ctx: TeamContext, playbook: Playbook) -> dict:
    db, slug = ctx.db, playbook.slug
    spend = ctx.spend()
    products = ctx.products_for(slug)
    models = [(tp, cm) for tp in products for cm in tp.cost_models]
    suppliers = {s.id: s for s in db.query(Supplier).filter(
        Supplier.id.in_({cm.supplier_id for _tp, cm in models if cm.supplier_id} or {-1})).all()}

    by_product: dict = {}
    by_supplier: Counter = Counter()
    by_site: Counter = Counter()
    for tp, cm in models:
        ms = spend.get(cm.id)
        value = ms.value if ms else 0.0
        entry = by_product.setdefault(tp.product.id, {
            "product_id": tp.product.id, "pid": tp.pid, "name": tp.product.name,
            "value": 0.0, "has_volume": False})
        entry["value"] += value
        entry["has_volume"] = entry["has_volume"] or bool(ms and ms.has_volume)
        by_supplier[cm.supplier_id] += value
        by_site[(cm.destination_country, cm.destination_region)] += value
    total = sum(e["value"] for e in by_product.values())

    product_rows = sorted(by_product.values(), key=lambda e: (-e["value"], e["name"]))
    product_out = [{**e, "key": str(e["product_id"]), "value": round(e["value"], 2),
                    "pct": round(100.0 * e["value"] / total, 1) if total else 0.0}
                   for e in product_rows]
    supplier_out = [{
        "key": str(sid) if sid else "none", "supplier_id": sid,
        "name": suppliers[sid].name if sid in suppliers else None,
        "value": round(v, 2), "pct": pct,
    } for sid, v, pct in _share_rows(dict(by_supplier), total)]
    site_out = [{
        "key": f"{country or ''}|{region or ''}", "name": country, "region": region,
        "value": round(v, 2), "pct": pct,
    } for (country, region), v, pct in _share_rows(dict(by_site), total)]
    conc, note = concentration([s for s in supplier_out if s["value"] > 0])

    evolution, base = _evolution(ctx, models)
    # The rates missing from the money this response sums.
    gaps = {g for _tp, cm in models if cm.id in spend for g in spend[cm.id].fx_gaps}
    return {
        "playbook_slug": slug, "currency": ctx.currency, "total": round(total, 2),
        "has_spend": total > 0,
        "window": f"latest {TTM_QUARTERS} quarters with an actual price, per cost model",
        "by_product": product_out, "by_supplier": supplier_out, "by_site": site_out,
        "concentration": conc, "concentration_note": note,
        "evolution_base": base, "evolution": evolution,
        "illustrative": ctx.illustrative,
        "fx_gaps": ctx.fx.gap_list(gaps), "fx_incomplete": bool(gaps),
    }


def _evolution(ctx: TeamContext, models) -> tuple[list[dict], str | None]:
    """Quarterly should-cost vs actual-price indices for the category, both
    100 at the first period of the window. Each cost model is indexed on
    itself first (units and currencies differ across products), then the
    indices are spend-weighted — renormalised over the models that have a
    value in that quarter, never carried forward."""
    db, spend = ctx.db, ctx.spend()
    prices = {cm.id: spend[cm.id].prices for _tp, cm in models if cm.id in spend}
    weights = {cm.id: spend[cm.id].value for _tp, cm in models if cm.id in spend}

    derived: dict[tuple, dict] = {}
    should: dict[uuid.UUID, dict] = {}
    for _tp, cm in models:
        combo = combo_for_cost_model(db, cm)
        if combo is None:
            continue
        key = (combo.template_id, combo.region)
        if key not in derived:
            result = derive(db, combo.template_id, combo.region, team_id=ctx.team_id)
            derived[key] = ({(p["year"], p["quarter"]): p["level"] for p in result.series}
                            if result.evaluable else {})
        if derived[key]:
            should[cm.id] = derived[key]

    periods = sorted({p for series in prices.values() for p in series})
    if not periods:
        periods = sorted({p for series in should.values() for p in series})
    periods = periods[-EVOLUTION_QUARTERS:]
    if not periods:
        return [], None

    def rebased(series_by_model: dict[uuid.UUID, dict]) -> dict[uuid.UUID, dict]:
        out = {}
        for cm_id, series in series_by_model.items():
            base = next((series[p] for p in periods if series.get(p)), None)
            if base:
                out[cm_id] = {p: 100.0 * series[p] / base for p in periods if p in series}
        return out

    actual_idx, should_idx = rebased(prices), rebased(should)
    points = []
    for p in periods:
        a = _weighted({k: s[p] for k, s in actual_idx.items() if p in s}, weights)
        s = _weighted({k: v[p] for k, v in should_idx.items() if p in v}, weights)
        points.append({"period": period_label(p), "year": p[0], "quarter": p[1],
                       "should_cost_index": round(s, 2) if s is not None else None,
                       "actual_price_index": round(a, 2) if a is not None else None})
    return points, period_label(periods[0])


# ── Actions ──────────────────────────────────────────────────────────────────

def action_rows(db: Session, team_id: uuid.UUID, slug: str,
                actions: list[StrategyAction] | None = None,
                levers: list[dict] | None = None) -> list[dict]:
    if actions is None:
        actions = db.query(StrategyAction).filter(
            StrategyAction.team_id == team_id, StrategyAction.playbook_slug == slug).all()
    if levers is None:
        levers = merged_levers(db, team_id, [slug]).get(slug, [])
    by_lever = {r["lever_id"]: r for r in levers if r["lever_id"]}
    by_custom = {r["custom_lever_id"]: r for r in levers if r["custom_lever_id"]}
    users = users_by_id(db, [a.assignee_user_id for a in actions])
    today = date.today()
    out = []
    for a in actions:
        lever = by_lever.get(a.lever_id) if a.lever_id else by_custom.get(a.custom_lever_id)
        out.append({
            "id": a.id, "playbook_slug": a.playbook_slug, "title": a.title,
            "description": a.description, "lever_id": a.lever_id,
            "custom_lever_id": a.custom_lever_id,
            "lever": {"number": lever["number"], "title": lever["title"], "code": lever["code"],
                      "gemstone": lever["gemstone"], "gemstone_name": lever["gemstone_name"],
                      "custom": lever["custom"]} if lever else None,
            "assignee_user_id": a.assignee_user_id,
            "assignee": user_ref(users.get(a.assignee_user_id)),
            "start_date": a.start_date, "due_date": a.due_date, "status": a.status,
            "pct_complete": a.pct_complete, "overdue": action_is_overdue(a, today),
            "created_at": a.created_at, "updated_at": a.updated_at,
        })
    # Lever order (the Opportunities tab's numbering), then due date; actions
    # with no lever last.
    out.sort(key=lambda r: (r["lever"]["number"] if r["lever"] else 10_000,
                            r["due_date"] or date.max, str(r["id"])))
    return out


def action_counts(rows: list[dict]) -> dict:
    status = Counter(r["status"] for r in rows)
    return {"total": len(rows), "not_started": status["Not started"],
            "in_progress": status["In progress"], "blocked": status["Blocked"],
            "done": status["Done"], "overdue": sum(1 for r in rows if r["overdue"])}


__all__ = [
    "ACTION_STATUSES", "LEVER_STATUSES", "PRIORITIES", "RECORD_STATUSES", "TeamContext",
    "action_counts", "action_rows", "analysis", "category_detail", "category_rows",
    "category_spend", "gemstone_code", "is_member", "known_currencies", "landing",
    "lever_groups", "merged_levers", "objectives_overlay", "team_industry",
]
