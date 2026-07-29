"""
Data resolver: implements the override hierarchy.
Priority: team override > scraped value > fallback.
"""
import uuid
from sqlalchemy.orm import Session
from sqlalchemy import text, and_, or_

from app.models.index_data import CommodityIndex, IndexValue, IndexOverride, TeamIndexSource
from app.models.user import User
from app.schemas.index_data import IndexValueOut
from app.services.scraper import SCRAPER_REGISTRY, SCRAPER_SOURCE_LABELS


def resolve_index_values(
    db: Session,
    team_id: uuid.UUID,
    region: str | None = None,
    commodity_name: str | None = None,
    year: int | None = None,
    quarter: int | None = None,
    commodity_ids: set[int] | None = None,
    from_year: int | None = None,
    from_quarter: int | None = None,
    to_year: int | None = None,
    to_quarter: int | None = None,
) -> list[IndexValueOut]:
    """
    Get index values with team overrides applied.
    Returns a flat list of values, with override values replacing scraped values where they exist.
    Enriched with scraped_value, override_id, override_by, override_at.
    """
    # Alias to avoid shadowing by the override-query loop variable below
    commodity_name_filter = commodity_name
    # Build set of commodity names that have built-in scrapers
    scraped_commodities = set(SCRAPER_REGISTRY.keys())

    # Build base query for scraped values
    query = (
        db.query(
            IndexValue.commodity_id,
            CommodityIndex.name.label("commodity_name"),
            IndexValue.region,
            IndexValue.year,
            IndexValue.quarter,
            IndexValue.value,
            IndexValue.scraped_at,
        )
        .join(CommodityIndex, CommodityIndex.id == IndexValue.commodity_id)
    )

    if region:
        query = query.filter(IndexValue.region == region)
    if commodity_name_filter:
        query = query.filter(CommodityIndex.name == commodity_name_filter)
    if year:
        query = query.filter(IndexValue.year == year)
    if quarter:
        query = query.filter(IndexValue.quarter == quarter)

    # Product/supplier filter: restrict to specific commodity IDs
    if commodity_ids is not None:
        if not commodity_ids:
            return []  # No matching commodities
        query = query.filter(IndexValue.commodity_id.in_(commodity_ids))

    # Time range filter
    if from_year is not None and from_quarter is not None:
        query = query.filter(or_(
            IndexValue.year > from_year,
            and_(IndexValue.year == from_year, IndexValue.quarter >= from_quarter),
        ))
    if to_year is not None and to_quarter is not None:
        query = query.filter(or_(
            IndexValue.year < to_year,
            and_(IndexValue.year == to_year, IndexValue.quarter <= to_quarter),
        ))

    scraped = query.all()

    # Build dict of overrides for this team, storing full objects + user display name + commodity name
    override_query = (
        db.query(IndexOverride, User.display_name, CommodityIndex.name.label("commodity_name"))
        .outerjoin(User, User.id == IndexOverride.uploaded_by)
        .join(CommodityIndex, CommodityIndex.id == IndexOverride.commodity_id)
        .filter(IndexOverride.team_id == team_id)
    )
    if region:
        override_query = override_query.filter(IndexOverride.region == region)
    if year:
        override_query = override_query.filter(IndexOverride.year == year)
    if quarter:
        override_query = override_query.filter(IndexOverride.quarter == quarter)

    overrides = {}
    for o, display_name, ovr_commodity_name in override_query.all():
        key = (o.commodity_id, o.region, o.year, o.quarter)
        overrides[key] = (o, display_name, ovr_commodity_name)

    # Merge: override wins
    results = []
    seen_keys = set()
    for row in scraped:
        key = (row.commodity_id, row.region, row.year, row.quarter)
        seen_keys.add(key)
        override_entry = overrides.get(key)

        # Determine global scraper info for this commodity
        gs = SCRAPER_SOURCE_LABELS.get(row.commodity_name) if row.commodity_name in scraped_commodities else None
        gs_at = row.scraped_at.isoformat() if row.scraped_at else None

        if override_entry:
            o, display_name, _ = override_entry
            results.append(IndexValueOut(
                commodity_id=row.commodity_id,
                commodity_name=row.commodity_name,
                region=row.region,
                year=row.year,
                quarter=row.quarter,
                value=float(o.value) if o.value is not None else None,
                source="team_blank" if o.value is None else "team_override",
                scraped_value=float(row.value),
                override_id=o.id,
                override_by=display_name,
                override_at=o.uploaded_at.isoformat() if o.uploaded_at else None,
                global_scraper=gs,
                global_scrape_at=gs_at,
            ))
        else:
            results.append(IndexValueOut(
                commodity_id=row.commodity_id,
                commodity_name=row.commodity_name,
                region=row.region,
                year=row.year,
                quarter=row.quarter,
                value=float(row.value),
                source="scraped",
                scraped_value=float(row.value),
                global_scraper=gs,
                global_scrape_at=gs_at,
            ))

    # Include override-only rows — team-scraped data that has no matching global IndexValue.
    # This happens when a commodity has no seed data and the nightly Celery scraper hasn't run yet.
    for key, (o, display_name, ovr_commodity_name) in overrides.items():
        if key in seen_keys:
            continue  # already emitted above
        if o.value is None:
            continue  # intentional blank — nothing to show
        commodity_id, ovr_region, ovr_year, ovr_quarter = key
        # Apply the same filters that were applied to the global query
        if region and ovr_region != region:
            continue
        if commodity_name_filter and ovr_commodity_name != commodity_name_filter:
            continue
        if commodity_ids is not None and commodity_id not in commodity_ids:
            continue
        if from_year is not None and from_quarter is not None:
            if not (ovr_year > from_year or (ovr_year == from_year and ovr_quarter >= from_quarter)):
                continue
        if to_year is not None and to_quarter is not None:
            if not (ovr_year < to_year or (ovr_year == to_year and ovr_quarter <= to_quarter)):
                continue
        gs = SCRAPER_SOURCE_LABELS.get(ovr_commodity_name) if ovr_commodity_name in scraped_commodities else None
        results.append(IndexValueOut(
            commodity_id=commodity_id,
            commodity_name=ovr_commodity_name,
            region=ovr_region,
            year=ovr_year,
            quarter=ovr_quarter,
            value=float(o.value),
            source="team_override",
            scraped_value=None,
            override_id=o.id,
            override_by=display_name,
            override_at=o.uploaded_at.isoformat() if o.uploaded_at else None,
            global_scraper=gs,
            global_scrape_at=None,
        ))

    # Generate placeholder rows for team sources that have no data yet.
    # Fixed sources always show their constant value. Manual/upload sources show
    # empty clickable cells so the user can enter values without a chicken-and-egg
    # problem (you can't click a cell that doesn't exist).
    covered_pairs = {(r.commodity_id, r.region) for r in results}

    from datetime import datetime as _dt
    _now = _dt.now()
    _fy = from_year if from_year is not None else _now.year - 1
    _fq = from_quarter if from_quarter is not None else 1
    _ty = to_year if to_year is not None else _now.year + 1
    _tq = to_quarter if to_quarter is not None else 4
    # Clamp to single-period when a specific year/quarter was requested
    if year is not None:
        _fy, _ty = year, year
        _fq = quarter if quarter is not None else 1
        _tq = quarter if quarter is not None else 4

    src_q = (
        db.query(TeamIndexSource, CommodityIndex.name.label("src_cname"))
        .join(CommodityIndex, CommodityIndex.id == TeamIndexSource.commodity_id)
        .filter(TeamIndexSource.team_id == team_id)
    )
    if region:
        src_q = src_q.filter(TeamIndexSource.region == region)
    if commodity_name_filter:
        src_q = src_q.filter(CommodityIndex.name == commodity_name_filter)
    if commodity_ids is not None and commodity_ids:
        src_q = src_q.filter(TeamIndexSource.commodity_id.in_(commodity_ids))

    for src, src_cname in src_q.all():
        pair = (src.commodity_id, src.region)
        if pair in covered_pairs:
            continue
        covered_pairs.add(pair)
        is_fixed = src.source_type == "fixed" and src.fixed_value is not None
        y, q = _fy, _fq
        while (y < _ty) or (y == _ty and q <= _tq):
            results.append(IndexValueOut(
                commodity_id=src.commodity_id,
                commodity_name=src_cname,
                region=src.region,
                year=y,
                quarter=q,
                value=float(src.fixed_value) if is_fixed else None,
                source="fixed" if is_fixed else "team_override",
                scraped_value=None,
            ))
            q += 1
            if q > 4:
                q = 1
                y += 1

    # Composite / calculated indexes: synthesize a computed row per period from their
    # components (live). Emitted for every requested period so the grid shows the curve.
    comp_q = db.query(CommodityIndex).filter(CommodityIndex.composite_expression.isnot(None))
    if commodity_name_filter:
        comp_q = comp_q.filter(CommodityIndex.name == commodity_name_filter)
    if commodity_ids is not None and commodity_ids:
        comp_q = comp_q.filter(CommodityIndex.id.in_(commodity_ids))
    comp_region = region or "GLOBAL"
    for ci in comp_q.all():
        if (ci.id, comp_region) in covered_pairs:
            continue
        y, q = _fy, _fq
        while (y < _ty) or (y == _ty and q <= _tq):
            val = get_single_index_value(db, team_id, ci.id, comp_region, y, q)
            results.append(IndexValueOut(
                commodity_id=ci.id,
                commodity_name=ci.name,
                region=comp_region,
                year=y,
                quarter=q,
                value=val,
                source="composite",
                scraped_value=None,
            ))
            q += 1
            if q > 4:
                q = 1
                y += 1

    return results


def compute_composite_value(
    db: Session,
    team_id: uuid.UUID,
    ci: CommodityIndex,
    region: str,
    year: int,
    quarter: int,
    _resolving: set,
) -> float | None:
    """Compute a composite/calculated index's value live from its component indexes.

    Builds a {var: value} context (index vars resolved recursively via
    get_single_index_value so team overrides on components are respected; fixed vars
    use their constant), then evaluates the stored expression with the same safe
    whitelist the advanced cost formulas use. Returns None (not computable) if any
    index component has no value for the period — never fabricates a 0."""
    from app.services.costing_engine import safe_eval_expr

    context: dict[str, float] = {}
    for var_name, var_def in (ci.composite_variables or {}).items():
        if var_def.get("type") == "index" and var_def.get("commodity_id"):
            val = get_single_index_value(
                db, team_id, var_def["commodity_id"], region, year, quarter,
                _resolving=_resolving,
            )
            if val is None:
                return None  # a required component is missing → composite not computable
            context[var_name] = float(val)
        else:
            context[var_name] = float(var_def.get("value", 0))
    try:
        return float(safe_eval_expr(ci.composite_expression, context))
    except Exception:
        return None


def get_single_index_value(
    db: Session,
    team_id: uuid.UUID,
    commodity_id: int,
    region: str,
    year: int,
    quarter: int,
    _resolving: set | None = None,
) -> float | None:
    """Get a single resolved index value (composite > fixed source > override > scraped).

    `_resolving` tracks the composite chain currently being computed to break cycles."""
    # Composite / calculated index: compute live from its components (with cycle guard).
    ci = db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first()
    if ci is not None and ci.composite_expression:
        _resolving = _resolving or set()
        if commodity_id in _resolving:
            return None  # cycle — a composite (transitively) references itself
        return compute_composite_value(
            db, team_id, ci, region, year, quarter, _resolving | {commodity_id},
        )

    # A "fixed" team source means the value is constant across all periods.
    fixed_source = db.query(TeamIndexSource).filter(
        TeamIndexSource.team_id == team_id,
        TeamIndexSource.commodity_id == commodity_id,
        TeamIndexSource.region == region,
        TeamIndexSource.source_type == "fixed",
    ).first()
    if fixed_source and fixed_source.fixed_value is not None:
        return float(fixed_source.fixed_value)

    # Check override first (exact region, then GLOBAL fallback)
    override = db.query(IndexOverride).filter(
        IndexOverride.team_id == team_id,
        IndexOverride.commodity_id == commodity_id,
        IndexOverride.region == region,
        IndexOverride.year == year,
        IndexOverride.quarter == quarter,
    ).first()

    if not override and region != "GLOBAL":
        override = db.query(IndexOverride).filter(
            IndexOverride.team_id == team_id,
            IndexOverride.commodity_id == commodity_id,
            IndexOverride.region == "GLOBAL",
            IndexOverride.year == year,
            IndexOverride.quarter == quarter,
        ).first()

    if override:
        # Null override = intentional blank (team source doesn't cover this period)
        return float(override.value) if override.value is not None else None

    # Fall back to scraped
    iv = db.query(IndexValue).filter(
        IndexValue.commodity_id == commodity_id,
        IndexValue.region == region,
        IndexValue.year == year,
        IndexValue.quarter == quarter,
    ).first()

    if iv:
        return float(iv.value)

    # Fall back to GLOBAL region if region-specific value not found
    if region != "GLOBAL":
        iv = db.query(IndexValue).filter(
            IndexValue.commodity_id == commodity_id,
            IndexValue.region == "GLOBAL",
            IndexValue.year == year,
            IndexValue.quarter == quarter,
        ).first()
        if iv:
            return float(iv.value)

    # Fall back to any region that has data for this commodity/period
    iv = db.query(IndexValue).filter(
        IndexValue.commodity_id == commodity_id,
        IndexValue.year == year,
        IndexValue.quarter == quarter,
    ).first()
    if iv:
        return float(iv.value)

    # Temporal fallback: carry forward the most recent available value.
    # This handles cases where the requested period (e.g. a future reference
    # quarter) doesn't have data yet — use the latest known value instead of
    # returning None (which would flatten all ratios to 1.0).
    from sqlalchemy import or_, and_
    iv = db.query(IndexValue).filter(
        IndexValue.commodity_id == commodity_id,
        or_(
            IndexValue.year < year,
            and_(IndexValue.year == year, IndexValue.quarter <= quarter),
        ),
    ).order_by(
        IndexValue.year.desc(),
        IndexValue.quarter.desc(),
    ).first()
    if iv:
        return float(iv.value)

    return None
