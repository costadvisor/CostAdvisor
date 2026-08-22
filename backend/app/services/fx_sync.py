"""
Sync FX-category commodity index values into the fx_rates table.

After FX exchange rate indexes (EUR/USD, GBP/EUR, etc.) are scraped,
this module copies their values into the fx_rates table so the cost
engine and portfolio calculations can use them for currency conversion.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.index_data import CommodityIndex, IndexValue
from app.models.fx_pair import FxPair
from app.models.fx_rate import FxRate


def sync_fx_rates(db: Session) -> int:
    """
    Copy FX-category index values into the fx_rates table.
    Pair mapping is now read from the fx_pairs DB table instead of hardcoded.
    Returns the number of rows upserted.
    """
    # Build name→(from, to) map from fx_pairs table
    pairs = db.query(FxPair).all()
    pair_map = {p.name: (p.from_currency, p.to_currency) for p in pairs}

    fx_commodities = (
        db.query(CommodityIndex)
        .filter(CommodityIndex.category == "FX")
        .all()
    )

    count = 0
    for commodity in fx_commodities:
        pair = pair_map.get(commodity.name)
        if not pair:
            continue
        from_ccy, to_ccy = pair

        values = (
            db.query(IndexValue)
            .filter(IndexValue.commodity_id == commodity.id)
            .all()
        )

        for iv in values:
            existing = db.query(FxRate).filter(
                FxRate.from_currency == from_ccy,
                FxRate.to_currency == to_ccy,
                FxRate.year == iv.year,
                FxRate.quarter == iv.quarter,
            ).first()

            if existing:
                existing.rate = iv.value
                existing.uploaded_at = datetime.now(timezone.utc)
                existing.uploaded_by = None
            else:
                db.add(FxRate(
                    from_currency=from_ccy,
                    to_currency=to_ccy,
                    year=iv.year,
                    quarter=iv.quarter,
                    rate=iv.value,
                    uploaded_by=None,
                ))
            count += 1

    db.commit()
    return count
