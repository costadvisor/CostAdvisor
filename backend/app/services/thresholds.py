"""The one threshold accessor (Wave 3, SCRUM-79 / MON-1).

Before this, `alert_subscriptions.threshold_pct` was the only threshold: per
subscription, percent only, default 5. The MON-1 ticket also specified a
per-team setting with a %-or-absolute unit and a 10% default. Shipping both as
written would have left two live thresholds with different defaults and no rule
for which wins — users hit that within a week of both existing.

Reconciliation: **team default, per-subscription override, one accessor.** The
radar and the alert layer both read through `effective_threshold`; neither
reads a column directly. That is the whole point — changing the team default
has to move the fire boundary for every subscription that has not overridden
it, and that is only true if nobody bypasses this function.

The unit travels with the value. An absolute-currency threshold only means
anything where a base price and an actual price exist; the platform index layer
is an index level (base 100), where nothing is money — so a caller that cannot
honour a currency unit has to be able to see that it was asked to.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.team import Team

DEFAULT_THRESHOLD_PCT = 10.0
DEFAULT_THRESHOLD_UNIT = "pct"

UNIT_PCT = "pct"
UNIT_CURRENCY = "currency"
THRESHOLD_UNITS = (UNIT_PCT, UNIT_CURRENCY)


@dataclass(frozen=True)
class Threshold:
    value: float
    unit: str
    # Which side supplied it — so a UI can show "inherited" rather than
    # implying the user chose 10.
    source: str          # "subscription" | "team_default" | "fallback"

    @property
    def is_pct(self) -> bool:
        return self.unit == UNIT_PCT


def effective_threshold(
    db: Session,
    team_id: uuid.UUID,
    subscription=None,
) -> Threshold:
    """The threshold that actually applies, and where it came from.

    A subscription overrides only when it states a value; a null
    `threshold_pct` means "inherit", which is what the MON-1 migration made
    possible (the column was NOT NULL with a default of 5 before).
    """
    if subscription is not None and subscription.threshold_pct is not None:
        return Threshold(
            value=float(subscription.threshold_pct),
            unit=subscription.threshold_unit or UNIT_PCT,
            source="subscription",
        )

    team = db.query(Team).filter(Team.id == team_id).first()
    if team is None:
        # No team row to read — report the fallback rather than raising, so a
        # radar run over a deleted team degrades instead of dying mid-pass.
        return Threshold(DEFAULT_THRESHOLD_PCT, DEFAULT_THRESHOLD_UNIT, "fallback")

    return Threshold(
        value=float(team.default_threshold_pct),
        unit=team.default_threshold_unit or DEFAULT_THRESHOLD_UNIT,
        source="team_default",
    )
