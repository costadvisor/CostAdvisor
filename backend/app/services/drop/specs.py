"""Per-file specs for the 2026-07 drop (SCRUM-74 unit 1).

The registry the generic reader consults — same split as
`sheet_roundtrip`: the mechanism never branches on a filename, it asks the
spec. Adding a table is one entry here, not a change to `reader.py`.

A spec declares only what the reader cannot infer: the stable key, and which
columns are numeric / integer / boolean. Everything else stays a string
until a loader decides what it means.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DropTableSpec:
    name: str
    filename: str
    key_columns: tuple[str, ...]
    numeric_columns: tuple[str, ...] = ()
    int_columns: tuple[str, ...] = ()
    bool_columns: tuple[str, ...] = ()
    note: str = ""


# ── The index layers (DB-5 / DB-6 consume these) ─────────────────────────────

TYPE_CODES = DropTableSpec(
    name="type_codes",
    filename="type_codes.csv",
    key_columns=("type_code",),
    numeric_columns=("total_weight",),
    int_columns=("n_formulas", "n_lines"),
    # `resolution` is three-valued: resolved / no_series / ambiguous. Only the
    # `ambiguous` rows have a blank `resolves_to` — `no_series` means the
    # target series exists but carries no numbers, so `resolves_to` is a valid
    # reference there. Binary handling files the third state wrong.
    note="resolves_to is blank ONLY for resolution='ambiguous'",
)

INDEX_COMMODITIES = DropTableSpec(
    name="index_commodities",
    filename="index_commodities.csv",
    key_columns=("commodity_key",),
    int_columns=("n_cards", "n_actual_points", "n_forecast_points"),
    bool_columns=("has_series",),
    # source_region carries the literal "NA" for North America — see trap 1 in
    # reader.py for why this file is read with stdlib csv.
    note="source_region 'NA' is North America, not missing",
)

INDEX_FEEDS = DropTableSpec(
    name="index_feeds",
    filename="index_feeds.csv",
    key_columns=("feed_key",),
    numeric_columns=("current_value", "change_pct", "volatility_pct", "cycle_pct"),
    int_columns=("n_series_points", "n_forecast_points", "used_in_formulas"),
    bool_columns=("is_default_region", "has_intel_block"),
    # feed_key has two formats: `slug|region` and a bare slug for the stub
    # cards. is_default_region is NOT unique per slug (18 slugs carry several
    # defaults), so it cannot back a unique constraint.
    note="feed_key is `slug|region` or a bare slug; is_default_region is not unique",
)

INDEX_SERIES = DropTableSpec(
    name="index_series",
    filename="index_series.csv",
    key_columns=("series_key", "year", "month"),
    numeric_columns=("value",),
    int_columns=("year", "month", "quarter"),
    # `kind` is actual|forecast. quarter is always consistent with the month,
    # so it derives rather than being stored.
    note="kind is actual|forecast; quarter derives from month",
)

INDEX_VALUES = DropTableSpec(
    name="index_values",
    filename="index_values.csv",
    key_columns=("commodity_key", "year", "quarter"),
    numeric_columns=("value",),
    int_columns=("year", "quarter", "n_months"),
    note="quarterly actuals only; exactly derivable from index_series",
)

INDEX_FORECASTS = DropTableSpec(
    name="index_forecasts",
    filename="index_forecasts.csv",
    key_columns=("commodity_key", "year", "quarter"),
    numeric_columns=("value",),
    int_columns=("year", "quarter", "n_months"),
    note="the only quarterly table covering the no-history series",
)

INDEX_SERIES_QUARTERLY = DropTableSpec(
    name="index_series_quarterly",
    filename="index_series_quarterly.csv",
    key_columns=("series_key", "year", "quarter"),
    numeric_columns=("value",),
    int_columns=("year", "quarter", "n_months"),
    note="superset of index_values + index_forecasts; derives from index_series",
)

# ── The catalog layers ───────────────────────────────────────────────────────

COMBOS = DropTableSpec(
    name="combos",
    filename="combos.csv",
    # Two combos share a (formula_id, region) and differ only by `variant`
    # (bentonite activated/natural, talc treated/untreated), so the key has
    # three parts. combo_id alone also works, but only after the middle-dot
    # normalisation in normalize.py.
    key_columns=("formula_id", "region", "variant"),
    numeric_columns=(
        "margin_pct", "weight_total", "w_direct", "w_proxy", "w_unclassified",
        "w_fixed", "indexed_pct", "priceable_pct", "indexed_priceable_pct",
    ),
    int_columns=("n_lines",),
    bool_columns=("assumed_region", "volatile", "loadable"),
    # `loadable` is a pricing-completeness flag, NOT a schema-validity one —
    # it is exactly (n_lines > 0 AND no line resolution in
    # {no_series, ambiguous}). Filtering a load on it drops combos for a
    # data-purchase reason while admitting taxonomy-broken ones.
    note="loadable means priceable, not schema-valid — see authority.py",
)

COMBO_LINES = DropTableSpec(
    name="combo_lines",
    filename="combo_lines.csv",
    key_columns=("combo_id", "seq"),
    numeric_columns=("weight_pct", "weight_frac"),
    int_columns=("seq",),
    note="type_code='fixed' is a sentinel, not an FK — see normalize.is_fixed_line",
)

FORMULAS = DropTableSpec(
    name="formulas",
    filename="formulas.csv",
    key_columns=("formula_id",),
    numeric_columns=("margin_pct_min", "margin_pct_max"),
    int_columns=("n_combos",),
    bool_columns=("has_synthesis_route",),
    # record_shape (combo|flat|partial) is the discriminated-union tag: the 17
    # `flat` rows are one cohort behind what the drop analysis lists as ~6
    # separate problems. Branch on it once.
    note="record_shape is the union tag; functionality_tags is single-valued free text",
)

FAMILIES = DropTableSpec(
    name="families",
    filename="families.csv",
    key_columns=("family", "subfamily"),
    int_columns=("n_formulas", "n_combos"),
    # Derived, not source: n_formulas/n_combos are counts, and the file holds
    # only the formula-level pairs. Combos carry pairs it lacks, so the
    # taxonomy should be rebuilt from the union rather than read from here.
    # There is no code column anywhere — codes must be synthesised.
    note="derived and incomplete; no code column exists",
)

# ── The decision forms (empty by design; Alexis fills them) ──────────────────

REGION_BASIS = DropTableSpec(
    name="region_basis",
    filename="region_basis.csv",
    key_columns=("region",),
    note="empty form — every basis column is blank for all 8 regions",
)

INDEX_BASIS = DropTableSpec(
    name="index_basis",
    filename="index_basis.csv",
    key_columns=("commodity_key",),
    note="empty form — currency/category/source_url unfilled for every series",
)


_REGISTRY: dict[str, DropTableSpec] = {
    s.name: s
    for s in (
        TYPE_CODES, INDEX_COMMODITIES, INDEX_FEEDS, INDEX_SERIES,
        INDEX_VALUES, INDEX_FORECASTS, INDEX_SERIES_QUARTERLY,
        COMBOS, COMBO_LINES, FORMULAS, FAMILIES,
        REGION_BASIS, INDEX_BASIS,
    )
}

# The two decision forms live in decisions/, everything else in tables/.
DECISION_SPECS = {REGION_BASIS.name, INDEX_BASIS.name}


def get_spec(name: str) -> DropTableSpec | None:
    return _REGISTRY.get(name.removesuffix(".csv"))


def all_specs() -> list[DropTableSpec]:
    return list(_REGISTRY.values())


def table_specs() -> list[DropTableSpec]:
    """Everything under tables/ — excludes the two decision forms."""
    return [s for s in _REGISTRY.values() if s.name not in DECISION_SPECS]
