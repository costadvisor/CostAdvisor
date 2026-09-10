"""Contract + clause API contract (Wave 3, SCRUM-79 / MON-1).

`notice_deadline` is read-only on the way out and never accepted on the way in:
it is derived from `term_end - notice_days` and stored, so letting a caller set
it independently would let the stored value and its own inputs disagree.
"""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.contract import CLAUSE_TYPES, PRICE_REVIEW_CADENCES


class ClauseIn(BaseModel):
    clause_type: str
    label: str | None = None
    body: str | None = None
    effective_date: date | None = None
    deadline_date: date | None = None
    sort_order: int = 0


class ClauseOut(ClauseIn):
    id: uuid.UUID

    model_config = {"from_attributes": True}


class ContractIn(BaseModel):
    supplier_id: int | None = None
    reference: str | None = Field(default=None, max_length=120)
    term_start: date | None = None
    term_end: date | None = None
    auto_renew: bool = False
    notice_days: int | None = Field(default=None, ge=0, le=3650)
    price_review_cadence: str | None = None
    indexation_formula_version_id: int | None = None
    currency: str | None = Field(default=None, max_length=3)
    notes: str | None = None
    # The cost models this contract covers. Replace-as-a-block on update, the
    # same convention as the weighted-lines editor.
    cost_model_ids: list[uuid.UUID] = []
    clauses: list[ClauseIn] = []


class ContractUpdate(BaseModel):
    supplier_id: int | None = None
    reference: str | None = Field(default=None, max_length=120)
    term_start: date | None = None
    term_end: date | None = None
    auto_renew: bool | None = None
    notice_days: int | None = Field(default=None, ge=0, le=3650)
    price_review_cadence: str | None = None
    indexation_formula_version_id: int | None = None
    currency: str | None = Field(default=None, max_length=3)
    notes: str | None = None
    cost_model_ids: list[uuid.UUID] | None = None
    clauses: list[ClauseIn] | None = None


class CoveredCostModel(BaseModel):
    cost_model_id: uuid.UUID
    product: str | None = None
    share_pct: float | None = None


class ContractOut(BaseModel):
    id: uuid.UUID
    supplier_id: int | None = None
    supplier_name: str | None = None
    reference: str | None = None
    term_start: date | None = None
    term_end: date | None = None
    auto_renew: bool
    notice_days: int | None = None
    # Derived and stored; never settable.
    notice_deadline: date | None = None
    days_to_notice: int | None = None
    price_review_cadence: str | None = None
    indexation_formula_version_id: int | None = None
    currency: str | None = None
    notes: str | None = None
    clauses: list[ClauseOut] = []
    covered: list[CoveredCostModel] = []
    created_at: datetime


VALID_CLAUSE_TYPES = set(CLAUSE_TYPES)
VALID_CADENCES = set(PRICE_REVIEW_CADENCES)
