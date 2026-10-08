"""Which catalogue cards are listed, and which sit in the default view.

One rule, one home (design §2.3). Every list, facet, picker, directory and
export over platform templates uses it: the Intelligence APIs, `GET
/api/formulas/`, the sheet exports, the review queue.

* **Listed** = `card_kind` is `product` or `group`, and the template has at
  least one coverage row that is not withdrawn. Absorbed, pointer, duplicate
  and withdrawn cards are never listed, nor is a card with no formula.
* **Default view** = listed, and `supply_status` is `live` or
  `supply_exception`. The Intelligence grid opens on it; the other statuses
  sit behind its status filter.
* **Answered by id** = every template. A by-id read never applies either
  clause (a team link to an absorbed or pointer card still opens).

`listed_clause()` is a **platform** predicate: it reads the card fields the
content loader writes. A team's own templates (forks, hand-built formulas) are
always listed for that team, so a query mixing both uses
`platform_or_team_listed_clause()`, which says exactly that.

Both clauses take the template entity to correlate against, so they work with
`aliased(FormulaTemplate)` too. The in-memory twins (`is_listed`,
`in_default_view`) serve code that has already loaded the rows, such as the
catalogue snapshot.

The display vocabulary lives here too, so every screen and API says the same
words: the status badges (owner decision Q1; a badge never carries a number)
and the makers' evidence labels.
"""
from __future__ import annotations

from sqlalchemy import and_, exists, false, or_
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.models.formula_template import (
    CARD_KINDS, SUPPLY_STATUSES, FormulaRegionCoverage, FormulaTemplate,
)
from app.models.producer import EVIDENCE_LABELS

LISTED_KINDS: tuple[str, ...] = ("product", "group")
DEFAULT_VIEW_STATUSES: tuple[str, ...] = ("live", "supply_exception")
# Sort rank within a family: verified first.
STATUS_RANK: dict[str, int] = {code: pos for pos, code in enumerate(SUPPLY_STATUSES)}

# code → (label, tone, one-line description). No counts, ever (house rules 4
# and 10b): the badge says whether the supplier floor is met, nothing else.
STATUS_BADGES: dict[str, tuple[str, str, str]] = {
    "live": ("Verified makers", "green",
             "Independent makers of this exact product are verified on their own pages."),
    "supply_exception": ("Concentrated supply", "green-amber",
                         "Few makers exist, and the evidence shows the market really is that "
                         "concentrated."),
    "supply_pending": ("Supply pending", "amber",
                       "Maker verification for this exact product or grade is still under way."),
    "not_audited": ("Makers not yet verified", "grey",
                    "The makers listed here have not been checked against their own pages yet."),
}
assert set(STATUS_BADGES) == set(SUPPLY_STATUSES)
assert set(LISTED_KINDS) <= set(CARD_KINDS)

# A supplier row's evidence label (`producer_formulas.evidence_label`, design
# §2.4) as screens show it, and the one origin marker shown next to it. Weak
# makers are shown, marked "Weak evidence" (owner decision, TRIM T6).
EVIDENCE_LABEL_TEXT: dict[str, str] = {
    "distributor": "Distributor",
    "family_only": "Makes the product family",
    "unverified": "Manufacture not verified",
    "weak": "Weak evidence",
    "not_counted": "Not counted",
    "verified": "Verified maker",
    "not_audited": "Not yet audited",
}
assert set(EVIDENCE_LABEL_TEXT) == set(EVIDENCE_LABELS)
ORIGIN_RESTRICTION_TEXT = "Sanctioned origin: check eligibility for your market"


def status_badge(code: str | None) -> dict | None:
    """`{code, label, tone, description}` for a supply status, or None (a card
    with no status: absorbed, pointer, duplicate, withdrawn)."""
    if code not in STATUS_BADGES:
        return None
    label, tone, description = STATUS_BADGES[code]
    return {"code": code, "label": label, "tone": tone, "description": description}


def live_coverage_exists(template=FormulaTemplate) -> ColumnElement[bool]:
    """EXISTS a coverage row of `template` that is not withdrawn (correlated).

    The inner coverage is its own alias and is never correlated, so a query
    that also selects from `formula_region_coverage` (the review queue, the
    sheet export) keeps the EXISTS whole instead of losing its FROM to
    auto-correlation."""
    cov = aliased(FormulaRegionCoverage)
    return (
        exists()
        .where(cov.template_id == template.id, cov.withdrawn_at.is_(None))
        .correlate_except(cov)
    )


def listed_clause(template=FormulaTemplate) -> ColumnElement[bool]:
    """A platform template that is listed (see the module docstring)."""
    return and_(template.card_kind.in_(LISTED_KINDS), live_coverage_exists(template))


def default_view_clause(template=FormulaTemplate) -> ColumnElement[bool]:
    """A platform template in the default view: listed, and verified or
    concentrated supply."""
    return and_(listed_clause(template), template.supply_status.in_(DEFAULT_VIEW_STATUSES))


def platform_or_team_listed_clause(team_id=None, template=FormulaTemplate) -> ColumnElement[bool]:
    """A platform template that is listed, or one of `team_id`'s own
    templates (always listed for that team). With `team_id` None it is the
    platform rule alone."""
    platform = and_(template.team_id.is_(None), listed_clause(template))
    team = template.team_id == team_id if team_id is not None else false()
    return or_(platform, team)


def is_listed(card_kind: str | None, has_live_coverage: bool) -> bool:
    """In-memory twin of `listed_clause()` for a platform template."""
    return card_kind in LISTED_KINDS and bool(has_live_coverage)


def in_default_view(card_kind: str | None, supply_status: str | None,
                    has_live_coverage: bool) -> bool:
    """In-memory twin of `default_view_clause()` for a platform template."""
    return is_listed(card_kind, has_live_coverage) and supply_status in DEFAULT_VIEW_STATUSES
