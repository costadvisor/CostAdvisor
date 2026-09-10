"""Proxy derivation engine + value provenance (Wave 3, SCRUM-80 / FD-1).

Three things, all at type-code grain:

1. **An execution call site for `proxy_logic`.** The spec has been written by
   the seed loaders, editable through `PUT /api/indexes/{id}/proxy-logic` and
   displayed in the admin editor since Scrum 57/67 — and read by no
   computation anywhere. The arithmetic was never the gap; the missing piece
   was anything that runs it. A derived value comes back tagged as derived and
   naming the base series and operation behind it, so it is never
   indistinguishable from an observed one.

2. **Value provenance with age.** `data_resolver.get_single_index_value` ends
   in a carry-forward tier that exists so future reference quarters do not
   flatten ratios to 1.0 — deliberate for costing, and deliberately kept. But
   it returns a number with no indication of when it was actually observed, so
   a caller cannot tell current from stale from never-had-it. This module
   reports that distinction. It does NOT change the costing path.

3. **The swap backlog.** `swap_priority` is a sourcing rank, not an accuracy
   score: A means a better index exists and buying it improves the number
   overnight, B is a defensible upstream stand-in, C is permanent by design
   (electricity tracks electricity). Ranked by the cost weight actually
   sitting behind each code, so an A carrying a lot of weight sorts above an A
   carrying little.

Consumes the resolution chain rather than rebuilding it — the type-code ->
series join and the concentration read are SCRUM-74's (services/resolution.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants.index_metadata import PROXY_OPERATIONS
from app.models.formula_template import FormulaTemplateComponent
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexMonthlyValue, TypeCode

# Value freshness, reported rather than inferred by the caller.
CURRENT = "current"
STALE = "stale"
ABSENT = "absent"

# Why a type code cannot produce a number. Distinct states because the
# remedies are completely different — buy a feed, decide what a code means, or
# make a scrape run.
UNRESOLVABLE_NO_SERIES = "no_series"
UNRESOLVABLE_AMBIGUOUS = "ambiguous"
UNRESOLVABLE_NO_HISTORY = "resolved_but_no_history"

# Operations this spec shape can actually express.
#
# `regression` is in the vocabulary but needs fitted coefficients, and
# `proxy_logic` carries a single scalar `spread` — there is nowhere to put
# them. `ratio` is executed as a multiplicative factor, which is what a
# historical-ratio proxy means in practice; if it was ever intended to mean
# "scale by another series' ratio" the spec needs a second series field, and
# guessing between the two would produce confidently wrong numbers.
_EXECUTABLE_OPERATIONS = {"passthrough", "add", "multiply", "ratio", "spread"}
_UNSUPPORTED_OPERATIONS = set(PROXY_OPERATIONS) - _EXECUTABLE_OPERATIONS


@dataclass
class ValueProvenance:
    """A number and the honest account of where it came from."""

    value: float | None
    status: str                       # current | stale | absent
    observed_year: int | None = None
    observed_quarter: int | None = None
    quarters_stale: int | None = None
    kind: str | None = None           # actual | forecast
    # True when produced by executing a proxy spec rather than observed.
    derived: bool = False
    derivation: dict | None = None
    reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.value is not None


def _quarter_of(month: int) -> int:
    return (month - 1) // 3 + 1


def resolve_with_provenance(
    db: Session,
    commodity_id: int,
    year: int,
    quarter: int,
    *,
    kind: str = "actual",
) -> ValueProvenance:
    """The value for a period, plus when it was actually observed.

    Reads `index_monthly_values` (the drop's series grain) and averages the
    months in the requested quarter. If that quarter has nothing, falls back to
    the most recent earlier quarter — but reports it as `stale` with the age,
    which is the distinction the costing path's silent carry-forward cannot
    make.

    Region is deliberately not a parameter: in the three-layer model the region
    is baked into the series key, so a series IS already region-specific.
    (The costing path's final fallback drops its region filter entirely and
    carries a value forward from any region — a real hazard, but one this read
    sidesteps by construction rather than inheriting.)
    """
    rows = (
        db.query(IndexMonthlyValue.year, IndexMonthlyValue.month, IndexMonthlyValue.value)
        .filter(
            IndexMonthlyValue.commodity_id == commodity_id,
            IndexMonthlyValue.kind == kind,
        )
        .all()
    )
    if not rows:
        return ValueProvenance(
            value=None, status=ABSENT,
            reason=f"no {kind} history for this series",
        )

    by_quarter: dict[tuple[int, int], list[float]] = {}
    for r_year, r_month, r_value in rows:
        by_quarter.setdefault((r_year, _quarter_of(r_month)), []).append(float(r_value))

    exact = by_quarter.get((year, quarter))
    if exact:
        return ValueProvenance(
            value=sum(exact) / len(exact), status=CURRENT,
            observed_year=year, observed_quarter=quarter,
            quarters_stale=0, kind=kind,
        )

    earlier = [k for k in by_quarter if (k[0], k[1]) < (year, quarter)]
    if not earlier:
        return ValueProvenance(
            value=None, status=ABSENT,
            reason=f"no {kind} value at or before Q{quarter}-{year}",
        )

    latest = max(earlier)
    values = by_quarter[latest]
    age = (year - latest[0]) * 4 + (quarter - latest[1])
    return ValueProvenance(
        value=sum(values) / len(values), status=STALE,
        observed_year=latest[0], observed_quarter=latest[1],
        quarters_stale=age, kind=kind,
        reason=f"carried forward from Q{latest[1]}-{latest[0]} ({age} quarter(s) stale)",
    )


# ── Derivation ───────────────────────────────────────────────────────────────

def derivation_spec(series: CommodityIndex) -> tuple[dict | None, str | None]:
    """The executable half of a `proxy_logic` spec, or a reason it is not one.

    Most specs in the catalog today carry only the analyst `note` — the
    `operation` and `base_index` params were never filled in. That is a
    configuration state, not an error, and it is reported as such rather than
    treated as a failure.
    """
    spec = series.proxy_logic or None
    if not spec:
        return None, "no proxy_logic configured"
    operation = spec.get("operation")
    base_index = spec.get("base_index")
    if not operation and not base_index:
        note = spec.get("note")
        return None, (
            "proxy_logic carries only an analyst note, no executable params"
            + (f": {note}" if note else "")
        )
    if not operation:
        return None, "proxy_logic has a base_index but no operation"
    if not base_index:
        return None, f"proxy_logic operation {operation!r} has no base_index"
    if operation in _UNSUPPORTED_OPERATIONS:
        return None, (
            f"operation {operation!r} needs parameters this spec shape cannot carry "
            "(a single scalar spread is not enough)"
        )
    if operation not in _EXECUTABLE_OPERATIONS:
        return None, f"unknown operation {operation!r}"
    return spec, None


def _apply_operation(base_value: float, spec: dict) -> tuple[float, str]:
    operation = spec["operation"]
    spread = spec.get("spread")
    unit = (spec.get("spread_unit") or "abs").lower()

    if operation == "passthrough":
        return base_value, "base value passed through unchanged"
    if spread is None:
        raise ValueError(f"operation {operation!r} needs a spread")

    if operation in {"multiply", "ratio"}:
        return base_value * float(spread), f"base x {spread}"
    if unit == "pct":
        return base_value * (1 + float(spread) / 100), f"base +{spread}%"
    return base_value + float(spread), f"base {'+' if spread >= 0 else ''}{spread}"


def derive_value(
    db: Session, commodity_id: int, year: int, quarter: int
) -> ValueProvenance:
    """Execute a series' configured proxy spec for a period.

    The return is always tagged `derived=True` when a number was produced this
    way, and carries the base series, the operation and the base value's own
    provenance — so a consumer can never mistake it for an observation, and a
    stale base is visible rather than laundered into a fresh-looking result.
    """
    series = db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first()
    if series is None:
        return ValueProvenance(value=None, status=ABSENT, reason="unknown series")

    spec, why_not = derivation_spec(series)
    if spec is None:
        return ValueProvenance(value=None, status=ABSENT, reason=why_not)

    # `base_index` holds a series NAME, not an id — that is how the seed loader
    # and the admin editor both write it. Resolved here rather than made an FK,
    # because a spec may legitimately name a series we do not hold yet.
    base_name = spec["base_index"]
    base = db.query(CommodityIndex).filter(CommodityIndex.name == base_name).first()
    if base is None:
        return ValueProvenance(
            value=None, status=ABSENT,
            reason=f"base_index {base_name!r} is not a known series",
        )

    base_provenance = resolve_with_provenance(db, base.id, year, quarter)
    if not base_provenance.usable:
        return ValueProvenance(
            value=None, status=ABSENT,
            reason=f"base series {base_name!r} has no usable value: {base_provenance.reason}",
        )

    try:
        value, expression = _apply_operation(base_provenance.value, spec)
    except ValueError as exc:
        return ValueProvenance(value=None, status=ABSENT, reason=str(exc))

    return ValueProvenance(
        value=value,
        # A derivation is only as fresh as the base it stands on.
        status=base_provenance.status,
        observed_year=base_provenance.observed_year,
        observed_quarter=base_provenance.observed_quarter,
        quarters_stale=base_provenance.quarters_stale,
        kind=base_provenance.kind,
        derived=True,
        derivation={
            "base_series": base.commodity_key or base.name,
            "base_series_id": base.id,
            "operation": spec["operation"],
            "spread": spec.get("spread"),
            "spread_unit": spec.get("spread_unit"),
            "expression": expression,
            "base_value": base_provenance.value,
            "base_status": base_provenance.status,
            "recalibration": spec.get("recalibration"),
            "note": spec.get("note"),
        },
    )


# ── The type-code entry point ────────────────────────────────────────────────

@dataclass
class TypeCodeValue:
    code: str
    resolution: str
    resolvable: bool
    value: float | None = None
    provenance: ValueProvenance | None = None
    # Named even when unresolvable — "we wanted this series and cannot have it"
    # is actionable; a bare null is not.
    wanted_series: str | None = None
    ideal_index: str | None = None
    swap_priority: str | None = None
    unresolvable_reason: str | None = None


def type_code_value(
    db: Session, code: str, year: int, quarter: int
) -> TypeCodeValue | None:
    """A number for a type code, or an explicit unresolvable state.

    Never returns a null that could be mistaken for zero, and never silently
    carries a value forward without saying so. When the code cannot produce a
    number it names the series it wanted and why that failed.
    """
    tc = db.query(TypeCode).filter(TypeCode.code == code).first()
    if tc is None:
        return None

    base = TypeCodeValue(
        code=tc.code,
        resolution=tc.resolution,
        resolvable=False,
        ideal_index=tc.ideal_index,
        swap_priority=tc.swap_priority,
    )

    if tc.resolution == "ambiguous":
        base.unresolvable_reason = UNRESOLVABLE_AMBIGUOUS
        return base

    series = (
        db.query(CommodityIndex).filter(CommodityIndex.id == tc.resolves_to_id).first()
        if tc.resolves_to_id
        else None
    )
    if series is None:
        base.unresolvable_reason = UNRESOLVABLE_AMBIGUOUS
        return base

    # Named regardless of outcome: the whole point of `no_series` is that we
    # know exactly which feed is missing.
    base.wanted_series = series.commodity_key or series.name

    if tc.resolution == "no_series":
        base.unresolvable_reason = UNRESOLVABLE_NO_SERIES
        return base

    observed = resolve_with_provenance(db, series.id, year, quarter)
    if observed.usable:
        base.resolvable = True
        base.value = observed.value
        base.provenance = observed
        return base

    # Nothing observed — fall back to a configured derivation if there is one.
    derived = derive_value(db, series.id, year, quarter)
    if derived.usable:
        base.resolvable = True
        base.value = derived.value
        base.provenance = derived
        return base

    base.unresolvable_reason = UNRESOLVABLE_NO_HISTORY
    base.provenance = observed
    return base


# ── The swap backlog ─────────────────────────────────────────────────────────

@dataclass
class BacklogEntry:
    code: str
    label: str | None
    swap_priority: str | None
    resolution: str
    proxy_status: str | None
    ideal_index: str | None
    catalog_weight: float
    line_count: int
    priceable: bool


@dataclass
class SwapBacklog:
    total_catalog_weight: float
    entries: list[BacklogEntry] = field(default_factory=list)


def swap_backlog(
    db: Session, *, priority: str | None = None, limit: int = 100
) -> SwapBacklog:
    """Sourcing candidates ranked by the cost weight behind them.

    Weight is the live catalog's, aggregated over recipe lines — not the
    drop's own snapshot — so the ranking reflects what the library actually
    depends on today.

    **Only `component_type='index'` lines count.** Margin is a line inside the
    100% total in this catalog, and fixed lines carry no index at all; letting
    either into a weight-share aggregation would inflate every denominator and
    make the ranking meaningless.
    """
    weights = (
        db.query(
            FormulaTemplateComponent.type_code_id.label("type_code_id"),
            func.sum(FormulaTemplateComponent.weight_pct).label("weight"),
            func.count(FormulaTemplateComponent.id).label("lines"),
        )
        .filter(
            FormulaTemplateComponent.type_code_id.isnot(None),
            FormulaTemplateComponent.component_type == "index",
        )
        .group_by(FormulaTemplateComponent.type_code_id)
        .subquery()
    )

    query = (
        db.query(TypeCode, weights.c.weight, weights.c.lines)
        .outerjoin(weights, weights.c.type_code_id == TypeCode.id)
        .order_by(weights.c.weight.desc().nullslast(), TypeCode.code)
    )
    if priority is not None:
        query = query.filter(TypeCode.swap_priority == priority)

    total = float(
        db.query(func.sum(FormulaTemplateComponent.weight_pct))
        .filter(
            FormulaTemplateComponent.type_code_id.isnot(None),
            FormulaTemplateComponent.component_type == "index",
        )
        .scalar()
        or 0
    )

    entries = [
        BacklogEntry(
            code=tc.code,
            label=tc.label,
            swap_priority=tc.swap_priority,
            resolution=tc.resolution,
            proxy_status=tc.proxy_status,
            ideal_index=tc.ideal_index,
            catalog_weight=float(weight or 0),
            line_count=int(lines or 0),
            priceable=tc.resolution == "resolved",
        )
        for tc, weight, lines in query.limit(limit).all()
    ]
    return SwapBacklog(total_catalog_weight=total, entries=entries)


def blocked_series(db: Session) -> list[dict]:
    """Series that cannot be priced, derived from the data rather than named in
    code.

    The pre-drop seeder hardcodes a two-element blocked set
    (`seed_index_metadata._NEW_BLOCKED_CODES`), which is why "exactly two feeds
    have no source" was repeated as a fact about the data when it was really a
    fact about that literal. The real answer is every type code whose
    resolution says so, weighted by what depends on it — and it moves as the
    data moves.
    """
    backlog = swap_backlog(db, limit=10_000)
    blocked = [e for e in backlog.entries if e.resolution in {"no_series", "ambiguous"}]

    # The series each blocked code wanted, in one lookup — that name IS the
    # sourcing instruction, so a caller never has to re-walk the chain to find
    # out which feed to go and buy.
    wanted = {
        # Same fallback as everywhere else in this module: `commodity_key` is
        # the drop's namespace and is null on series that predate it.
        code: key or name
        for code, key, name in (
            db.query(TypeCode.code, CommodityIndex.commodity_key, CommodityIndex.name)
            .join(CommodityIndex, CommodityIndex.id == TypeCode.resolves_to_id)
            .filter(TypeCode.code.in_([e.code for e in blocked]))
            .all()
        )
    } if blocked else {}

    return [
        {
            "code": e.code,
            "resolution": e.resolution,
            "wanted_series": wanted.get(e.code),
            "ideal_index": e.ideal_index,
            "swap_priority": e.swap_priority,
            "catalog_weight": e.catalog_weight,
            "line_count": e.line_count,
        }
        for e in blocked
    ]
