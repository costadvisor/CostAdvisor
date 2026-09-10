"""The two "which column wins" rules for the 2026-07 drop (SCRUM-74 unit 1).

Both facts below are stated twice in the source, and the two statements
disagree on a material slice of the data. Left to each loader, the choice
gets made three times, differently, in a branch nobody reviews. Deciding
once here — with the evidence recorded — is the point of this module.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.drop.normalize import is_fixed_line, parse_number


# ── Rule 1: margin comes from the LINE, not the header ───────────────────────
#
# `combos.margin_pct` and the margin line's own `weight_pct` disagree on 146
# of the 1,061 combos that have a margin line (13.8%), by anywhere from −10 to
# +10 percentage points. The error is per-formula, not per-region: all six
# regional combos of a formula carry the same wrong header.
#
# The line wins, and the reason is arithmetic rather than editorial: every
# combo's line weights sum to exactly 100 **including** the margin line
# (verified on 1,078 of 1,079 — the sole exception has no lines at all). If
# the header were right, the weights would not close. The header value is
# simply stale.
#
# Two consequences worth carrying forward:
#   * `formulas.margin_pct_min/max` is derived from the header, so it inherits
#     the error — the margin line falls outside that range on 144 combos.
#     A loader that trusts the range will reject good data.
#   * This is what the Loader v2 ticket means by "margin is stated twice and
#     the two disagree on a real slice of combos". The ticket says to prefer
#     the line weight "because it belongs to a set summing to 100"; that is
#     the same reasoning, now measured.

MARGIN_LABEL_TOKEN = "margin"


def is_margin_line(line: dict) -> bool:
    """A margin line is a fixed line whose label mentions margin.

    Matching on an exact label does not work: there is no bare "Margin"
    literal anywhere. 1,008 lines read "Supplier margin" and 53 carry an
    annotated variant ("Supplier margin purity premium", "Performance IP
    margin", …).
    """
    if not is_fixed_line(line.get("type_code")):
        return False
    return MARGIN_LABEL_TOKEN in str(line.get("label", "")).lower()


def find_margin_line(lines: list[dict]) -> dict | None:
    """The combo's margin line, or None.

    Exactly one exists on 1,061 of 1,079 combos and it is always the last
    `seq`. The 18 without are the zero-line combo plus the 17 `flat`-shaped
    records, which carry no margin at all — so None is a real answer, not a
    parse failure.
    """
    matches = [line for line in lines if is_margin_line(line)]
    if not matches:
        return None
    return max(matches, key=lambda line: parse_number(line.get("seq")) or 0)


@dataclass(frozen=True)
class MarginResolution:
    margin_pct: float | None
    source: str                  # "line" | "header" | "absent"
    header_value: float | None
    line_value: float | None

    @property
    def disagrees(self) -> bool:
        if self.header_value is None or self.line_value is None:
            return False
        return abs(self.header_value - self.line_value) > 1e-6


def resolve_margin(combo: dict, lines: list[dict]) -> MarginResolution:
    """Authoritative margin for one combo, plus both inputs so a loader can
    record the losing value instead of discarding it."""
    header = parse_number(combo.get("margin_pct"))
    margin_line = find_margin_line(lines)
    line_value = parse_number(margin_line.get("weight_pct")) if margin_line else None

    if line_value is not None:
        return MarginResolution(line_value, "line", header, line_value)
    if header is not None:
        # No margin line to appeal to; the header is all there is.
        return MarginResolution(header, "header", header, None)
    return MarginResolution(None, "absent", None, None)


# ── Rule 2: proxy_status keeps BOTH readings ─────────────────────────────────
#
# `combo_lines.proxy_status` and `type_codes.proxy_status` disagree on 736 of
# the 4,430 joinable lines (16.6%), carrying 18.14% of all cost weight. 103 of
# 191 type-codes are affected, and 103 are not even internally consistent
# across their own lines — so this is per-line drift, not a per-code offset a
# lookup table could repair.
#
# There is no winner, and picking one would be destructive in both directions:
#
#   * The **line** value is what the drop's own `w_direct` / `w_proxy` /
#     `w_unclassified` columns were computed from (verified: 0 mismatches
#     against line-level, 501/461/79 against registry-level), and
#     `coverage_tier` derives from `w_proxy`. Adopting registry truth silently
#     moves 461 combos' proxy weight and a large slice of coverage_tier.
#   * The **registry** value is the better-informed one where the line says
#     `unclassified` (124 lines), and the line understates proxy exposure in
#     the dominant disagreement (384 lines say `direct` where the registry
#     says `proxy` — 12.4% of all cost weight).
#
# So both are stored, and anything that reports proxy exposure states which
# reading it used. SCRUM-80 owns the eventual adjudication; until then, a
# consumer that silently picks one is the failure mode.
#
# Note `resolution` and `resolves_to` agree perfectly across the same join
# (0 mismatches) — proxy_status is the only divergent column.

PROXY_DIRECT = "direct"
PROXY_PROXY = "proxy"
PROXY_UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class ProxyStatusPair:
    line: str | None       # combo_lines.proxy_status ("" on fixed lines)
    registry: str | None   # type_codes.proxy_status (None when unjoinable)

    @property
    def agrees(self) -> bool:
        """Fixed lines have no proxy question, so they are not a disagreement."""
        if self.line is None or self.registry is None:
            return True
        return self.line == self.registry

    @property
    def is_proxy_either_way(self) -> bool:
        """The conservative read — used where understating proxy exposure is
        the worse error (a trust grade, a customer-facing caveat)."""
        return PROXY_PROXY in {self.line, self.registry}


def proxy_status_pair(line: dict, type_code_row: dict | None) -> ProxyStatusPair:
    """Both readings for one cost line, never collapsed to one."""
    if is_fixed_line(line.get("type_code")):
        return ProxyStatusPair(line=None, registry=None)
    raw_line = (line.get("proxy_status") or "").strip() or None
    raw_registry = (
        (type_code_row.get("proxy_status") or "").strip() or None
        if type_code_row
        else None
    )
    return ProxyStatusPair(line=raw_line, registry=raw_registry)


# ── Rule 3 (a corollary, not a conflict): what `loadable` actually means ─────
#
# `combos.loadable` reads like a schema-validity flag and is not one. It is
# exactly `(n_lines > 0) AND (no line resolution in {no_series, ambiguous})`
# — verified against all 1,079 rows with zero false positives or negatives.
#
# That makes it a **pricing-completeness** flag: the 197 `loadable=False`
# combos are excluded because somebody has not bought a price series yet, not
# because their rows are malformed. Filtering a load on it would drop 197
# combos for a purchasing reason while admitting all 115 combos whose
# taxonomy does not resolve. The two gates are orthogonal and a loader needs
# both, separately.

_UNPRICEABLE_RESOLUTIONS = {"no_series", "ambiguous"}


def is_priceable(lines: list[dict]) -> bool:
    """The drop's own `loadable` rule, restated so a loader never has to
    infer it from the flag's misleading name."""
    if not lines:
        return False
    return not any(
        (line.get("resolution") or "").strip() in _UNPRICEABLE_RESOLUTIONS
        for line in lines
    )


def unpriceable_lines(lines: list[dict]) -> list[dict]:
    """The specific lines blocking a combo from being costed — this is what
    "why can't this combo be priced" answers with, rather than a bare flag."""
    return [
        line
        for line in lines
        if (line.get("resolution") or "").strip() in _UNPRICEABLE_RESOLUTIONS
    ]
