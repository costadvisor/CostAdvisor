"""Negotiation position engine (Scrum 30b).

Turns a costed catalog combo (FormulaTemplate x region x period, resolved by
formula_resolver.evaluate_weighted_template — "the attribution service") plus
a supplier's quoted number into a position a buyer can defend: a target, an
ask, and an explicit unexplained remainder.

Key invariant: evaluate_weighted_template's should_cost already consumes
100% of every line's verified index movement by construction (per-line
contributions sum exactly to should_cost at any period, including the base
period). That means none of the gap between a supplier's ask and our target
can be re-explained by re-crediting the same lines — that would double-count
evidence already spent building the target. The only thing that could
legitimately explain part of an ask is new information not already inside
the target (a documented negotiation note/index dossier) — no such
quantitative evidence source exists anywhere in this repo's data model, so
`attributed_amount`/`evidence` are always 0/None today. They are a real
structural seam, not dead code: a future evidence model plugs in here
without changing the identity `sum(attributed) + unexplained == ask`.

Deliberately does NOT touch costing_engine.py's _apply_margin/_component_base
— those implement the CostModel-path convention (margin stripped out of the
pool and re-applied). The catalog convention (used here, via
evaluate_weighted_template) bakes margin in as a recipe line. Mixing the two
conventions produces an ask wrong by the margin.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.services.formula_resolver import evaluate_weighted_template
from app.services.fx_converter import get_fx_rate
from app.services.unit_converter import convert_price_per_unit
from app.services.incoterm_normalizer import normalize_price


def _empty_position(reason: str | None = None) -> dict:
    return {
        "insufficient": True,
        "reason": reason,
        "ask": None,
        "attributed_components": [],
        "attributed_total": 0.0,
        "unexplained_remainder": None,
    }


def compute_negotiation_position(
    db: Session,
    team_id: uuid.UUID,
    template_id: uuid.UUID,
    region: str,
    year: int,
    quarter: int,
    supplier_price: float,
    supplier_currency: str | None = None,
    supplier_unit: str | None = None,
    supplier_incoterm: str | None = None,
    combo_unit: str | None = None,
    combo_incoterm: str | None = None,
    incoterm_adjustments: dict | None = None,
) -> dict:
    evaluation = evaluate_weighted_template(db, team_id, template_id, region, year, quarter)
    # FormulaEvaluateOut requires template_id, which evaluate_weighted_template's
    # own dict doesn't carry (the /evaluate router supplies it as a sibling
    # kwarg at construction time; here it's a nested field, so it has to be
    # in the dict itself).
    evaluation["template_id"] = template_id

    if not evaluation["evaluable"]:
        return {
            "target": evaluation,
            "normalization": None,
            "position": _empty_position(evaluation["reason"]),
        }

    combo_ccy = evaluation["currency"]
    from_ccy = supplier_currency or combo_ccy
    price = float(supplier_price)
    notes: list[str] = []
    fx_rate_used = None
    unit_factor_used = None
    incoterm_adjustment = None

    # Currency — coverage.currency always exists, so this is always attempted.
    if from_ccy != combo_ccy:
        rate = get_fx_rate(db, from_ccy, combo_ccy, year, quarter, team_id=team_id)
        if rate is not None:
            price = price * rate
            fx_rate_used = rate
        else:
            notes.append(f"No FX rate found for {from_ccy}→{combo_ccy} at Q{quarter}-{year} — compared unconverted")

    # Unit — the combo has no stored unit; only attempted when the caller
    # declares both sides explicitly.
    if combo_unit and supplier_unit and combo_unit != supplier_unit:
        try:
            converted = convert_price_per_unit(price, supplier_unit, combo_unit)
            unit_factor_used = converted / price if price else None
            price = converted
        except ValueError:
            notes.append(f"Unknown unit conversion {supplier_unit}→{combo_unit} — compared unconverted")
    elif (combo_unit or supplier_unit) and combo_unit != supplier_unit:
        notes.append("Unit basis not declared for both sides — compared as quoted")

    # Incoterm — no destination_region on a bare combo, so lane defaults
    # aren't available; only a caller-supplied adjustments dict can correct
    # for a declared mismatch. normalize_price is a documented no-op when
    # adjustments is empty, so an unadjusted mismatch is reported, not hidden.
    if combo_incoterm and supplier_incoterm and combo_incoterm != supplier_incoterm:
        if incoterm_adjustments:
            corrected = normalize_price(price, supplier_incoterm, combo_incoterm, incoterm_adjustments)
            incoterm_adjustment = round(corrected - price, 4)
            price = corrected
        else:
            notes.append(
                f"Incoterm differs ({supplier_incoterm} vs {combo_incoterm}) but no adjustment data supplied — compared as-is"
            )

    normalized_price = round(price, 4)

    normalization = {
        "supplier_price_raw": float(supplier_price),
        "supplier_currency": from_ccy,
        "supplier_unit": supplier_unit,
        "supplier_incoterm": supplier_incoterm,
        "normalized_price": normalized_price,
        "fx_rate_used": fx_rate_used,
        "unit_factor_used": unit_factor_used,
        "incoterm_adjustment": incoterm_adjustment,
        "notes": notes,
    }

    should_cost = evaluation["should_cost"]
    if should_cost is None:
        position = _empty_position("no base price anchor — index level only, cannot compute a priced ask")
    else:
        ask = round(normalized_price - should_cost, 4)
        attributed_components = [
            {**line, "attributed_amount": 0.0, "evidence": None}
            for line in evaluation["lines"]
        ]
        position = {
            "insufficient": False,
            "reason": None,
            "ask": ask,
            "attributed_components": attributed_components,
            "attributed_total": 0.0,
            "unexplained_remainder": ask,
        }

    return {"target": evaluation, "normalization": normalization, "position": position}
