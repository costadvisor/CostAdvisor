"""The demo buyer: "Aquaverde Water Utility (demo)" (demo build, spec D8).

A fictional municipal water utility, owned by the local super-admin
(`alexis@staminachem.com` by default, or `seed_demo_buyer.py --owner-email`;
resolved by email at run time), on the Dream Plan,
buying water-treatment chemicals for two sites. It exists so the investor demo
can walk the existing Portfolio / Monitor / Forecast / Negotiate pages and the
new Strategy pages on a buyer that looks real.

    ILLUSTRATIVE DEMO BUYER DATA — NOT MARKET DATA
    Every price, volume, supplier premium, site split and seasonal pattern in
    this file is made up for the demo. None of it is shown on Intelligence
    pages; Intelligence stays platform reference only. The rows this writes
    carry the marker too: `Product.custom_attributes.illustrative`,
    `FormulaVersion.notes`, `ActualPrice.source_file` / `ActualVolume.source_file`.

The buyer's own industry, Municipal Water, is declared on every product it
holds (`Product.custom_attributes.buyer_industry = "municipal_water"`): `teams`
has no settings column, and the Strategy pages lead a category's demand
categories with the team's industry (services/strategy.py `team_industry`).

What is NOT made up: which catalogue products the buyer holds (platform
`formula_templates`, looked up by code), their recipes (tracked live from the
catalogue combo at EU), which producers make them (`producer_formulas`), and the
quarterly should-cost index every price follows (`intelligence.derive`, EU).

Each product links to its catalogue card by template id
(`Product.formula_template_id`) and takes its product line from that card
(design §2.8, `effective_lines()`): the seed writes no line, family or
sub-family on the product. Through those lines the products reach the four
playbooks the buyer has adopted; the seed refuses to run if one is not reached.

How a price is generated, per product x supplier x site x quarter:

    actual = base_price x derive_level(q) / 100
                        x (1 + supplier_premium + drift(q))
                        x (1 + noise),   noise uniform in +-1.5% (<= 2%)

seeded from a fixed string per (product, supplier, site, quarter), so a re-run
produces the same numbers and changes nothing.

Base prices, EUR per tonne of product as delivered (DAP), at the catalogue
combo's 2023Q1 anchor. ILLUSTRATIVE magnitudes, not quotes:

    Ferric chloride 40% ........... 300     Caustic soda 50% .............. 400
    PAC 18% Al2O3 ................. 420     Anionic PAM emulsion ......... 2300
    Aluminium sulphate liquid ..... 220     Cationic PAM powder .......... 2900
    Ferric sulphate 41% ........... 240     Activated carbon (coal) ...... 1900
    Hydrochloric acid 33% ......... 180     Sodium hypochlorite .......... 260

The one story the data tells: Kemira's ferric chloride into Rotterdam has
drifted above should-cost over the last four quarters (+3%, +7%, +11%, +15% on
top of its normal premium), while Feralco's ferric chloride into Antwerp keeps
tracking the index. That gap is what Monitor, Negotiate and the coagulants
strategy point at.

Team creation mirrors `routers/teams.py:create_team` (a `Team` row plus an
owner `TeamMembership`), plus what migration 451dfc150b30 gave every existing
team: Owner / Admin / Member roles and a `TeamMemberRole` per member. Role
permissions are copied from the roles existing teams hold (the most common set
per role name), limited to the Dream Plan, with `strategy.view` and
`strategy.edit` on all three (spec §5 Permissions).

Products link to platform templates the way the Cost Model Builder does it
(`loadTemplateIntoModel` + `save`): `Product.formula_template_id` points at the
platform template, the cost model's formula version snapshots the flattened EU
recipe (`flatten_components`) with `margin_type='pct', margin_value=0` because
the catalogue carries margin as a line, and `source_coverage_id` +
`link_mode='tracking'` keep it tracking the catalogue combo. Nothing is forked:
a fork is for editing a recipe, and this buyer does not.

Everything here flushes and never commits; the caller owns the transaction.
Nothing here writes a platform row.
"""
from __future__ import annotations

import random
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.actual_volume import ActualVolume
from app.models.cost_model import CostModel, FormulaComponent, FormulaVersion
from app.models.formula_template import FormulaTemplate
from app.models.price_data import ActualPrice
from app.models.producer import Producer, ProducerFormula
from app.models.product import Product
from app.models.rbac import Permission, Plan, Role, RolePermission, TeamMemberRole
from app.models.strategy import (
    ACTION_STATUSES,
    LEVER_STATUSES,
    OBJECTIVES,
    PRIORITIES,
    Playbook,
    PlaybookLever,
    LeverScore,
    StrategyAction,
    StrategyRecord,
    TeamObjective,
)
from app.models.supplier import Supplier
from app.models.team import Team, TeamMembership
from app.models.user import User
from app.services.drop.report import LoadReport, TableDiff
from app.services.formula_resolver import flatten_components, resolve_coverage
from app.services.intelligence import derive

# ── Identity ─────────────────────────────────────────────────────────────────

TEAM_NAME = "Aquaverde Water Utility (demo)"
OWNER_EMAIL = "alexis@staminachem.com"
PLAN_NAME = "Dream Plan"
# The date the strategy actions are written around. Fixed, not date.today(), so
# a re-run next week changes nothing — and chosen so that on demo day (early
# October 2026) exactly one action is overdue.
DEMO_TODAY = date(2026, 9, 25)

MARKER = "seed_demo_buyer.py (illustrative demo buyer data)"
# The industry this buyer buys for (an `industries.slug`), declared on its
# products: see the module docstring.
BUYER_INDUSTRY = "municipal_water"
ILLUSTRATIVE_NOTE = (
    "Illustrative demo buyer data, generated by seed_demo_buyer.py. "
    "Not a quote and not market data."
)


@dataclass(frozen=True)
class Colleague:
    """A fictional team member. The user row exists without a login: the
    `.example` domain is reserved (RFC 2606), so no Google account can ever
    sign in as one, and `google_id` carries a synthetic value."""
    key: str
    email: str
    display_name: str
    membership_role: str  # admin | member
    title: str


COLLEAGUES: tuple[Colleague, ...] = (
    Colleague("noor", "noor.demo@aquaverde.example", "Noor Demo", "admin",
              "Category manager, water-treatment chemicals"),
    Colleague("pieter", "pieter.demo@aquaverde.example", "Pieter Demo", "member",
              "Process chemist, Rotterdam plant"),
    Colleague("lotte", "lotte.demo@aquaverde.example", "Lotte Demo", "member",
              "Procurement analyst"),
)
GOOGLE_ID_PREFIX = "demo-aquaverde-"


@dataclass(frozen=True)
class DemoBuyerConfig:
    """Who the seed writes as and for. Tests pass their own (a throwaway team
    name, a test owner, unique colleague emails) so they never touch the real
    demo team."""
    team_name: str = TEAM_NAME
    owner_email: str = OWNER_EMAIL
    colleagues: tuple[Colleague, ...] = COLLEAGUES
    plan_name: str = PLAN_NAME
    google_id_prefix: str = GOOGLE_ID_PREFIX


DEFAULT_CONFIG = DemoBuyerConfig()

# ── Sites ────────────────────────────────────────────────────────────────────
# The app has no site entity: a cost model's delivery point is
# `destination_country` (free text, shown as "Supplier → destination" on
# Portfolio / Negotiate / Brief) plus `destination_region`. These two strings
# are the site names the Strategy spend-by-site view groups on.

RTM = "Rotterdam, NL"
ANR = "Antwerp, BE"
SITE_PLACE = {RTM: "Rotterdam", ANR: "Antwerp"}
SITES = (RTM, ANR)

REGION = "Europe"          # app region code (drop code EU)
CURRENCY = "EUR"
INCOTERM = "DAP"
UNIT = "t"

# 12 quarters of actuals, 2023Q3 → 2026Q2 (the drop's last actual month is Jun 2026).
PRICE_QUARTERS: tuple[tuple[int, int], ...] = tuple(
    (y, q) for y in (2023, 2024, 2025, 2026) for q in (1, 2, 3, 4)
    if (2023, 3) <= (y, q) <= (2026, 2)
)

# Seasonal volume factors, Q1..Q4 — illustrative.
SEASON_COAGULANT = (0.94, 1.03, 1.08, 0.95)   # summer raw-water load
SEASON_CAUSTIC = (0.98, 1.01, 1.03, 0.98)
SEASON_POLYMER = (1.05, 0.98, 0.94, 1.03)     # winter sludge volumes
SEASON_CARBON = (0.80, 1.05, 1.30, 0.85)      # taste-and-odour season
SEASON_ACID = (0.97, 1.02, 1.03, 0.98)
SEASON_HYPO = (0.88, 1.04, 1.18, 0.90)        # summer disinfection demand
VOLUME_GROWTH_PER_YEAR = 0.015
VOLUME_NOISE = 0.04
PRICE_NOISE = 0.015


@dataclass(frozen=True)
class SupplyLine:
    """One supplier delivering one product to one site."""
    supplier: str       # a producer name, exactly as the drop's `producers.name`
    site: str
    share: float        # of the product's volume
    premium: float      # over should-cost, before noise — illustrative


@dataclass(frozen=True)
class DemoProduct:
    pid: str                        # platform template code
    name: str
    reference: str                  # Product.formula (the app's "reference" field)
    active_content: float | None    # from the grade stated in the catalogue name
    base_price: float               # EUR/t at 2023Q1 — ILLUSTRATIVE
    volume_per_quarter: float       # t/quarter, all suppliers — ILLUSTRATIVE
    season: tuple[float, float, float, float]
    lines: tuple[SupplyLine, ...]


# Suppliers are producers `producer_formulas` lists for that PID. Where the drop
# lists an EU-region producer it is used; hydrochloric acid and hypochlorite have
# none in the drop, so their suppliers are the listed (NA) producers.
PRODUCTS: tuple[DemoProduct, ...] = (
    DemoProduct("BCI-FECL3-LIQ", "Ferric chloride 40% solution", "FeCl3", 0.40,
                300.0, 900.0, SEASON_COAGULANT, (
                    SupplyLine("Kemira", RTM, 0.62, 0.005),
                    SupplyLine("Feralco Group", ANR, 0.38, 0.030),
                )),
    DemoProduct("BCI-PAC-18", "Polyaluminium chloride (PAC) 18% Al2O3", "PAC", 0.18,
                420.0, 300.0, SEASON_COAGULANT, (
                    SupplyLine("Kemira", RTM, 0.55, 0.005),
                    SupplyLine("Feralco Group", ANR, 0.30, 0.030),
                    SupplyLine("Química del Cinca", ANR, 0.15, 0.040),
                )),
    DemoProduct("BCI-ALUM-LIQ", "Aluminium sulphate liquid", "Al2(SO4)3", 0.09,
                220.0, 360.0, SEASON_COAGULANT, (
                    SupplyLine("Kemira", RTM, 0.60, 0.005),
                    SupplyLine("Kemira", ANR, 0.40, 0.010),
                )),
    DemoProduct("BCI-FE2SO4-LIQ", "Ferric sulphate 41% solution", "Fe2(SO4)3", 0.41,
                240.0, 240.0, SEASON_COAGULANT, (
                    SupplyLine("Kemira", ANR, 0.70, 0.005),
                    SupplyLine("Kronos Worldwide", RTM, 0.30, 0.030),
                )),
    DemoProduct("BCI-NAOH-SOL", "Caustic soda 50% solution", "NaOH", 0.50,
                400.0, 420.0, SEASON_CAUSTIC, (
                    SupplyLine("INEOS", RTM, 0.55, 0.005),
                    SupplyLine("INEOS", ANR, 0.45, 0.010),
                )),
    DemoProduct("PAM-A-EMU", "Anionic polyacrylamide emulsion", "APAM", 0.45,
                2300.0, 12.0, SEASON_POLYMER, (
                    SupplyLine("SNF", RTM, 0.60, 0.005),
                    SupplyLine("Solenis", ANR, 0.40, 0.030),
                )),
    DemoProduct("PAM-C-PWD", "Cationic polyacrylamide powder", "CPAM", 0.90,
                2900.0, 28.0, SEASON_POLYMER, (
                    SupplyLine("SNF", RTM, 0.70, 0.005),
                    SupplyLine("Solenis", ANR, 0.30, 0.030),
                )),
    DemoProduct("ACT-CARB-MIN", "Activated carbon, coal-based", "C", 1.0,
                1900.0, 60.0, SEASON_CARBON, (
                    SupplyLine("Jacobi Carbons", RTM, 0.65, 0.005),
                    SupplyLine("Norit Activated Carbon", ANR, 0.35, 0.030),
                )),
    DemoProduct("BCI-HCL-LIQ", "Hydrochloric acid 33%", "HCl", 0.33,
                180.0, 110.0, SEASON_ACID, (
                    SupplyLine("Westlake Chemical", RTM, 0.60, 0.005),
                    SupplyLine("Olin", ANR, 0.40, 0.030),
                )),
    DemoProduct("CLN-NAOCL-LIQ", "Sodium hypochlorite 12-15%", "NaOCl", 0.12,
                260.0, 240.0, SEASON_HYPO, (
                    SupplyLine("Univar Solutions", RTM, 0.70, 0.005),
                    SupplyLine("Olin", ANR, 0.30, 0.030),
                )),
)

# The story: extra premium over should-cost, by quarter, on one supply line.
DRIFT: dict[tuple[str, str, str], dict[tuple[int, int], float]] = {
    ("BCI-FECL3-LIQ", "Kemira", RTM): {
        (2025, 3): 0.03, (2025, 4): 0.07, (2026, 1): 0.11, (2026, 2): 0.15,
    },
}

# The same supply line carries the app's negotiation flag (Scrum 25), matching
# the in-progress "present the gap to Kemira" action below. Every other cost
# model stays at the default "none".
NEGOTIATION_STATE: dict[tuple[str, str, str], str] = {
    ("BCI-FECL3-LIQ", "Kemira", RTM): "in_negotiation",
}

# ── Strategy state ───────────────────────────────────────────────────────────
# owner is "owner" (the team owner), a colleague key, or None.

STRATEGY_RECORDS: tuple[tuple[str, str, str | None], ...] = (
    ("coagulants", "Active", "owner"),
    ("synthetic_flocculants_pam", "Active", "noor"),
    ("alkalis", "Active", "lotte"),
    ("adsorbents", "Not started", None),
)

# The full 7-objective answer for coagulants, as a PUT of the objectives panel
# would write it. Objectives not listed here are written unselected.
COAGULANT_OBJECTIVES: dict[str, tuple[str, str]] = {
    "cost_reduction": ("High",
                       "Kemira's ferric chloride has run above should-cost for four "
                       "quarters. Close the gap at the next price review."),
    "supply_security": ("High",
                        "Both plants depend on liquid coagulants delivered by road. "
                        "Keep a qualified second supplier for every grade."),
    "sustainability_esg": ("Medium",
                           "Report the carbon footprint of the coagulant spend. Prefer "
                           "by-product iron sources where the grade allows."),
}

# lever_code -> (applies, ease, savings_score, status, notes). None = the
# playbook default stands (the LeverScore column stays NULL).
COAGULANT_LEVER_SCORES: dict[str, tuple] = {
    "coagulants-3c": (True, None, 4, "Approved",
                      "Kemira ferric chloride invoices ran above should-cost for four "
                      "quarters (illustrative demo data). The margin block is where "
                      "that gap sits."),
    "coagulants-3b": (True, None, None, "Approved",
                      "Put an iron index in the ferric contracts and an aluminium "
                      "index in the PAC and alum contracts."),
    "coagulants-4a": (True, None, None, "Approved",
                      "Jar tests at the Rotterdam plant: ferric chloride against PAC."),
    "coagulants-1b": (True, None, None, "Under evaluation",
                      "Rotterdam and Antwerp sit inside one delivery radius. Tender "
                      "both sites together."),
    "coagulants-5a": (True, None, None, "Under evaluation",
                      "Kronos already delivers ferric sulphate to Rotterdam. Use its "
                      "price as the iron-side reference."),
    "coagulants-4b": (True, 3, None, "Under evaluation",
                      "Ask the plants for sludge disposal cost per tonne of coagulant."),
}

# (lever_code, title, description, assignee, start, due, status, pct_complete)
COAGULANT_ACTIONS: tuple[tuple, ...] = (
    ("coagulants-3c", "Present the ferric chloride should-cost gap to Kemira",
     "Share the quarterly should-cost against Kemira's Rotterdam invoices and ask "
     "for the margin line to come back in line.",
     "owner", date(2026, 9, 14), date(2026, 10, 30), "In progress", 40),
    ("coagulants-4a", "Jar tests: ferric chloride against PAC at Rotterdam",
     "Dose both coagulants on the same raw water and compare cost per cubic metre "
     "treated.",
     "pieter", date(2026, 7, 6), date(2026, 9, 15), "In progress", 70),
    ("coagulants-1b", "Align Kemira and Feralco contract end dates",
     "Move both ferric chloride contracts to the same end date so the two sites can "
     "be tendered together.",
     "noor", date(2026, 6, 1), date(2026, 8, 28), "Done", 100),
    ("coagulants-5a", "Request a ferric sulphate reference quote from Kronos",
     "Use a by-product iron price as the reference in the ferric chloride talks.",
     "lotte", date(2026, 10, 5), date(2026, 11, 27), "Not started", 0),
    ("coagulants-4b", "Add sludge disposal cost to the coagulant bid sheet",
     "Waiting for disposal cost per tonne from the plant teams.",
     "pieter", date(2026, 9, 21), date(2026, 12, 11), "Blocked", 10),
)

# ── Roles (spec §5 Permissions; migration 451dfc150b30 for the shape) ─────────

ROLE_DESCRIPTIONS = {
    "Owner": "Full access to all team data and settings",
    "Admin": "Full access except delete operations",
    "Member": "View and export access only",
}
MEMBERSHIP_TO_ROLE = {"owner": "Owner", "admin": "Admin", "member": "Member"}
STRATEGY_KEYS = {
    "Owner": {"strategy.view", "strategy.edit"},
    "Admin": {"strategy.view", "strategy.edit"},
    "Member": {"strategy.view", "strategy.edit"},
}


class DemoSeedError(RuntimeError):
    """The loaded data cannot support the demo buyer (a missing template,
    producer link, playbook or lever). Raised, never papered over."""


@dataclass
class SeedResult:
    team_id: uuid.UUID
    owner_id: uuid.UUID
    colleague_ids: dict[str, uuid.UUID]
    cost_model_ids: dict[tuple[str, str, str], uuid.UUID]
    # (pid, supplier, site) -> [(year, quarter, should_cost, actual)] for the story
    story: dict[tuple[str, str, str], list[tuple[int, int, float, float]]] = field(
        default_factory=dict)


# ── Small helpers ────────────────────────────────────────────────────────────

class _Diff:
    """A TableDiff per table, created on first touch, in touch order."""

    def __init__(self, report: LoadReport):
        self.report = report

    def __call__(self, table: str) -> TableDiff:
        existing = self.report.table(table)
        if existing is not None:
            return existing
        diff = TableDiff(table=table)
        self.report.tables.append(diff)
        return diff


def _dec(value: float, places: int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _same(current, wanted) -> bool:
    if isinstance(current, Decimal) or isinstance(wanted, Decimal):
        if current is None or wanted is None:
            return current is None and wanted is None
        return Decimal(str(current)) == Decimal(str(wanted))
    return current == wanted


def _sync(obj, values: dict) -> bool:
    """Set the fields that differ. True if anything changed."""
    changed = False
    for key, wanted in values.items():
        if not _same(getattr(obj, key), wanted):
            setattr(obj, key, wanted)
            changed = True
    return changed


def _upsert(db: Session, diff: TableDiff, model, key: dict, values: dict):
    row = db.query(model).filter_by(**key).first()
    if row is None:
        row = model(**key, **values)
        db.add(row)
        db.flush()
        diff.created += 1
    elif _sync(row, values):
        db.flush()
        diff.updated += 1
    else:
        diff.unchanged += 1
    return row


def _rng(*parts) -> random.Random:
    # A str seed is hashed with SHA-512 by `random` — stable across runs and
    # interpreters, unlike hash().
    return random.Random("aquaverde|" + "|".join(str(p) for p in parts))


# ── Lookups ──────────────────────────────────────────────────────────────────

def _owner(db: Session, config: DemoBuyerConfig) -> User:
    # Emails compare without case, as provision_user.py and sign-in do.
    owner = db.query(User).filter(
        func.lower(User.email) == config.owner_email.strip().lower()).first()
    if owner is None:
        raise DemoSeedError(f"owner {config.owner_email} does not exist — sign in once first")
    return owner


def find_demo_teams(db: Session, config: DemoBuyerConfig = DEFAULT_CONFIG) -> list[Team]:
    """Demo teams are identified by (name, owner): team names are not unique."""
    owner = _owner(db, config)
    return (
        db.query(Team)
        .filter(Team.name == config.team_name, Team.created_by == owner.id)
        .order_by(Team.created_at)
        .all()
    )


def _platform_template(db: Session, pid: str) -> FormulaTemplate:
    """The platform card a demo product links to. It must be a product card
    (not a group, nor an absorbed, pointer, duplicate or withdrawn one) and
    sit on a product line: the product takes its line from it."""
    template = (
        db.query(FormulaTemplate)
        .filter(FormulaTemplate.code == pid, FormulaTemplate.team_id.is_(None))
        .first()
    )
    if template is None:
        raise DemoSeedError(f"catalogue template {pid} is not loaded — run seed_content_drop.py")
    if template.card_kind != "product" or template.absorbed_into:
        target = template.redirect_to or template.absorbed_into
        raise DemoSeedError(
            f"catalogue template {pid} is a {template.card_kind} card"
            + (f" (it points at {target})" if target else "") + ", not a product")
    if template.product_line_id is None:
        raise DemoSeedError(f"catalogue template {pid} sits on no product line")
    return template


def _producer_country(db: Session, template: FormulaTemplate, supplier: str) -> str | None:
    """The producer's HQ country as the drop gives it — and proof the drop
    lists this producer for this product."""
    row = (
        db.query(ProducerFormula.hq_country)
        .join(Producer, Producer.id == ProducerFormula.producer_id)
        .filter(ProducerFormula.template_id == template.id, Producer.name == supplier,
                Producer.is_bucket.is_(False))
        .first()
    )
    if row is None:
        raise DemoSeedError(
            f"producer_formulas lists no producer {supplier!r} for {template.code}")
    return (row[0] or None) and row[0][:64]


def _reference_role_keys(db: Session, plan_keys: set[str],
                         exclude_team_id: uuid.UUID | None) -> dict[str, set[str]]:
    """The permission set each default role holds on existing teams — the most
    common set per role name, since no Dream-plan team exists to copy from.
    Falls back to the migration's own rule if no team has roles yet."""
    q = (
        db.query(Role.team_id, Role.name, Permission.key)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .filter(Role.team_id.isnot(None), Role.name.in_(list(ROLE_DESCRIPTIONS)))
    )
    if exclude_team_id is not None:
        q = q.filter(Role.team_id != exclude_team_id)
    sets: dict[tuple, set[str]] = defaultdict(set)
    for team_id, name, key in q.all():
        sets[(team_id, name)].add(key)
    by_name: dict[str, Counter] = defaultdict(Counter)
    for (_, name), keys in sets.items():
        by_name[name][frozenset(keys)] += 1

    out: dict[str, set[str]] = {}
    for name in ROLE_DESCRIPTIONS:
        if by_name.get(name):
            keys = set(by_name[name].most_common(1)[0][0])
        elif name == "Owner":
            keys = set(plan_keys)
        elif name == "Admin":
            keys = {k for k in plan_keys if not k.endswith(".delete")}
        else:
            keys = {k for k in plan_keys if k.endswith((".view", ".export"))}
        out[name] = (keys | STRATEGY_KEYS[name]) & plan_keys
    return out


# ── The seed ─────────────────────────────────────────────────────────────────

def seed(db: Session, report: LoadReport | None = None,
         config: DemoBuyerConfig = DEFAULT_CONFIG) -> SeedResult:
    """Create or bring up to date the demo buyer. Idempotent: a second call
    reports zero changes. Flushes, never commits."""
    report = report if report is not None else LoadReport(title="demo buyer")
    diff = _Diff(report)

    owner = _owner(db, config)
    plan = db.query(Plan).filter(Plan.name == config.plan_name).first()
    if plan is None:
        raise DemoSeedError(f"plan {config.plan_name!r} does not exist")

    # Team — what create_team does, on the Dream Plan.
    teams = find_demo_teams(db, config)
    if len(teams) > 1:
        raise DemoSeedError(
            f"{len(teams)} teams named {config.team_name!r} owned by {config.owner_email} — "
            "run with --reset")
    if teams:
        team = teams[0]
        if _sync(team, {"plan_id": plan.id}):
            db.flush()
            diff("teams").updated += 1
        else:
            diff("teams").unchanged += 1
    else:
        team = Team(name=config.team_name, created_by=owner.id, plan_id=plan.id)
        db.add(team)
        db.flush()
        diff("teams").created += 1

    # Fictional colleagues — pre-provisioned user rows, no login.
    colleagues: dict[str, User] = {}
    for c in config.colleagues:
        colleagues[c.key] = _upsert(
            db, diff("users"), User, {"email": c.email},
            {"google_id": f"{config.google_id_prefix}{c.key}",
             "display_name": c.display_name, "company": config.team_name,
             "is_super_admin": False},
        )
    people: dict[str, User] = {"owner": owner, **colleagues}

    members = [(owner, "owner")] + [(colleagues[c.key], c.membership_role)
                                    for c in config.colleagues]
    for user, role in members:
        _upsert(db, diff("team_memberships"), TeamMembership,
                {"user_id": user.id, "team_id": team.id}, {"role": role})

    _seed_roles(db, diff, team, plan, members)

    result = SeedResult(team_id=team.id, owner_id=owner.id,
                        colleague_ids={k: u.id for k, u in colleagues.items()},
                        cost_model_ids={})
    _seed_portfolio(db, diff, team, owner, result)
    _check_adopted_reachable(db, team)
    _seed_strategy(db, diff, team, people)
    db.flush()
    return result


def _check_adopted_reachable(db: Session, team: Team) -> None:
    """Every playbook the buyer adopts must be reached by one of its products
    (product → effective line → market report → playbook), or the Strategy
    landing would show an adopted category with no product and no spend."""
    from app.services.strategy import team_portfolio

    for slug, _status, _owner in STRATEGY_RECORDS:
        if db.get(Playbook, slug) is None:
            raise DemoSeedError(f"playbook {slug!r} is not loaded — run seed_content_drop.py")
    reached = {slug for tp in team_portfolio(db, team.id) for slug in tp.slugs}
    missing = [slug for slug, _status, _owner in STRATEGY_RECORDS if slug not in reached]
    if missing:
        raise DemoSeedError(
            f"no demo product reaches the adopted playbook(s) {', '.join(missing)} "
            "through its product line")


def _seed_roles(db: Session, diff: _Diff, team: Team, plan: Plan,
                members: list[tuple[User, str]]) -> None:
    plan_keys = {p.key for p in plan.permissions}
    wanted = _reference_role_keys(db, plan_keys, exclude_team_id=team.id)
    perm_ids = {p.key: p.id for p in db.query(Permission).all()}

    roles: dict[str, Role] = {}
    for name, description in ROLE_DESCRIPTIONS.items():
        role = _upsert(db, diff("roles"), Role, {"team_id": team.id, "name": name},
                       {"description": description})
        roles[name] = role
        have = {rp.permission_id for rp in
                db.query(RolePermission).filter(RolePermission.role_id == role.id).all()}
        want = {perm_ids[k] for k in wanted[name]}
        rp_diff = diff("role_permissions")
        for pid in want - have:
            db.add(RolePermission(role_id=role.id, permission_id=pid))
            rp_diff.created += 1
        stale = have - want
        if stale:
            db.query(RolePermission).filter(
                RolePermission.role_id == role.id,
                RolePermission.permission_id.in_(stale),
            ).delete(synchronize_session=False)
            rp_diff.deleted += len(stale)
        rp_diff.unchanged += len(have & want)
    db.flush()

    # One default-role assignment per member, mapped from the membership role —
    # the mapping migration 451dfc150b30 applied to every existing member.
    default_role_ids = {r.id for r in roles.values()}
    tmr_diff = diff("team_member_roles")
    for user, membership_role in members:
        target = roles[MEMBERSHIP_TO_ROLE[membership_role]].id
        rows = db.query(TeamMemberRole).filter(
            TeamMemberRole.user_id == user.id, TeamMemberRole.team_id == team.id,
            TeamMemberRole.role_id.in_(default_role_ids),
        ).all()
        if any(r.role_id == target for r in rows):
            tmr_diff.unchanged += 1
        else:
            db.add(TeamMemberRole(user_id=user.id, team_id=team.id, role_id=target))
            tmr_diff.created += 1
        for r in rows:
            if r.role_id != target:
                db.delete(r)
                tmr_diff.deleted += 1
    db.flush()


def _component_values(lines: list[dict]) -> list[dict]:
    """FormulaComponent kwargs for a flattened catalogue recipe — the payload
    CostModelBuilder saves after `loadTemplateIntoModel` (weights are each
    line's share of the recipe, margin included as a line)."""
    total = sum(l["effective_weight_pct"] for l in lines)
    return [
        {
            "label": l["name"][:64],
            "commodity_id": l["commodity_id"],
            "weight": _dec(l["effective_weight_pct"] / total, 4),
            "component_type": l["component_type"],
            "depth": l["depth"],
            "via_template_id": l["via_template_id"],
            "line_region": l["line_region"],
            "is_proxy": l["is_proxy"],
        }
        for l in lines
    ]


_COMPONENT_FIELDS = ("label", "commodity_id", "weight", "component_type", "depth",
                     "via_template_id", "line_region", "is_proxy")


def _sync_components(db: Session, diff: TableDiff, fv: FormulaVersion,
                     wanted: list[dict]) -> None:
    existing = (
        db.query(FormulaComponent)
        .filter(FormulaComponent.formula_version_id == fv.id)
        .order_by(FormulaComponent.id)
        .all()
    )
    same = len(existing) == len(wanted) and all(
        all(_same(getattr(row, f), want[f]) for f in _COMPONENT_FIELDS)
        for row, want in zip(existing, wanted)
    )
    if same:
        diff.unchanged += len(existing)
        return
    for row in existing:
        db.delete(row)
    diff.deleted += len(existing)
    db.flush()
    for want in wanted:
        db.add(FormulaComponent(formula_version_id=fv.id, **want))
    diff.created += len(wanted)
    db.flush()
    db.expire(fv, ["components"])


def _seed_portfolio(db: Session, diff: _Diff, team: Team, owner: User,
                    result: SeedResult) -> None:
    supplier_rows: dict[str, Supplier] = {}

    for spec in PRODUCTS:
        template = _platform_template(db, spec.pid)
        coverage, coverage_region = resolve_coverage(db, template.id, REGION)
        if coverage is None or coverage_region != REGION or coverage.withdrawn_at is not None:
            raise DemoSeedError(f"{spec.pid} has no {REGION} coverage")
        lines = flatten_components(db, template.id, region=REGION)
        if not lines:
            raise DemoSeedError(f"{spec.pid} has no {REGION} recipe lines")

        intel = derive(db, template.id, REGION)
        levels = {(s["year"], s["quarter"]): s["level"] for s in intel.series}
        if not intel.evaluable or (intel.base_year, intel.base_quarter) != (
                coverage.base_year, coverage.base_quarter):
            raise DemoSeedError(f"{spec.pid}: should-cost index not evaluable ({intel.reason})")
        missing = [p for p in PRICE_QUARTERS if p not in levels]
        if missing:
            raise DemoSeedError(f"{spec.pid}: no should-cost level for {missing}")

        product = _upsert(
            db, diff("products"), Product,
            {"team_id": team.id, "formula_template_id": template.id},
            {
                "name": spec.name,
                "formula": spec.reference,
                "active_content": (_dec(spec.active_content, 3)
                                   if spec.active_content is not None else None),
                "unit": UNIT,
                # A linked product's line is its template's (design §2.8);
                # `product_line_id` is only for custom products.
                "product_line_id": None,
                "created_by": owner.id,
                "custom_attributes": {
                    "illustrative": True,
                    "seed": "seed_demo_buyer.py",
                    "catalogue_pid": spec.pid,
                    "buyer_industry": BUYER_INDUSTRY,
                    "note": ILLUSTRATIVE_NOTE,
                },
            },
        )
        components = _component_values(lines)

        for line in spec.lines:
            if line.supplier not in supplier_rows:
                supplier_rows[line.supplier] = _upsert(
                    db, diff("suppliers"), Supplier,
                    {"team_id": team.id, "name": line.supplier},
                    {"country": _producer_country(db, template, line.supplier)},
                )
            else:
                _producer_country(db, template, line.supplier)  # still must be listed
            supplier = supplier_rows[line.supplier]

            cm = _upsert(
                db, diff("cost_models"), CostModel,
                {"team_id": team.id, "product_id": product.id,
                 "supplier_id": supplier.id, "destination_country": line.site},
                {"destination_region": REGION, "region": REGION, "currency": CURRENCY,
                 "incoterm": INCOTERM, "created_by": owner.id,
                 "negotiation_state": NEGOTIATION_STATE.get(
                     (spec.pid, line.supplier, line.site), "none")},
            )
            result.cost_model_ids[(spec.pid, line.supplier, line.site)] = cm.id

            fv = _upsert(
                db, diff("formula_versions"), FormulaVersion,
                {"cost_model_id": cm.id, "base_year": coverage.base_year,
                 "base_quarter": coverage.base_quarter},
                {
                    "base_price": _dec(spec.base_price, 4),
                    "formula_type": "simple",
                    "expression": None,
                    "variables": None,
                    "margin_type": "pct",
                    "margin_value": _dec(0, 4),
                    "incoterm": INCOTERM,
                    "named_place": SITE_PLACE[line.site],
                    "landed_cost_adjustments": None,
                    "notes": (f"{ILLUSTRATIVE_NOTE} Base price is an illustrative "
                              f"{coverage.base_year}Q{coverage.base_quarter} EUR/t; the "
                              f"recipe tracks catalogue combo {spec.pid} · EU."),
                    "source_coverage_id": coverage.id,
                    "link_mode": "tracking",
                },
            )
            _sync_components(db, diff("formula_components"), fv, components)

            drift = DRIFT.get((spec.pid, line.supplier, line.site), {})
            story = []
            prices, volumes = {}, {}
            for (year, quarter) in PRICE_QUARTERS:
                should_cost = spec.base_price * levels[(year, quarter)] / 100.0
                noise = _rng(spec.pid, line.supplier, line.site, year, quarter,
                             "price").uniform(-PRICE_NOISE, PRICE_NOISE)
                actual = should_cost * (1 + line.premium + drift.get((year, quarter), 0.0)) \
                    * (1 + noise)
                prices[(year, quarter)] = _dec(round(actual, 2), 4)
                story.append((year, quarter, round(should_cost, 2), round(actual, 2)))

                years_in = (year - 2023) + (quarter - 3) / 4.0
                vnoise = _rng(spec.pid, line.supplier, line.site, year, quarter,
                              "volume").uniform(-VOLUME_NOISE, VOLUME_NOISE)
                volume = (spec.volume_per_quarter * line.share * spec.season[quarter - 1]
                          * (1 + VOLUME_GROWTH_PER_YEAR * years_in) * (1 + vnoise))
                volumes[(year, quarter)] = _dec(round(volume, 1), 4)
            result.story[(spec.pid, line.supplier, line.site)] = story

            _sync_quarterly(db, diff("actual_prices"), ActualPrice, cm.id, prices, "price",
                            {"incoterm": INCOTERM, "named_place": SITE_PLACE[line.site],
                             "landed_cost_adjustments": None,
                             "source_file": MARKER, "uploaded_by": owner.id})
            _sync_quarterly(db, diff("actual_volumes"), ActualVolume, cm.id, volumes,
                            "volume",
                            {"unit": UNIT, "source_file": MARKER, "uploaded_by": owner.id})


def _sync_quarterly(db: Session, diff: TableDiff, model, cost_model_id: uuid.UUID,
                    values: dict[tuple[int, int], Decimal], field_name: str,
                    fixed: dict) -> None:
    """Make the cost model's quarterly rows exactly `values` (the cost model is
    the seed's own, so a quarter outside the window is removed)."""
    rows = {(r.year, r.quarter): r for r in
            db.query(model).filter(model.cost_model_id == cost_model_id).all()}
    for period, value in values.items():
        row = rows.pop(period, None)
        wanted = {field_name: value, **fixed}
        if row is None:
            db.add(model(cost_model_id=cost_model_id, year=period[0], quarter=period[1],
                         **wanted))
            diff.created += 1
        elif _sync(row, wanted):
            diff.updated += 1
        else:
            diff.unchanged += 1
    for row in rows.values():
        db.delete(row)
        diff.deleted += 1
    db.flush()


def _seed_strategy(db: Session, diff: _Diff, team: Team, people: dict[str, User]) -> None:
    def person(key):
        return people[key].id if key is not None else None

    for slug, status, owner_key in STRATEGY_RECORDS:
        if db.get(Playbook, slug) is None:
            raise DemoSeedError(f"playbook {slug!r} is not loaded — run seed_content_drop.py")
        _upsert(db, diff("strategy_records"), StrategyRecord,
                {"team_id": team.id, "playbook_slug": slug},
                {"status": status, "owner_user_id": person(owner_key)})

    owner_id = people["owner"].id
    for code, _name, _desc in OBJECTIVES:
        priority, note = COAGULANT_OBJECTIVES.get(code, (None, None))
        if priority is not None and priority not in PRIORITIES:
            raise DemoSeedError(f"bad priority {priority}")
        _upsert(db, diff("team_objectives"), TeamObjective,
                {"team_id": team.id, "playbook_slug": "coagulants", "objective_code": code},
                {"selected": code in COAGULANT_OBJECTIVES, "priority": priority,
                 "note": note, "updated_by": owner_id})

    levers = {
        lv.lever_code: lv for lv in db.query(PlaybookLever).filter(
            PlaybookLever.playbook_slug == "coagulants").all()
    }
    wanted_codes = set(COAGULANT_LEVER_SCORES) | {a[0] for a in COAGULANT_ACTIONS}
    missing = wanted_codes - set(levers)
    if missing:
        raise DemoSeedError(f"coagulants levers not loaded: {sorted(missing)}")

    for code, (applies, ease, savings_score, status, notes) in COAGULANT_LEVER_SCORES.items():
        if status not in LEVER_STATUSES:
            raise DemoSeedError(f"bad lever status {status}")
        _upsert(db, diff("lever_scores"), LeverScore,
                {"team_id": team.id, "lever_id": levers[code].id},
                {"applies": applies, "ease": ease, "savings_score": savings_score,
                 "savings_value": None, "status": status, "notes": notes,
                 "updated_by": owner_id})

    for code, title, description, assignee, start, due, status, pct in COAGULANT_ACTIONS:
        if status not in ACTION_STATUSES:
            raise DemoSeedError(f"bad action status {status}")
        _upsert(db, diff("strategy_actions"), StrategyAction,
                {"team_id": team.id, "playbook_slug": "coagulants", "title": title},
                {"lever_id": levers[code].id, "custom_lever_id": None,
                 "description": description, "assignee_user_id": person(assignee),
                 "start_date": start, "due_date": due, "status": status,
                 "pct_complete": pct, "created_by": owner_id})


def overdue_actions(db: Session, team_id: uuid.UUID, today: date = DEMO_TODAY) -> list:
    """Overdue is derived, never stored (models/strategy.py): due before today
    and not Done."""
    return (
        db.query(StrategyAction)
        .filter(StrategyAction.team_id == team_id,
                StrategyAction.due_date < today,
                StrategyAction.status != "Done")
        .all()
    )


# ── Reset ────────────────────────────────────────────────────────────────────

def reset(db: Session, report: LoadReport | None = None,
          config: DemoBuyerConfig = DEFAULT_CONFIG) -> int:
    """Delete the demo team(s) — identified by name AND owner — and the
    fictional colleagues this seed provisioned. The team delete is raw SQL so
    Postgres' ON DELETE CASCADE removes the whole graph (products, cost models,
    prices, volumes, suppliers, roles, memberships, strategy rows); the ORM would
    try to blank the membership primary keys instead. Flushes, never commits.
    Returns the number of teams deleted."""
    report = report if report is not None else LoadReport(title="demo buyer reset")
    diff = _Diff(report)
    teams = find_demo_teams(db, config)
    for team in teams:
        db.execute(text("DELETE FROM teams WHERE id = :tid"), {"tid": str(team.id)})
    diff("teams").deleted += len(teams)

    for c in config.colleagues:
        user = db.query(User).filter(
            User.email == c.email,
            User.google_id == f"{config.google_id_prefix}{c.key}",
        ).first()
        if user is None:
            continue
        still_member = db.query(TeamMembership).filter(
            TeamMembership.user_id == user.id).first()
        if still_member is not None:
            continue  # a member elsewhere — not ours to remove
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(user.id)})
        diff("users").deleted += 1
    db.flush()
    db.expire_all()
    return len(teams)
