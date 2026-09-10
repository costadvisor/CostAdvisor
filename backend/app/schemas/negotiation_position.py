import uuid

from pydantic import BaseModel

from app.schemas.formula_template import EvaluatedLineOut, FormulaEvaluateOut


class AttributedLineOut(EvaluatedLineOut):
    # Always 0.0 / None today — no supplier-cost data and no editorial
    # evidence model (negotiation_note / index dossier) exist in this repo.
    # should_cost already consumes 100% of every line's verified movement by
    # construction, so re-crediting a line here would double-count it; these
    # fields are the seam a future evidence source would populate without
    # breaking the attributed+unexplained==ask identity.
    attributed_amount: float = 0.0
    evidence: str | None = None


class NegotiationNormalizationOut(BaseModel):
    supplier_price_raw: float
    # None only when neither side ever declared a currency at all (an
    # uncommon combo with no coverage.currency and no supplier_currency arg).
    supplier_currency: str | None = None
    supplier_unit: str | None = None
    supplier_incoterm: str | None = None
    normalized_price: float
    fx_rate_used: float | None = None
    unit_factor_used: float | None = None
    incoterm_adjustment: float | None = None
    notes: list[str] = []


class NegotiationPositionOut(BaseModel):
    insufficient: bool
    reason: str | None = None
    ask: float | None = None
    attributed_components: list[AttributedLineOut] = []
    attributed_total: float = 0.0
    unexplained_remainder: float | None = None


class NegotiationResponseOut(BaseModel):
    template_id: uuid.UUID
    region_requested: str
    year: int
    quarter: int
    target: FormulaEvaluateOut
    normalization: NegotiationNormalizationOut | None = None
    position: NegotiationPositionOut
