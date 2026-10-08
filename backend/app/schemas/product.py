import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    name: str
    formula: str | None = None
    active_content: float | None = None
    unit: str = "kg"
    formula_template_id: uuid.UUID | None = None
    # A custom product's line, picked from the taxonomy (design §2.8). Cleared
    # when the linked catalogue template gives a line: the template wins.
    product_line_id: int | None = None
    custom_attributes: dict | None = None


class ProductUpdate(BaseModel):
    name: str | None = None
    formula: str | None = None
    active_content: float | None = None
    unit: str | None = None
    # For both links, an explicit null unlinks; an absent field leaves it.
    formula_template_id: uuid.UUID | None = None
    product_line_id: int | None = None
    custom_attributes: dict | None = None


class TaxonomyRef(BaseModel):
    """A family, sub-family or product line, by id and display name."""
    id: int
    name: str | None = None


def _resolved(name: str):
    """A field the router fills after validation. Its validation alias names
    no ORM attribute, so `model_validate(orm_row)` never reads (and lazy-loads)
    the relationship of the same name; it serialises under the field name."""
    return Field(default=None, validation_alias=f"resolved_{name}")


class ProductOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    created_by: uuid.UUID
    formula_template_id: uuid.UUID | None = None
    formula_template_code: str | None = None
    formula_template_name: str | None = None
    # The stored manual line (custom products only). The resolved taxonomy is
    # below.
    product_line_id: int | None = None
    # The product's place in the supply taxonomy, resolved on read by
    # `effective_lines()`: its linked template's line, else the template behind
    # one of its cost models, else its manual line. Each is null-safe: a
    # template with no published line gives the family and a null line.
    family: TaxonomyRef | None = _resolved("family")
    subfamily: TaxonomyRef | None = _resolved("subfamily")
    product_line: TaxonomyRef | None = _resolved("product_line")
    # Which path gave the taxonomy: "template", "cost_model", "manual", or
    # null when none did.
    taxonomy_source: str | None = None
    name: str
    formula: str | None
    active_content: float | None
    unit: str
    custom_attributes: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
