"""Resolving live-number tokens to data (Jira 210 / LIVE-3, S2).

`resolve(db, tokens)` answers every token of a document in a fixed number of
queries (series, health, monthly values, recipe lines), whatever the count.

Index tokens read the monthly series — the store the content drop writes and
LIVE-2 refreshes — so a new month shows up with no edit to the text:

    last        the newest actual value
    change12m   the newest actual against the same month a year earlier
    asof        the month of the newest actual

Whether that value is late comes from `series_health` (Jira 208/209 S4): the
same per-frequency rule as the Refresh status page and the Index Library, so
the text never calls a value current that the rest of the app calls late.
A series with only forecast values shows a forecast point, marked as one.

Recipe tokens read a platform recipe line's weight (`weight_pct`). A weight
is a fact of the recipe, not of a month, so it carries no date. Bar tokens
(LIVE-4) read the min–max weight of one input across the recipes of the
report's product lines (`report_recipe_weights`).

Anything that cannot be answered — unknown series, removed series, no value a
year back, unknown recipe line — is `missing` with a reason, and the display
falls back to the token's own fallback text.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.formula_template import FormulaTemplate, FormulaTemplateComponent
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexMonthlyValue, TypeCode
from app.models.market_report import MarketReportLine
from app.services.live_tokens import (
    ASOF, BAR, CHANGE_12M, CURRENT, FORECAST, INDEX, LATE, MISSING, RECIPE, Resolved, Token,
)
from app.services.series_health import CURRENT as HEALTH_CURRENT
from app.services.series_health import FORECAST_ONLY, STALE, series_health_bulk


def _month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def _year_month(index: int) -> tuple[int, int]:
    return index // 12, index % 12 + 1


def _resolve_index(db: Session, tokens: list[Token], as_of: date) -> dict[Token, Resolved]:
    keys = {t.key for t in tokens}
    series = (db.query(CommodityIndex.commodity_key, CommodityIndex.id, CommodityIndex.value_kind)
              .filter(CommodityIndex.commodity_key.in_(keys)).all())
    ids = {key: cid for key, cid, _ in series}
    levels = {cid for _, cid, kind in series if kind == "index_level"}
    health = {h.commodity_id: h for h in series_health_bulk(
        db, as_of=as_of, commodity_ids=list(ids.values()))} if ids else {}

    # Every monthly point of the series in play: (id, kind) -> {month index: value}.
    points: dict[tuple[int, str], dict[int, float]] = {}
    if ids:
        for cid, year, month, value, kind in (
            db.query(IndexMonthlyValue.commodity_id, IndexMonthlyValue.year,
                     IndexMonthlyValue.month, IndexMonthlyValue.value, IndexMonthlyValue.kind)
            .filter(IndexMonthlyValue.commodity_id.in_(list(ids.values())))
        ):
            points.setdefault((cid, kind), {})[_month_index(year, month)] = float(value)

    out: dict[Token, Resolved] = {}
    for t in tokens:
        cid = ids.get(t.key)
        if cid is None:
            out[t] = Resolved(MISSING, reason=f"unknown series {t.key!r}")
            continue
        h = health.get(cid)
        actuals = points.get((cid, "actual"), {})
        forecasts = points.get((cid, "forecast"), {})

        if h is not None and h.state in (HEALTH_CURRENT, STALE) and actuals:
            status = CURRENT if h.state == HEALTH_CURRENT else LATE
            latest = max(actuals)
            values = actuals
        elif forecasts and (h is None or h.state == FORECAST_ONLY):
            # Only forecasts: the forecast for this month, else the nearest one.
            status = FORECAST
            now = _month_index(as_of.year, as_of.month)
            earlier = [m for m in forecasts if m <= now]
            latest = max(earlier) if earlier else min(forecasts)
            values = forecasts
        else:
            out[t] = Resolved(MISSING, reason=f"series {t.key!r} has no values")
            continue

        year, month = _year_month(latest)
        if t.measure == ASOF:
            out[t] = Resolved(status, year=year, month=month)
        elif t.measure == CHANGE_12M:
            before = values.get(latest - 12)
            if not before:
                out[t] = Resolved(MISSING, reason=f"series {t.key!r} has no value 12 months earlier")
            else:
                out[t] = Resolved(status, value=(values[latest] / before - 1) * 100,
                                  year=year, month=month)
        else:
            out[t] = Resolved(status, value=values[latest], year=year, month=month,
                              index_level=cid in levels)
    return out


def _resolve_recipe(db: Session, tokens: list[Token]) -> dict[Token, Resolved]:
    codes = {t.code for t in tokens}
    rows = (
        db.query(FormulaTemplate.code, FormulaTemplateComponent.region,
                 FormulaTemplateComponent.variant, FormulaTemplateComponent.name,
                 FormulaTemplateComponent.weight_pct)
        .join(FormulaTemplateComponent, FormulaTemplateComponent.template_id == FormulaTemplate.id)
        .filter(FormulaTemplate.team_id.is_(None), FormulaTemplate.code.in_(codes))
        .all()
    )
    # (code, region, line) -> weight; the base variant ('') wins over named ones.
    weights: dict[tuple[str, str, str], float] = {}
    for code, region, variant, name, weight in sorted(rows, key=lambda r: r[2] != ""):
        weights.setdefault((code, (region or "").lower(), name.lower()), float(weight))

    out: dict[Token, Resolved] = {}
    for t in tokens:
        w = weights.get((t.code, t.region.lower(), t.line.lower()))
        out[t] = (Resolved(CURRENT, value=w) if w is not None
                  else Resolved(MISSING, reason=f"no line {t.line!r} in {t.code} {t.region}"))
    return out


def report_recipe_weights(db: Session, slugs: Iterable[str] | None = None) -> tuple[dict, dict]:
    """Per report: {type code: [weights]} and {line name (lower case): [weights]}
    over the platform recipes of the report's own product lines.

    PROPOSED (LIVE-4, decision 2 — Laurent to confirm): a cost-structure
    bar's range is the min–max weight of its input across these recipes."""
    by_code: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_name: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    q = (db.query(MarketReportLine.slug, TypeCode.code, FormulaTemplateComponent.name,
                  FormulaTemplateComponent.weight_pct, FormulaTemplateComponent.id)
         .join(FormulaTemplate, (FormulaTemplate.product_line_id == MarketReportLine.product_line_id)
               & FormulaTemplate.team_id.is_(None))
         .join(FormulaTemplateComponent, FormulaTemplateComponent.template_id == FormulaTemplate.id)
         .outerjoin(TypeCode, TypeCode.id == FormulaTemplateComponent.type_code_id)
         .distinct())
    if slugs is not None:
        q = q.filter(MarketReportLine.slug.in_(list(slugs)))
    for slug, code, name, weight, _id in q:
        if code:
            by_code[slug][code].append(float(weight))
        by_name[slug][name.lower()].append(float(weight))
    return by_code, by_name


def _resolve_bar(db: Session, tokens: list[Token]) -> dict[Token, Resolved]:
    by_code, by_name = report_recipe_weights(db, {t.report for t in tokens})
    out: dict[Token, Resolved] = {}
    for t in tokens:
        weights = (by_code.get(t.report, {}).get(t.code) if t.code
                   else by_name.get(t.report, {}).get(t.line.lower()))
        if not weights:
            what = f"code {t.code}" if t.code else f"line {t.line!r}"
            out[t] = Resolved(MISSING, reason=f"no recipe line with {what} for report {t.report}")
        else:
            out[t] = Resolved(CURRENT, value=max(weights), low=min(weights))
    return out


def resolve(db: Session, tokens: Iterable[Token], *, as_of: date | None = None) -> dict[Token, Resolved]:
    """Every token answered: a value with its month and state, or `missing`."""
    as_of = as_of or date.today()
    tokens = list(dict.fromkeys(tokens))
    out: dict[Token, Resolved] = {}
    index = [t for t in tokens if t.kind == INDEX]
    recipe = [t for t in tokens if t.kind == RECIPE]
    bars = [t for t in tokens if t.kind == BAR]
    if index:
        out.update(_resolve_index(db, index, as_of))
    if recipe:
        out.update(_resolve_recipe(db, recipe))
    if bars:
        out.update(_resolve_bar(db, bars))
    return out

