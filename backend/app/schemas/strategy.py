"""Strategy API contract (design §2.6 and §4.2 "Strategy"; demo spec §6 and §9).

A team's category strategy is an **overlay** on a platform playbook: every
response merges the authored defaults with the team's own rows, and says which
is which (`overridden`, `custom`, `authored`) so a page can show "reset to the
playbook" without re-deriving the merge.

Vocabulary is stored as codes and returned with display names alongside
(`gemstone` + `gemstone_name`, objective `code` + `type`), per §9.

Write bodies are partial: a field left out is not touched. On a lever score an
explicit `null` means "back to the playbook default".

Every write body refuses a NUL character (`\\x00`) in any text field with a
422: Postgres text cannot hold one, and psycopg2 raises before the query is
sent, which would otherwise surface as a 500.
"""
import math
import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, field_validator


# ── Shared bits ──────────────────────────────────────────────────────────────

class WriteBody(BaseModel):
    """Base for request bodies: no NUL character in any str (or list of str)
    field. Runs after type validation, so non-text fields pass untouched."""

    @field_validator("*")
    @classmethod
    def _no_nul(cls, value):
        items = value if isinstance(value, list) else [value]
        if any(isinstance(v, str) and "\x00" in v for v in items):
            raise ValueError("must not contain a NUL character")
        return value


class UserRef(BaseModel):
    id: uuid.UUID
    name: str


class Kraljic(BaseModel):
    """As authored on the playbook: `{cx, cy, badge, label, boundary}` in the
    mockup's 260×260 chart space."""
    cx: float | None = None
    cy: float | None = None
    badge: str | None = None
    label: str | None = None
    boundary: bool | None = None


class ProductRef(BaseModel):
    product_id: uuid.UUID
    pid: str | None = None       # catalogue code (formula_templates.code)
    name: str                    # the team's own product name


class ObjectiveRef(BaseModel):
    code: str
    name: str


class FxGap(BaseModel):
    """A currency and quarter with no FX rate: the value was used unconverted."""
    currency: str
    period: str


class TeamIndustry(BaseModel):
    """The industry the team buys for (services/strategy.py, "The team's own
    industry")."""
    slug: str
    name: str
    # "declared": the team's products name it (`custom_attributes.buyer_industry`);
    # "inferred": the industry with the most servable-category placements of
    # the team's catalogue products.
    source: str


# ── Landing ──────────────────────────────────────────────────────────────────

class CategoryRow(BaseModel):
    playbook_slug: str
    name: str
    family: str | None = None
    adopted: bool
    status: str
    kraljic: Kraljic | None = None
    has_report: bool
    annual_spend: float
    currency: str
    # True when part of `annual_spend` had no FX rate into `currency` and was
    # summed unconverted.
    fx_incomplete: bool = False
    open_opportunities: int
    actions_in_progress: int
    actions_overdue: int
    owner: UserRef | None = None
    last_updated: date | None = None
    playbook_last_updated: date | None = None
    products: list[ProductRef]


class CategoriesOut(BaseModel):
    team_id: uuid.UUID
    currency: str
    illustrative: bool
    team_industry: TeamIndustry | None = None
    adopted: list[CategoryRow]
    suggested: list[CategoryRow]
    # Currencies / quarters with no rate into `currency` (as on /spend): the
    # spend they touch was summed unconverted. Usually [].
    fx_gaps: list[FxGap] = []
    # True when fx_gaps is not empty: some row's `annual_spend` is incomplete.
    fx_incomplete: bool = False


class AdoptOut(BaseModel):
    created: bool
    category: CategoryRow


class RecordUpdate(WriteBody):
    status: str | None = None
    owner_user_id: uuid.UUID | None = None


# ── Category detail ─────────────────────────────────────────────────────────

class RecordOut(BaseModel):
    status: str
    owner: UserRef | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LineOut(BaseModel):
    """A product line the playbook's report is written for. A report key
    that is no current line is one entry, `published` false, named "Product
    line not yet published", with no id or key (design §4.1)."""
    product_line_id: int | None = None
    line_key: str | None = None
    name: str
    family_id: int | None = None
    subfamily_id: int | None = None      # the line's sub-family
    published: bool
    source: str                          # report_map | key_map


class DemandCategoryOut(BaseModel):
    industry: str
    industry_slug: str
    code: str
    name: str
    fn: str | None = None
    status: str
    pids: list[str]
    # The subset of `pids` this team buys.
    team_pids: list[str]


class TeamProductOut(BaseModel):
    product_id: uuid.UUID
    name: str
    pid: str | None = None
    template_name: str | None = None
    # The product's effective line (template, cost-model template, or the
    # manual line of a custom product); None when it has none published.
    product_line_id: int | None = None
    line_key: str | None = None
    line_name: str | None = None
    taxonomy_source: str = "none"        # template | cost_model | manual | none
    cost_model_count: int


class CatalogueProductOut(BaseModel):
    """A listed catalogue product (design §2.3) on one of the lines."""
    pid: str
    name: str
    product_line_id: int | None = None
    line_key: str | None = None
    supply_status: str | None = None


class AuthoredObjective(BaseModel):
    priority: str | None = None
    note: str | None = None


class ObjectiveOut(BaseModel):
    code: str
    type: str                    # display name ("Cost Reduction")
    desc: str | None = None
    selected: bool
    priority: str | None = None
    note: str | None = None
    # The playbook's authored pre-fill, whatever the team did with it.
    authored: AuthoredObjective | None = None
    # True once the team has saved its own answer for this objective.
    overridden: bool


class GemstoneCount(BaseModel):
    code: str
    name: str
    colour: str | None = None
    count: int
    applying: int


class CategoryDetailOut(BaseModel):
    playbook_slug: str
    name: str
    family: str | None = None
    report_slug: str | None = None
    playbook_last_updated: date | None = None
    kraljic: Kraljic | None = None
    adopted: bool
    record: RecordOut | None = None
    lines: list[LineOut]
    # Leads `demand_categories`: that industry's rows come first.
    team_industry: TeamIndustry | None = None
    demand_categories: list[DemandCategoryOut]
    team_products: list[TeamProductOut]
    catalogue_products: list[CatalogueProductOut]
    objectives: list[ObjectiveOut]
    gemstones: list[GemstoneCount]
    lever_count: int


# ── Strategic analysis ───────────────────────────────────────────────────────

class CaveatLine(BaseModel):
    # None for a line not yet published: its stored key is never served.
    line_key: str | None = None
    product_line_id: int | None = None
    name: str
    visible: bool


class Caveat(BaseModel):
    """The same caveat `/api/intel/reports/{slug}` carries (REP-3)."""
    as_of: str | None = None             # "2026-08"
    text: str
    old_line_name: str
    split_into_n_lines: int
    lines: list[CaveatLine]


class Panel(BaseModel):
    heading: str | None = None
    html: str


class PestelCard(BaseModel):
    factor: str
    text: str
    implication: str


class PestelPanel(Panel):
    columns: list[str] = []
    cards: list[PestelCard] = []


class PorterRow(BaseModel):
    force: str
    question: str | None = None
    assessment: str
    implication: str


class PorterPanel(Panel):
    columns: list[str] = []
    rows: list[PorterRow] = []
    # A section-level "adding it up" line. No delivered report carries one
    # (each row carries its own buyer implication), so this is null today.
    implication: str | None = None


class DriverCard(BaseModel):
    title: str
    arrow: str | None = None
    text: str


class DriversPanel(Panel):
    cards: list[DriverCard] = []


class KraljicPanel(Panel):
    cx: float | None = None
    cy: float | None = None
    badge: str | None = None
    label: str | None = None
    boundary: bool | None = None
    quadrant: str | None = None
    lead: str | None = None
    note: str | None = None
    narrative: list[str] = []


class AnalysisPanels(BaseModel):
    overview: Panel | None = None
    how: Panel | None = None
    applications: Panel | None = None
    tech: Panel | None = None
    process: Panel | None = None
    supply: Panel | None = None
    pestel: PestelPanel | None = None
    porter: PorterPanel | None = None
    market_drivers: DriversPanel | None = None
    kraljic: KraljicPanel | None = None
    outlook: Panel | None = None


class AnalysisOut(BaseModel):
    playbook_slug: str
    name: str
    family: str | None = None
    report_slug: str | None = None
    available: bool
    report_name: str | None = None
    as_of: str | None = None             # "2026-08"
    caveat: Caveat | None = None
    panel_order: list[str]
    panels: AnalysisPanels
    # The playbook's Kraljic even when there is no report to carry a panel.
    kraljic: Kraljic | None = None


# ── Spend ────────────────────────────────────────────────────────────────────

class SpendSlice(BaseModel):
    key: str
    name: str | None = None
    value: float
    pct: float


class ProductSpend(SpendSlice):
    product_id: uuid.UUID
    pid: str | None = None
    has_volume: bool


class SupplierSpend(SpendSlice):
    supplier_id: int | None = None


class SiteSpend(SpendSlice):
    region: str | None = None


class Concentration(BaseModel):
    suppliers: int
    top_supplier: str | None = None
    top_share_pct: float
    top2_share_pct: float | None = None
    hhi: int
    level: str       # unconcentrated / moderately concentrated / highly concentrated


class EvolutionPoint(BaseModel):
    period: str
    year: int
    quarter: int
    should_cost_index: float | None = None
    actual_price_index: float | None = None


class SpendOut(BaseModel):
    playbook_slug: str
    currency: str
    total: float
    has_spend: bool
    window: str
    by_product: list[ProductSpend]
    by_supplier: list[SupplierSpend]
    by_site: list[SiteSpend]
    concentration: Concentration | None = None
    concentration_note: str | None = None
    evolution_base: str | None = None
    evolution: list[EvolutionPoint]
    illustrative: bool
    fx_gaps: list[FxGap] = []
    # True when fx_gaps is not empty: `total` and the slices include money
    # summed unconverted.
    fx_incomplete: bool = False


# ── Levers ───────────────────────────────────────────────────────────────────

class LeverDefaults(BaseModel):
    applies: bool | None = None
    ease: int | None = None
    savings_score: int | None = None
    status: str | None = None
    notes: str | None = None


class LeverOut(BaseModel):
    number: int
    # Exactly one of the two ids is set: `lever_id` for an authored lever
    # (PUT /strategy/levers/{lever_id}), `custom_lever_id` for a team's own
    # (PUT/DELETE /strategy/custom-levers/{id}).
    lever_id: int | None = None
    custom_lever_id: uuid.UUID | None = None
    code: str | None = None
    gemstone: str
    gemstone_name: str
    gemstone_colour: str | None = None
    title: str
    guidance: str | None = None
    applies: bool | None = None
    ease: int | None = None
    savings_score: int | None = None
    savings_value: float | None = None
    status: str | None = None
    notes: str | None = None
    objectives: list[ObjectiveRef]
    scales: list | None = None
    custom: bool
    overridden: bool
    plotted: bool
    actions_count: int
    # The playbook's authored values (authored levers only).
    defaults: LeverDefaults | None = None


class GemstoneGroup(BaseModel):
    code: str
    name: str
    colour: str | None = None
    count: int
    applying: int
    levers: list[LeverOut]


class LeversOut(BaseModel):
    playbook_slug: str
    total: int
    applying: int
    plotted: int
    gemstones: list[GemstoneGroup]
    levers: list[LeverOut]


# `savings_value` is money the team expects to save on the lever, in the
# team's currency: not negative, finite, and inside the column's
# Numeric(14, 2). Out of range is a 422, not a database error.
SAVINGS_VALUE_MAX = 999_999_999_999.99


def _non_finite_as_text(v):
    # A NaN / Infinity literal in the body would be echoed as `input` in the
    # 422 detail, which JSON cannot encode (FastAPI then answers 500). Passed
    # on as text, the float check refuses it and the 422 says "nan".
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)
    return v


SavingsValue = Annotated[
    float,
    Field(ge=0, le=SAVINGS_VALUE_MAX, allow_inf_nan=False),
    BeforeValidator(_non_finite_as_text),
]


class LeverScoreUpdate(WriteBody):
    applies: bool | None = None
    ease: int | None = Field(default=None, ge=1, le=5)
    savings_score: int | None = Field(default=None, ge=1, le=5)
    savings_value: SavingsValue | None = None
    status: str | None = None
    notes: str | None = None


class CustomLeverCreate(WriteBody):
    # Code ("VC") or display name ("Volume Concentration").
    gemstone: str
    title: str = Field(min_length=1)
    guidance: str | None = None
    applies: bool | None = True
    ease: int | None = Field(default=None, ge=1, le=5)
    savings_score: int | None = Field(default=None, ge=1, le=5)
    savings_value: SavingsValue | None = None
    status: str | None = "Identified"
    notes: str | None = None
    objectives: list[str] = []


class CustomLeverUpdate(WriteBody):
    gemstone: str | None = None
    title: str | None = Field(default=None, min_length=1)
    guidance: str | None = None
    applies: bool | None = None
    ease: int | None = Field(default=None, ge=1, le=5)
    savings_score: int | None = Field(default=None, ge=1, le=5)
    savings_value: SavingsValue | None = None
    status: str | None = None
    notes: str | None = None
    objectives: list[str] | None = None


# ── Objectives ───────────────────────────────────────────────────────────────

class ObjectiveIn(WriteBody):
    code: str
    selected: bool
    priority: str | None = None
    note: str | None = None


class ObjectivesOut(BaseModel):
    playbook_slug: str
    objectives: list[ObjectiveOut]


# ── Actions ──────────────────────────────────────────────────────────────────

class ActionLeverRef(BaseModel):
    number: int | None = None
    title: str
    code: str | None = None
    gemstone: str
    gemstone_name: str
    custom: bool


class ActionOut(BaseModel):
    id: uuid.UUID
    playbook_slug: str
    title: str
    description: str | None = None
    lever_id: int | None = None
    custom_lever_id: uuid.UUID | None = None
    lever: ActionLeverRef | None = None
    assignee_user_id: uuid.UUID | None = None
    assignee: UserRef | None = None
    start_date: date | None = None
    due_date: date | None = None
    status: str
    pct_complete: int
    overdue: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ActionCounts(BaseModel):
    total: int
    not_started: int
    in_progress: int
    blocked: int
    done: int
    overdue: int


class ActionsOut(BaseModel):
    playbook_slug: str
    today: date
    counts: ActionCounts
    actions: list[ActionOut]


class ActionCreate(WriteBody):
    title: str = Field(min_length=1)
    description: str | None = None
    lever_id: int | None = None
    custom_lever_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    start_date: date | None = None
    due_date: date | None = None
    status: str = "Not started"
    pct_complete: int = Field(default=0, ge=0, le=100)


class ActionUpdate(WriteBody):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    lever_id: int | None = None
    custom_lever_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    start_date: date | None = None
    due_date: date | None = None
    status: str | None = None
    pct_complete: int | None = Field(default=None, ge=0, le=100)
