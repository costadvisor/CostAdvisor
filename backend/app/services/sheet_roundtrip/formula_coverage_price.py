"""FormulaRegionCoverage base-price payload spec (Scrum 27b).

The one registered sheet-roundtrip payload: catalog combo pricing
(base_price/currency/margin_pct/base_year/base_quarter), scoped by family/
subfamily/needs_review — the ticket's own example scenario ("export the
unreviewed blocks for one subfamily") maps directly onto this filter shape.

Validation mirrors services/file_parser.py's parse_coverage_price_upload
exactly (currency 3-letter uppercase, base_price non-negative, base_quarter
1-4) so the two entry points agree on what a valid value looks like.
"""
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.formula_template import FormulaRegionCoverage, FormulaTemplate
from app.services.sheet_roundtrip.base import SheetColumnSpec, SheetPayloadSpec


class FormulaCoveragePriceFilter(BaseModel):
    subfamily_id: int | None = None
    needs_review: bool | None = None


def _parse_str(v) -> str:
    s = str(v).strip()
    if not s:
        raise ValueError("must not be empty")
    return s


def _parse_price(v) -> float:
    price = float(v)
    if price < 0:
        raise ValueError("must be non-negative")
    return price


def _parse_currency(v) -> str:
    ccy = str(v).strip().upper()
    if len(ccy) != 3:
        raise ValueError("currency must be a 3-letter code")
    return ccy


def _parse_float(v) -> float:
    return float(v)


def _parse_year(v) -> int:
    year = int(float(v))
    if not (2000 <= year <= 2100):
        raise ValueError("base_year out of range")
    return year


def _parse_quarter(v) -> int:
    q = int(float(v))
    if q not in (1, 2, 3, 4):
        raise ValueError("base_quarter must be 1-4")
    return q


def _passthrough(v):
    return v


class FormulaCoveragePriceSpec(SheetPayloadSpec):
    key = "formula_coverage_price"
    sheet_name = "Coverage Prices"
    permission_key = "formulas.edit"
    filter_schema = FormulaCoveragePriceFilter

    columns = [
        SheetColumnSpec("code", "key", "Formula Code", _parse_str),
        SheetColumnSpec("region", "key", "Region", _parse_str),
        SheetColumnSpec("name", "readonly", "Formula Name", _parse_str),
        SheetColumnSpec("base_price", "editable", "Base Price", _parse_price),
        SheetColumnSpec("currency", "editable", "Currency", _parse_currency),
        SheetColumnSpec("margin_pct", "editable", "Margin Pct", _parse_float),
        SheetColumnSpec("base_year", "editable", "Base Year", _parse_year),
        SheetColumnSpec("base_quarter", "editable", "Base Quarter", _parse_quarter),
        SheetColumnSpec("data_confidence", "readonly", "Data Confidence", _passthrough),
        SheetColumnSpec("coverage_tier", "readonly", "Coverage Tier", _passthrough),
        SheetColumnSpec("needs_review", "readonly", "Needs Review", _passthrough),
    ]

    def query_rows(self, db: Session, filter_spec: FormulaCoveragePriceFilter) -> list[dict]:
        q = (
            db.query(FormulaRegionCoverage)
            .join(FormulaTemplate, FormulaRegionCoverage.template_id == FormulaTemplate.id)
            .filter(FormulaTemplate.team_id.is_(None))  # platform catalog only
        )
        if filter_spec.subfamily_id is not None:
            q = q.filter(FormulaTemplate.subfamily_id == filter_spec.subfamily_id)
        if filter_spec.needs_review is not None:
            q = q.filter(FormulaRegionCoverage.needs_review == filter_spec.needs_review)

        rows = []
        for cov in q.all():
            rows.append({
                "code": cov.template.code,
                "region": cov.region,
                "name": cov.template.name,
                "base_price": _row_value(cov, "base_price"),
                "currency": cov.currency,
                "margin_pct": _row_value(cov, "margin_pct"),
                "base_year": cov.base_year,
                "base_quarter": cov.base_quarter,
                "data_confidence": cov.data_confidence,
                "coverage_tier": cov.coverage_tier,
                "needs_review": cov.needs_review,
            })
        return rows

    def _find(self, db: Session, row_key: dict) -> FormulaRegionCoverage | None:
        template = db.query(FormulaTemplate).filter(
            FormulaTemplate.code == row_key["code"], FormulaTemplate.team_id.is_(None)
        ).first()
        if not template:
            return None
        return db.query(FormulaRegionCoverage).filter(
            FormulaRegionCoverage.template_id == template.id,
            FormulaRegionCoverage.region == row_key["region"],
        ).first()

    def apply_change(self, db: Session, row_key: dict, column: str, value) -> None:
        cov = self._find(db, row_key)
        if cov is None:
            raise ValueError(f"No combo for {row_key} — cannot apply a change to a row that no longer exists")
        setattr(cov, column, value)

    def get_current_value(self, db: Session, row_key: dict, column: str):
        cov = self._find(db, row_key)
        if cov is None:
            return None
        # Same Decimal->float conversion query_rows applies — get_current_value's
        # result is stringified and compared against a diff's old_value (itself
        # computed from query_rows' output) to detect a concurrent edit; a
        # representation mismatch (Decimal("100.0000") vs float 100.0 stringify
        # differently) would falsely read as "changed since diff" every time.
        return _row_value(cov, column) if column in ("base_price", "margin_pct") else getattr(cov, column)


def _row_value(cov: FormulaRegionCoverage, column: str):
    value = getattr(cov, column)
    return float(value) if value is not None else None
