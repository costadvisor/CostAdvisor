"""Scrum 29 — check a supplier's claims against what the indices actually did.

The arithmetic this rests on, which is counter-intuitive and worth stating
before reading the verdicts: **the should-cost has already consumed 100% of
every verified index movement.** `evaluate_weighted_template` and the costing
engine both build the number by applying each line's real ratio, so a driver
the supplier cites cannot justify anything *on top of* the should-cost — it is
already in there. Whatever they name, the honest answer ends the same way, and
everything above should-cost is unexplained by construction.

That is why no verdict here ever adds to the target. The verdicts differ only
in how a claim fails: overstated, contradicted, not a cost line, or true and
already priced.

Nothing is stored. The verdict is computed from the live brief each time,
because the driver's real movement changes as index data lands and a stored
verdict would silently become a different answer from the one the numbers now
support.
"""
from __future__ import annotations

from dataclasses import dataclass

# A claim within this much of the real movement is "about right", not
# overstated. Suppliers round; arguing with 11.4 vs 12 loses the room and the
# point is the 30-vs-11 cases.
CLAIM_TOLERANCE_PCT = 2.0
# Below this, a driver did not really move, whatever anyone says about it.
FLAT_PCT = 0.5

VERDICTS = {
    "already_priced": "Real, and already inside the should-cost",
    "overstated": "Overstated",
    "contradicted": "Contradicted — it moved the other way",
    "no_movement": "That input did not move",
    "out_of_scope": "Not a cost line in this product",
    "unmapped": "No driver chosen yet",
}


@dataclass
class CheckedClaim:
    claim_id: str
    said: str
    driver_label: str | None
    claimed_change_pct: float | None
    actual_change_pct: float | None
    weight_pct: float | None
    verdict: str
    verdict_label: str
    note: str
    include_in_script: bool


def _weight_pct(driver, total_cost: float | None) -> float | None:
    if not total_cost or driver.component_cost is None:
        return None
    return round(driver.component_cost / total_cost * 100, 1)


def check_claim(claim, drivers, should_cost: float | None) -> CheckedClaim:
    """One claim against the brief's own drivers.

    `drivers` are `BriefDriver`s straight off `calculate_brief` — the same
    numbers the brief shows, never recomputed here, so the script and the brief
    cannot disagree.
    """
    claimed = float(claim.claimed_change_pct) if claim.claimed_change_pct is not None else None
    total = sum(d.component_cost for d in drivers if d.component_cost is not None) or should_cost

    if claim.driver_label is None:
        # Deliberately two different states. "I have not said which input this
        # is about" is a to-do; "this is about something that is not an input"
        # is a finished rebuttal, and collapsing them would lose the second.
        return CheckedClaim(
            claim_id=str(claim.id), said=claim.said, driver_label=None,
            claimed_change_pct=claimed, actual_change_pct=None, weight_pct=None,
            verdict="unmapped", verdict_label=VERDICTS["unmapped"],
            note="Pick the cost line this is about, or mark it as not one of them.",
            include_in_script=claim.include_in_script,
        )

    driver = next((d for d in drivers
                   if d.component_label == claim.driver_label
                   or (d.index_name and d.index_name == claim.driver_label)), None)
    if driver is None:
        return CheckedClaim(
            claim_id=str(claim.id), said=claim.said, driver_label=claim.driver_label,
            claimed_change_pct=claimed, actual_change_pct=None, weight_pct=None,
            verdict="out_of_scope", verdict_label=VERDICTS["out_of_scope"],
            note=(f"“{claim.driver_label}” is not one of this product's cost lines, so it cannot "
                  "move this price. If it belongs in the recipe, that is a formula change, not a price rise."),
            include_in_script=claim.include_in_script,
        )

    actual = float(driver.index_change_pct)
    weight = _weight_pct(driver, total)
    share = f" At {weight}% of the recipe" if weight is not None else " At its weight in the recipe"

    if abs(actual) < FLAT_PCT:
        verdict = "no_movement"
        note = (f"{driver.index_name or driver.component_label} is flat over this window "
                f"({actual:+.1f}%). There is nothing here to pass on.")
    elif claimed is not None and actual * claimed < 0:
        verdict = "contradicted"
        note = (f"{driver.index_name or driver.component_label} moved {actual:+.1f}%, the opposite direction. "
                "This argues the price down, not up.")
    elif claimed is not None and abs(claimed) - abs(actual) > CLAIM_TOLERANCE_PCT:
        verdict = "overstated"
        note = (f"Our reading of {driver.index_name or driver.component_label} is {actual:+.1f}%, "
                f"not {claimed:+.1f}%.{share} that is already inside the should-cost.")
    else:
        verdict = "already_priced"
        note = (f"{driver.index_name or driver.component_label} moved {actual:+.1f}%, which matches."
                f"{share} it is already carried in the should-cost — worth conceding out loud, because "
                "it costs nothing and buys credibility on the rest.")

    return CheckedClaim(
        claim_id=str(claim.id), said=claim.said, driver_label=claim.driver_label,
        claimed_change_pct=claimed, actual_change_pct=actual, weight_pct=weight,
        verdict=verdict, verdict_label=VERDICTS[verdict], note=note,
        include_in_script=claim.include_in_script,
    )


def _money(v: float, currency: str, unit: str) -> str:
    return f"{v:,.2f} {currency}/{unit}"


def build_script(brief, checked: list[CheckedClaim]) -> list[str]:
    """Deterministic template text over the brief's own numbers.

    Template first, numbers from the engine, and an LLM only ever smoothing the
    prose around them — never the source of a figure. `services/narrative.py`
    is available for that smoothing and is deliberately not called here.
    """
    cur, unit = brief.currency, brief.unit
    lines: list[str] = []

    opening = (f"We have modelled {brief.product_name} bottom-up from published indices. "
               f"At {brief.period_label} the defensible number is "
               f"{_money(brief.current_should_cost, cur, unit)}.")
    if brief.current_actual_price is not None and brief.gap is not None:
        direction = "above" if brief.gap > 0 else "below"
        opening += (f" Your current price is {_money(brief.current_actual_price, cur, unit)}, "
                    f"{_money(abs(brief.gap), cur, unit)} {direction} that.")
    lines.append(opening)

    for c in checked:
        if not c.include_in_script:
            continue
        lines.append(f"On “{c.said}” — {c.note}")

    close = f"We are ready to settle at {_money(brief.current_should_cost, cur, unit)}."
    if brief.current_floor is not None:
        close += (f" Below {_money(brief.current_floor, cur, unit)} there is no margin left in the chain, "
                  "so that is not a number we expect you to accept.")
    lines.append(close)

    if brief.data_gaps:
        # Said in the script, not just shown on the page: walking in citing a
        # line that rode flat because its index is missing is how a buyer loses
        # the room.
        lines.append(
            f"Before the call: {len(brief.data_gaps)} cost line(s) in this model have no index data and are "
            "riding flat, so the target is conservative. Check them rather than quoting them."
        )
    return lines
