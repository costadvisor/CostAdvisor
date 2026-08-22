"""FX rate lookup and currency conversion.

Priority (per period):
  1. Team custom_fx_rates — resolved by value_type:
       "fixed"      → stored rate
       "live"       → fx_pairs.live_rate (dynamic, fetched fresh each run)
       "quarter_ref"→ platform fx_rates for (ref_year, ref_quarter)
  2. Platform quarterly rate — fx_rates for (from, to, year, quarter)
  3. Platform live rate fallback — fx_pairs.live_rate
"""
import uuid
from sqlalchemy.orm import Session

from app.models.fx_rate import FxRate
from app.models.custom_fx_rate import CustomFxRate
from app.models.fx_pair import FxPair


def _platform_quarterly(
    db: Session, from_ccy: str, to_ccy: str, year: int, quarter: int
) -> float | None:
    rate = db.query(FxRate).filter(
        FxRate.from_currency == from_ccy,
        FxRate.to_currency == to_ccy,
        FxRate.year == year,
        FxRate.quarter == quarter,
    ).first()
    if rate:
        return float(rate.rate)
    inv = db.query(FxRate).filter(
        FxRate.from_currency == to_ccy,
        FxRate.to_currency == from_ccy,
        FxRate.year == year,
        FxRate.quarter == quarter,
    ).first()
    if inv and float(inv.rate) != 0:
        return 1.0 / float(inv.rate)
    return None


def _live_rate(db: Session, from_ccy: str, to_ccy: str) -> float | None:
    pair = db.query(FxPair).filter(
        FxPair.from_currency == from_ccy,
        FxPair.to_currency == to_ccy,
    ).first()
    if pair and pair.live_rate is not None:
        return float(pair.live_rate)
    inv = db.query(FxPair).filter(
        FxPair.from_currency == to_ccy,
        FxPair.to_currency == from_ccy,
    ).first()
    if inv and inv.live_rate is not None and float(inv.live_rate) != 0:
        return 1.0 / float(inv.live_rate)
    return None


def _resolve_custom(db: Session, custom: CustomFxRate) -> float | None:
    if custom.value_type == "fixed":
        return float(custom.rate) if custom.rate is not None else None
    if custom.value_type == "live":
        return _live_rate(db, custom.from_currency, custom.to_currency)
    if custom.value_type == "quarter_ref":
        return _platform_quarterly(
            db, custom.from_currency, custom.to_currency,
            custom.ref_year, custom.ref_quarter,
        )
    return None


def get_fx_rate(
    db: Session,
    from_ccy: str,
    to_ccy: str,
    year: int,
    quarter: int,
    team_id: uuid.UUID | str | None = None,
) -> float | None:
    """Look up FX rate for a given period and team."""
    if from_ccy == to_ccy:
        return 1.0

    # 1. Team custom override
    if team_id:
        custom = db.query(CustomFxRate).filter(
            CustomFxRate.team_id == team_id,
            CustomFxRate.from_currency == from_ccy,
            CustomFxRate.to_currency == to_ccy,
            CustomFxRate.year == year,
            CustomFxRate.quarter == quarter,
        ).first()
        if custom:
            v = _resolve_custom(db, custom)
            if v is not None:
                return v

        # Try inverse custom
        inv_custom = db.query(CustomFxRate).filter(
            CustomFxRate.team_id == team_id,
            CustomFxRate.from_currency == to_ccy,
            CustomFxRate.to_currency == from_ccy,
            CustomFxRate.year == year,
            CustomFxRate.quarter == quarter,
        ).first()
        if inv_custom:
            inv_custom_swapped = _SwappedCustom(inv_custom)
            v = _resolve_custom(db, inv_custom_swapped)
            if v is not None and v != 0:
                return 1.0 / v

    # 2. Platform quarterly rate
    rate = _platform_quarterly(db, from_ccy, to_ccy, year, quarter)
    if rate is not None:
        return rate

    # 3. Live rate fallback
    return _live_rate(db, from_ccy, to_ccy)


class _SwappedCustom:
    """Adapter to resolve an inverse custom rate as if it were the direct direction."""
    def __init__(self, original: CustomFxRate):
        self.value_type = original.value_type
        self.rate = original.rate
        # For live/quarter_ref resolution keep original currencies (the live lookup
        # also checks inverse, so we use original from/to)
        self.from_currency = original.from_currency
        self.to_currency = original.to_currency
        self.ref_year = original.ref_year
        self.ref_quarter = original.ref_quarter


def convert_price(
    db: Session,
    value: float,
    from_ccy: str,
    to_ccy: str,
    year: int,
    quarter: int,
    team_id: uuid.UUID | str | None = None,
) -> float:
    """Convert a price from one currency to another. Returns original if no rate found."""
    rate = get_fx_rate(db, from_ccy, to_ccy, year, quarter, team_id=team_id)
    if rate is None:
        return value
    return value * rate
