"""Response shapes for the recipe-line audit API (Jira 208)."""
from datetime import date

from pydantic import BaseModel


class AuditExampleOut(BaseModel):
    template_code: str | None = None
    region: str | None = None
    line_name: str
    weight_pct: float
    reason: str


class AuditStatusOut(BaseModel):
    lines: int
    recipes: int
    weight_pct_of_all: float
    examples: list[AuditExampleOut]


class AuditSummaryOut(BaseModel):
    as_of: date
    lines: int
    recipes: int
    meets_pct_of_index_weight: float | None = None
    proxy_unclassified_lines: int
    templates_without_product_line: list[str]
    by_status: dict[str, AuditStatusOut]   # every status, most serious first


class LineAuditOut(BaseModel):
    template_id: str
    template_code: str | None = None
    region: str | None = None
    variant: str
    line_name: str
    component_type: str
    cost_category: str | None = None
    weight_pct: float
    status: str
    reason: str
    type_code: str | None = None
    commodity_id: int | None = None
    commodity_key: str | None = None
    series_region: str | None = None
    series_last_actual: str | None = None
    proxy_status: str | None = None


class LineAuditPageOut(BaseModel):
    as_of: date
    total: int                         # lines matching the filters, before paging
    lines: list[LineAuditOut]
