import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Weights must sum to exactly 100 per template; tolerance absorbs float noise
# from the UI, not real imbalance.
WEIGHT_SUM_TOLERANCE = 0.01


class FormulaTemplateCreate(BaseModel):
    team_id: uuid.UUID | None = None
    name: str
    description: str | None = None
    # Optional since Scrum 58: a template can be purely weighted components.
    expression: str | None = None
    variables: dict | None = None


class FormulaTemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    expression: str | None = None
    variables: dict | None = None


class FormulaTemplateForkRequest(BaseModel):
    team_id: uuid.UUID


class FormulaTemplateOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID | None
    origin_id: uuid.UUID | None = None
    created_by: uuid.UUID
    creator_email: str | None = None
    name: str
    code: str | None = None
    family_id: int | None = None
    subfamily_id: int | None = None
    family_code: str | None = None
    family_name: str | None = None
    subfamily_name: str | None = None
    catalog_meta: dict | None = None
    # SCRUM-78 rollup across this template's coverage rows. The grade is stored
    # per (template, region) because trust is a property of a *combo*, not of a
    # recipe — but the catalog list renders one row per template, so the worst
    # grade and the review count are rolled up here rather than making the page
    # fetch every combo. `catalog_meta.data_confidence` is NOT a substitute: the
    # July sheet dropped that column, so it is null on everything loaded since.
    trust_summary: dict | None = None
    description: str | None
    expression: str | None
    variables: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Weighted components (Scrum 58) ────────────────────────────────────────────

class FormulaComponentIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    component_type: Literal["index", "fixed", "formula"]
    commodity_id: int | None = None
    input_template_id: uuid.UUID | None = None
    # Signed percent (a by-product credit can be negative).
    weight_pct: float
    is_proxy: bool = False
    sort_order: int = 0

    @model_validator(mode="after")
    def _check_target_coherence(self):
        if self.component_type == "index" and self.commodity_id is None:
            raise ValueError("an 'index' component requires commodity_id")
        if self.component_type == "formula" and self.input_template_id is None:
            raise ValueError("a 'formula' component requires input_template_id")
        if self.component_type == "index" and self.input_template_id is not None:
            raise ValueError("an 'index' component cannot carry input_template_id")
        if self.component_type == "formula" and self.commodity_id is not None:
            raise ValueError("a 'formula' component cannot carry commodity_id")
        if self.component_type == "fixed" and (
            self.commodity_id is not None or self.input_template_id is not None
        ):
            raise ValueError("a 'fixed' component carries no index or formula reference")
        return self


class FormulaComponentsReplace(BaseModel):
    """Replace-all payload: weighted lines are edited as a block."""
    components: list[FormulaComponentIn]

    @model_validator(mode="after")
    def _check_weights_sum(self):
        if self.components:
            total = sum(c.weight_pct for c in self.components)
            if abs(total - 100.0) > WEIGHT_SUM_TOLERANCE:
                raise ValueError(
                    f"component weights must sum to 100 (got {total:g})"
                )
        return self


class FormulaComponentOut(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    name: str
    component_type: str
    commodity_id: int | None
    input_template_id: uuid.UUID | None
    region: str | None = None
    weight_pct: float
    is_proxy: bool
    sort_order: int

    model_config = {"from_attributes": True}


# ── Per-(formula x region) coverage (Scrum 58) ───────────────────────────────

class FormulaCoverageIn(BaseModel):
    base_price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    margin_pct: float | None = Field(default=None, ge=-100, le=100)
    base_year: int | None = Field(default=None, ge=2000, le=2100)
    base_quarter: int | None = Field(default=None, ge=1, le=4)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v):
        return v.upper() if v else v

    @model_validator(mode="after")
    def _base_period_pairs(self):
        if (self.base_year is None) != (self.base_quarter is None):
            raise ValueError("base_year and base_quarter must be set together")
        return self


class FormulaCoverageOut(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    region: str
    base_price: float | None
    currency: str | None
    margin_pct: float | None
    base_year: int | None
    base_quarter: int | None
    # Legacy. The July sheet dropped the column it came from, so this is None on
    # everything loaded since — and it no longer drives `needs_review`.
    data_confidence: str | None = None
    # Two coverage columns, two questions, and the grade below is neither of
    # them: coverage is an *input* to the grade.
    coverage_tier: str | None = None
    proxy_density_tier: str | None = None
    needs_review: bool = False
    # The reviewer's display identity, resolved from the FK — so it still
    # resolves after they change their email. `reviewed_by` is the legacy
    # free-text value, kept for sign-offs that predate the FK.
    reviewed_by: str | None = None
    reviewed_by_id: uuid.UUID | None = None
    reviewed_by_name: str | None = None
    reviewed_at: datetime | None = None
    review_metadata: dict | None = None
    # ── The derived trust state (SCRUM-78) ──────────────────────────────────
    trust_grade: str | None = None
    # Names the type-codes and lines that pulled the grade down — an ungraded
    # "low" tells a reviewer nothing about what to go and look at.
    trust_inputs: dict | None = None
    trust_computed_at: datetime | None = None
    # The customer-facing caveat for this grade, so the text has one source.
    trust_caveat: str | None = None
    # Present once signed off; a mismatch against the live recipe is what
    # returns the combo to the queue.
    review_fingerprint: str | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class TrustQueueRow(BaseModel):
    template_id: uuid.UUID
    template_code: str | None = None
    template_name: str | None = None
    region: str
    variant: str = ""
    scope: str
    trust_grade: str | None = None
    needs_review: bool = False
    trust_inputs: dict | None = None
    coverage_tier: str | None = None
    proxy_density_tier: str | None = None
    reviewed_at: datetime | None = None
    reviewed_by_id: uuid.UUID | None = None
    # Resolved from the FK on read, never stored — a copied email decays the
    # moment the reviewer changes their address.
    reviewed_by_name: str | None = None
    # The line set the sign-off was pinned to. Change a weight and the combo
    # returns to the queue; a reorder does not, because sort_order is
    # presentation and re-queueing on it would train reviewers to ignore the flag.
    review_fingerprint: str | None = None


class TrustQueueOut(BaseModel):
    total: int
    # Worst first by default: a queue ordered by region or name would have a
    # reviewer reading alphabetically through something whose whole point is
    # triage.
    order_by: str
    rows: list[TrustQueueRow] = []


class TrustRecomputeOut(BaseModel):
    considered: int
    graded: int
    # Sign-offs cleared because the reviewed inputs moved.
    invalidated: int
    by_grade: dict[str, int] = {}


# ── Resolver output (Scrum 58) ───────────────────────────────────────────────

class ResolvedLineOut(BaseModel):
    component_id: uuid.UUID
    name: str
    component_type: str
    commodity_id: int | None
    commodity_name: str | None = None
    weight_pct: float
    effective_weight_pct: float
    is_proxy: bool
    depth: int
    via_template_id: uuid.UUID
    via_template_name: str | None = None
    # Region whose seeded line set this line came from (None = the template-
    # level / API-authored set) — trust signal for the reader.
    line_region: str | None = None


class FormulaResolveOut(BaseModel):
    template_id: uuid.UUID
    region_requested: str
    region_resolved: str | None
    coverage: FormulaCoverageOut | None
    lines: list[ResolvedLineOut]


# ── Evaluation output (weighted should-cost) ─────────────────────────────────

class EvaluatedLineOut(ResolvedLineOut):
    commodity_name: str | None = None
    base_value: float | None = None
    current_value: float | None = None
    ratio: float
    has_data: bool
    # Share of the rebased index level / absolute money this line explains;
    # abs contributions sum exactly to the should-cost.
    contribution_pct: float
    contribution_abs: float | None = None


class FormulaEvaluateOut(BaseModel):
    template_id: uuid.UUID
    region_requested: str
    coverage_region: str | None
    year: int
    quarter: int
    evaluable: bool
    reason: str | None = None
    base_price: float | None = None
    currency: str | None = None
    base_year: int | None = None
    base_quarter: int | None = None
    margin_pct: float | None = None
    # 100.0 at the base period by construction (rebased to the recipe's own
    # weight sum); should_cost = base_price × index_level/100.
    index_level_pct: float | None = None
    should_cost: float | None = None
    lines: list[EvaluatedLineOut] = []
    data_gaps: list[dict] = []
