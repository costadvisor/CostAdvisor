"""Vocabularies for CommodityIndex metadata + proxy mapping (Scrum 57).

Single source of truth for the enum values, validated at the API/seed layer.
Kept as plain tuples (like constants/incoterms.py) rather than DB enums so the
admin proxy editor (SCRUM-67) and FD-1 executor (SCRUM-80) can share them.
"""

# How the real feed is licensed.
ACCESS_TIERS = ("Free", "Partial", "Subscription")

# Refresh cadence. `frequency` already exists on CommodityIndex — this is its
# canonical vocabulary (also reused for a proxy's recalibration cadence).
FREQUENCIES = ("Daily", "Weekly", "Monthly", "Quarterly", "Annual", "Irregular")

# What the index feeds in a should-cost formula.
ROLES = ("feedstock", "energy", "fixed")

# How reliably we can get a live number for the index:
#   free       — a free public feed gives the real number directly
#   good_proxy — a solid free-data approximation
#   weak_proxy — a rough free-data approximation (a softer signal)
#   blocked    — no free source or proxy; needs a paid feed or manual entry
RETRIEVAL_STATUSES = ("free", "good_proxy", "weak_proxy", "blocked")

# Operations FD-1 (SCRUM-80) can execute against a base index to derive an estimate.
PROXY_OPERATIONS = ("passthrough", "ratio", "multiply", "add", "spread", "regression")

# Allowed keys of the structured proxy_logic spec (JSONB). Free-text lives in `note`.
PROXY_LOGIC_KEYS = {"base_index", "operation", "spread", "spread_unit", "recalibration", "note"}


def validate_proxy_logic(spec):
    """Validate a structured proxy_logic dict; return it, or raise ValueError.

    None is allowed (no proxy). The executable params (base_index/operation/spread/
    recalibration) may be null when only the analyst `note` is known — they get
    filled in via the admin editor (SCRUM-67).
    """
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise ValueError("proxy_logic must be an object")
    unknown = set(spec) - PROXY_LOGIC_KEYS
    if unknown:
        raise ValueError(f"proxy_logic has unknown keys: {sorted(unknown)}")
    op = spec.get("operation")
    if op is not None and op not in PROXY_OPERATIONS:
        raise ValueError(f"proxy_logic.operation must be one of {PROXY_OPERATIONS}")
    recal = spec.get("recalibration")
    if recal is not None and recal not in FREQUENCIES:
        raise ValueError(f"proxy_logic.recalibration must be one of {FREQUENCIES}")
    spread = spec.get("spread")
    if spread is not None and not isinstance(spread, (int, float)):
        raise ValueError("proxy_logic.spread must be a number")
    unit = spec.get("spread_unit")
    if unit is not None and unit not in ("abs", "pct"):
        raise ValueError("proxy_logic.spread_unit must be 'abs' or 'pct'")
    return spec


def detect_expression_vars(expression: str) -> set[str]:
    """Return the identifier names referenced by an advanced expression, excluding
    the whitelisted function names. Mirrors the frontend `detectVars`/`stripReservedFns`
    and the evaluator's bracket→paren normalisation."""
    import ast as _ast
    from app.services.costing_engine import _SAFE_FUNCS

    expr = (expression or "").replace("[", "(").replace("]", ")")
    tree = _ast.parse(expr, mode="eval")  # raises SyntaxError on a malformed expression
    names = {n.id for n in _ast.walk(tree) if isinstance(n, _ast.Name)}
    return names - set(_SAFE_FUNCS)


def validate_composite_structure(expression, variables):
    """Validate a composite index's expression + variable map (structure only — DB
    checks like 'commodity exists' / 'no self-reference' stay in the router).

    Returns (expression, variables) normalised, or raises ValueError. A null/blank
    expression clears the composite (returns (None, None))."""
    if expression is None or not str(expression).strip():
        return None, None
    expression = str(expression).strip()
    variables = variables or {}
    if not isinstance(variables, dict):
        raise ValueError("composite_variables must be an object")

    try:
        detected = detect_expression_vars(expression)
    except SyntaxError as e:
        raise ValueError(f"composite_expression is not a valid expression: {e}")

    for name, spec in variables.items():
        if not isinstance(spec, dict):
            raise ValueError(f"variable '{name}' must be an object")
        vtype = spec.get("type")
        if vtype == "index":
            if not isinstance(spec.get("commodity_id"), int):
                raise ValueError(f"index variable '{name}' needs an integer commodity_id")
            # Optional region pin. The same commodity commonly carries different
            # values per region (Iron has both a Europe and a GLOBAL series), and
            # without a pin every variable resolved at whatever region the composite
            # was asked for — so the two were indistinguishable and unselectable.
            # Omitted/None keeps the original behaviour: follow the requested region,
            # then the resolver's Europe → GLOBAL → any fallback.
            region = spec.get("region")
            if region is not None:
                if not isinstance(region, str) or not region.strip():
                    raise ValueError(f"index variable '{name}' region must be a non-empty string")
                if len(region.strip()) > 32:
                    raise ValueError(f"index variable '{name}' region is too long")
                spec["region"] = region.strip()
        elif vtype == "fixed":
            if not isinstance(spec.get("value"), (int, float)):
                raise ValueError(f"fixed variable '{name}' needs a numeric value")
        else:
            raise ValueError(f"variable '{name}' type must be 'index' or 'fixed'")

    missing = detected - set(variables)
    if missing:
        raise ValueError(f"expression references undefined variables: {sorted(missing)}")
    return expression, variables
