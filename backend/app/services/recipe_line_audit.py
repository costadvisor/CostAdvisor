"""Recipe-line audit: does every recipe line point at a real index with data? (Jira 208)

The acceptance criterion, as a repeatable read: every line of every platform
catalogue recipe gets exactly ONE status, with the reason and the cost weight
behind it, so "where possible" becomes a list somebody can work through
rather than a judgement made once.

    ok               indexed, resolved, series has a current actual value,
                     and the series is the line's own region (or global)
    stale            as ok, but the series' last actual is older than the
                     series-health threshold
    other_region     priced on a series stated for a different region
                     (e.g. a Europe line on `methanol-na`)
    series_empty     resolves to a series with no actual values
    code_mismatch    the line's series is not the one its type code resolves to
    ambiguous        the type code does not resolve to one series
    no_series        the type code resolves to a series nobody has
    unlinked         an index line with no type code
    fixed_no_index   a material (feedstock / utility / packaging) priced as a
                     fixed share because no index exists for it
    fixed            margin and other deliberately non-indexed shares
    sub_recipe       a line that is another recipe (`component_type='formula'`)

One status per line, the most serious first, so the counts add up to the
number of lines. Proxy status is reported beside the status rather than as a
status of its own: the content leaves it NULL on every type code today, so a
status for it would mark every indexed line and hide everything else.

Read only. Series health comes from `series_health` — the one shared answer —
so this audit and the screens that adopt it cannot disagree about a series.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.models.formula_template import FormulaTemplate, FormulaTemplateComponent
from app.models.index_data import CommodityIndex
from app.models.index_layer import TypeCode
from app.services.content_drop.reader import app_region
from app.services.series_health import (
    CURRENT, EMPTY, FORECAST_ONLY, STALE as SERIES_STALE, series_health_bulk,
)

OK = "ok"
STALE = "stale"
OTHER_REGION = "other_region"
SERIES_EMPTY = "series_empty"
CODE_MISMATCH = "code_mismatch"
AMBIGUOUS = "ambiguous"
NO_SERIES = "no_series"
UNLINKED = "unlinked"
FIXED_NO_INDEX = "fixed_no_index"
FIXED = "fixed"
SUB_RECIPE = "sub_recipe"

# Most serious first; also the order the summary is printed in.
STATUSES = (
    UNLINKED, NO_SERIES, AMBIGUOUS, CODE_MISMATCH, SERIES_EMPTY,
    OTHER_REGION, STALE, FIXED_NO_INDEX, OK, FIXED, SUB_RECIPE,
)

# Statuses that meet the acceptance criterion as written. `fixed` (margin) and
# `sub_recipe` are not index lines at all, so they neither meet nor miss it.
MEETS = (OK,)
NOT_INDEX_LINES = (FIXED, SUB_RECIPE)

# A fixed share in one of these categories is a material with no index, not a
# deliberate margin.
_MATERIAL_CATEGORIES = ("feedstock", "utility", "packaging")

# PROPOSED, pending Alexis. Series regions a line region accepts as its own,
# beyond an exact match and GLOBAL. The `regions` table has no parent link for
# these (China and India have no parent), but an APAC line priced on a China or
# India series is pricing inside its own region; flagging those 460 lines
# would bury the real cross-region cases (Europe on NA) under them.
_REGION_INCLUDES = {"APAC": ("China", "India")}


def _same_region(line_region: str | None, series_region: str | None) -> bool:
    if not line_region or not series_region or series_region == "GLOBAL":
        return True   # no stated region on one side: no claim either way
    return series_region == line_region or series_region in _REGION_INCLUDES.get(line_region, ())


@dataclass
class LineAudit:
    template_id: str
    template_code: str | None
    region: str | None
    variant: str
    line_name: str
    component_type: str
    cost_category: str | None
    weight_pct: float
    status: str
    reason: str
    type_code: str | None = None
    commodity_id: int | None = None
    commodity_key: str | None = None
    series_region: str | None = None
    series_last_actual: str | None = None
    proxy_status: str | None = None       # the type code's reading; None = unclassified


@dataclass
class AuditSummary:
    as_of: date
    lines: int
    recipes: int
    templates_without_product_line: list[str]
    by_status: dict[str, dict] = field(default_factory=dict)
    # Share of the weight on index-or-material lines that meets the criterion.
    meets_pct_of_index_weight: float | None = None
    proxy_unclassified_lines: int = 0


def _series_region(series: CommodityIndex | None) -> str | None:
    if series is None or not series.source_region:
        return None
    return app_region(series.source_region) or series.source_region


def _index_status(line, tc, series, health, series_region) -> tuple[str, str]:
    if tc is None:
        return UNLINKED, "index line with no type code"
    if tc.resolution == "ambiguous":
        return AMBIGUOUS, f"type code {tc.code} does not resolve to a single series"
    if tc.resolution == "no_series" or tc.resolves_to_id is None:
        return NO_SERIES, f"type code {tc.code} resolves to no available series"
    if line.commodity_id is not None and line.commodity_id != tc.resolves_to_id:
        return CODE_MISMATCH, (
            f"line uses series {line.commodity_id} but {tc.code} resolves to {tc.resolves_to_id}"
        )
    if health is None or health.state in (EMPTY, FORECAST_ONLY):
        why = health.reason if health else "series not found"
        return SERIES_EMPTY, f"series has no actual values ({why})"
    key = series.commodity_key or series.name
    if not _same_region(line.region, series_region):
        return OTHER_REGION, f"{line.region} line priced on {key}, a {series_region} series"
    if health.state == SERIES_STALE:
        return STALE, f"{key}: {health.reason}"
    if health.state == CURRENT or health.usable:
        return OK, f"{key}: {health.reason}"
    return SERIES_EMPTY, f"{key}: {health.reason}"


def audit_recipe_lines(db: Session, *, as_of: date | None = None) -> list[LineAudit]:
    """Every line of every platform (catalogue) recipe, one status each."""
    as_of = as_of or date.today()

    rows = (
        db.query(FormulaTemplateComponent, FormulaTemplate.code)
        .join(FormulaTemplate, FormulaTemplate.id == FormulaTemplateComponent.template_id)
        .filter(FormulaTemplate.team_id.is_(None))
        .order_by(FormulaTemplate.code, FormulaTemplateComponent.region,
                  FormulaTemplateComponent.variant, FormulaTemplateComponent.sort_order)
        .all()
    )
    codes = {tc.id: tc for tc in db.query(TypeCode)}
    series = {s.id: s for s in db.query(CommodityIndex)}
    health = {h.commodity_id: h for h in series_health_bulk(db, as_of=as_of)}

    out: list[LineAudit] = []
    for line, template_code in rows:
        tc = codes.get(line.type_code_id) if line.type_code_id else None
        sid = line.commodity_id or (tc.resolves_to_id if tc else None)
        s = series.get(sid) if sid else None
        h = health.get(sid) if sid else None
        s_region = _series_region(s)

        if line.component_type == "formula":
            status, reason = SUB_RECIPE, "line is another recipe"
        elif line.component_type == "fixed":
            if line.cost_category in _MATERIAL_CATEGORIES:
                status, reason = FIXED_NO_INDEX, f"{line.cost_category} priced as a fixed share; no index"
            else:
                status, reason = FIXED, f"{line.cost_category or 'fixed'} share, not indexed by design"
        else:
            status, reason = _index_status(line, tc, s, h, s_region)

        out.append(LineAudit(
            template_id=str(line.template_id),
            template_code=template_code,
            region=line.region,
            variant=line.variant,
            line_name=line.name,
            component_type=line.component_type,
            cost_category=line.cost_category,
            weight_pct=float(line.weight_pct),
            status=status,
            reason=reason,
            type_code=tc.code if tc else None,
            commodity_id=sid,
            commodity_key=s.commodity_key if s else None,
            series_region=s_region,
            series_last_actual=h.last_actual if h else None,
            proxy_status=tc.proxy_status if tc else None,
        ))
    return out


def summarise(db: Session, lines: list[LineAudit], *, as_of: date, examples: int = 5) -> AuditSummary:
    """Counts and weight by status, plus the heaviest examples of each."""
    recipes = {(l.template_id, l.region, l.variant) for l in lines}
    no_line = [
        code for (code,) in db.query(FormulaTemplate.code)
        .filter(FormulaTemplate.team_id.is_(None), FormulaTemplate.product_line_id.is_(None))
        .order_by(FormulaTemplate.code)
    ]

    total_weight = sum(l.weight_pct for l in lines) or 1.0
    grouped: dict[str, list[LineAudit]] = defaultdict(list)
    for l in lines:
        grouped[l.status].append(l)

    by_status = {}
    for status in STATUSES:
        group = grouped.get(status, [])
        weight = sum(l.weight_pct for l in group)
        heaviest = sorted(group, key=lambda l: -l.weight_pct)[:examples]
        by_status[status] = {
            "lines": len(group),
            "recipes": len({(l.template_id, l.region, l.variant) for l in group}),
            "weight_pct_of_all": round(weight / total_weight * 100, 2),
            "examples": [
                {"template_code": l.template_code, "region": l.region, "line_name": l.line_name,
                 "weight_pct": l.weight_pct, "reason": l.reason}
                for l in heaviest
            ],
        }

    scored = [l for l in lines if l.status not in NOT_INDEX_LINES]
    scored_weight = sum(l.weight_pct for l in scored)
    meets_weight = sum(l.weight_pct for l in scored if l.status in MEETS)

    return AuditSummary(
        as_of=as_of,
        lines=len(lines),
        recipes=len(recipes),
        templates_without_product_line=no_line,
        by_status=by_status,
        meets_pct_of_index_weight=(
            round(meets_weight / scored_weight * 100, 2) if scored_weight else None
        ),
        proxy_unclassified_lines=sum(
            1 for l in lines if l.component_type == "index" and l.proxy_status is None
        ),
    )
