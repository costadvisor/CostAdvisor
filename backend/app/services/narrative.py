"""Narrative generation for negotiation briefs (rule-based + LLM-enhanced)."""

import logging

from app.services.ollama import ollama_generate

logger = logging.getLogger(__name__)

_SYMBOL = {"EUR": "€", "USD": "$", "GBP": "£"}


def fmt_money(v: float, currency: str | None, unit: str | None = None, signed: bool = False) -> str:
    """Money the way the app prints it (frontend utils/currency fmtMoney):
    symbol (or ISO code) first, whole units from 100 up and cents below,
    thousands separators, sign before the symbol. fmt_money(-9.0152, "EUR",
    "t") -> "-€9.02/t". Three-decimal euros read as thousands ("€291.161")."""
    dp = 0 if abs(v) >= 100 else 2
    body = f"{abs(v):,.{dp}f}"
    zero = float(body.replace(",", "")) == 0
    sign = "" if zero else "-" if v < 0 else "+" if signed else ""
    sym = _SYMBOL.get(currency or "", f"{currency} " if currency else "")
    return f"{sign}{sym}{body}" + (f"/{unit}" if unit else "")

BRIEF_SYSTEM_PROMPT = (
    "You are a senior procurement cost analyst writing a concise negotiation brief. "
    "Write in third person, use precise numbers from the data provided, and focus on "
    "actionable insights for the buyer. Keep your response to 3-5 sentences. "
    "Do not use markdown formatting, bullet points, or headers."
)


async def generate_enhanced_narrative(
    product_name: str,
    supplier_name: str | None,
    drivers: list[dict],
    gap: float | None,
    gap_pct: float | None,
    total_impact: float | None,
    currency: str,
    period_label: str,
    num_periods: int,
    base_period_label: str = "",
    unit: str | None = None,
) -> str:
    """Attempt LLM-enhanced narrative; fall back to rule-based on any failure."""
    base = generate_narrative(
        product_name, supplier_name, drivers, gap, gap_pct,
        total_impact, currency, period_label, num_periods, base_period_label, unit,
    )
    since = f"since {base_period_label}" if base_period_label else "since the formula's base quarter"

    # Build structured prompt for the LLM. A driver's figure is how far the
    # should-cost moved on that line since the base quarter, not a share of
    # the supplier gap, and is labelled so.
    driver_lines = []
    for d in sorted(drivers, key=lambda x: abs(x["contribution_to_gap"]), reverse=True)[:5]:
        idx = d["index_name"] or d["component_label"]
        driver_lines.append(
            f"- {d['component_label']}: linked to {idx}, "
            f"index {'up' if d['index_change_pct'] >= 0 else 'down'} {abs(d['index_change_pct']):.1f}% {since}, "
            f"moving the should-cost by {fmt_money(d['contribution_to_gap'], currency, unit, signed=True)} {since} "
            f"({d['direction']})"
        )

    gap_str = (f"{fmt_money(gap, currency, unit, signed=True)} ({gap_pct:+.1f}% of should-cost)"
               if gap is not None and gap_pct is not None else "N/A")
    impact_str = fmt_money(total_impact, currency, signed=True) if total_impact is not None else "N/A"

    prompt = (
        f"Product: {product_name}\n"
        f"Supplier: {supplier_name or 'Unknown'}\n"
        f"Period: {period_label} ({num_periods} quarters)\n"
        f"Currency: {currency}\n\n"
        f"Should-cost movement per cost line {since} (not a split of the price gap):\n"
        + "\n".join(driver_lines) + "\n\n"
        f"Price gap today (actual vs should-cost): {gap_str}\n"
        f"Total financial impact over period: {impact_str}\n\n"
        f"Base analysis: {base}\n\n"
        f"Rewrite this analysis in a more insightful way. Highlight which cost driver "
        f"matters most and why, whether the buyer has negotiation leverage, and what "
        f"action they should consider. Be specific with the numbers provided."
    )

    try:
        enhanced = await ollama_generate(prompt, system=BRIEF_SYSTEM_PROMPT)
        return enhanced if enhanced else base
    except Exception:
        logger.warning("LLM narrative enhancement failed", exc_info=True)
        return base


def generate_narrative(
    product_name: str,
    supplier_name: str | None,
    drivers: list[dict],
    gap: float | None,
    gap_pct: float | None,
    total_impact: float | None,
    currency: str,
    period_label: str,
    num_periods: int,
    base_period_label: str = "",
    unit: str | None = None,
) -> str:
    """
    Generate a rule-based negotiation narrative.

    drivers: list of dicts with keys:
        component_label, index_name, index_change_pct, contribution_to_gap, direction
    """
    parts = []

    # Opening
    supplier_str = f" from {supplier_name}" if supplier_name else ""
    parts.append(
        f"Analysis of {product_name}{supplier_str} over {period_label} "
        f"({num_periods} periods)."
    )

    # Top drivers. Each figure is the should-cost's movement on that line
    # since the formula's base quarter, so the sentence says "since", not
    # "over the period" (the brief's window is a different, shorter span).
    since = f"since {base_period_label}" if base_period_label else "since the formula's base quarter"
    top = sorted(drivers, key=lambda d: abs(d["contribution_to_gap"]), reverse=True)[:3]
    for d in top:
        direction = "rose" if d["direction"] == "up" else "fell" if d["direction"] == "down" else "remained flat"
        change = abs(d["index_change_pct"])

        idx_name = d["index_name"] or d["component_label"]
        contribution = fmt_money(abs(d["contribution_to_gap"]), currency, unit)

        impact_word = "adding" if d["contribution_to_gap"] >= 0 else "taking"
        to_from = "to" if d["contribution_to_gap"] >= 0 else "off"

        parts.append(
            f"{d['component_label']} is linked to {idx_name}, which {direction} "
            f"{change:.1f}% {since}, {impact_word} {contribution} {to_from} the should-cost."
        )

    # Verdict
    if gap is not None and total_impact is not None:
        if gap > 0.001:
            parts.append(
                f"Actual pricing has not reflected these cost movements, "
                f"generating an estimated {fmt_money(abs(total_impact), currency)} "
                f"desqueeze (supplier margin expansion) over the period."
            )
        elif gap < -0.001:
            parts.append(
                f"The supplier has absorbed cost increases, resulting in an "
                f"estimated {fmt_money(abs(total_impact), currency)} squeeze "
                f"(supplier margin compression) over the period."
            )
        else:
            parts.append(
                "Pricing has tracked should-cost within tolerance over the period."
            )
    elif gap is not None:
        if gap > 0.001:
            parts.append(
                f"Current actual price exceeds should-cost by "
                f"{fmt_money(abs(gap), currency, unit)} ({abs(gap_pct or 0):.1f}%), "
                f"suggesting potential for negotiation."
            )
        elif gap < -0.001:
            parts.append(
                f"Current actual price is below should-cost by "
                f"{fmt_money(abs(gap), currency, unit)}, indicating supplier margin compression."
            )

    return " ".join(parts)
