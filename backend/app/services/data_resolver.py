"""
Data resolver: implements the override hierarchy.
Priority: team override > scraped value > fallback.
"""
import uuid
from datetime import datetime
from types import SimpleNamespace
from sqlalchemy.orm import Session
from sqlalchemy import func, text, and_, or_

from app.models.index_data import CommodityIndex, IndexValue, IndexOverride, TeamIndexSource
from app.models.index_layer import IndexCard, IndexMonthlyValue
from app.models.user import User
from app.schemas.index_data import IndexValueOut
from app.services.scraper import SCRAPER_REGISTRY, SCRAPER_SOURCE_LABELS
from app.services.drop.catalog_loader import REGION_MAP


def _override_source_label(override: IndexOverride) -> str:
    """A non-null override's source_file tags where it came from (Scrum 26)
    — "provider:<name>:<series_id>" for a provider-adapter fetch, "scrape:<url>"
    for a team scrape, anything else (manual entry, upload, blank) reads as a
    plain team override. Distinguishing "provider" here is what makes a
    provider-credential value's provenance visible on the resolved row,
    without changing which value wins (IndexOverride already outranks
    scraped IndexValue in every branch below)."""
    if (override.source_file or "").startswith("provider:"):
        return "provider"
    return "team_override"


def _monthly_only_commodity_ids(db: Session) -> set[int]:
    """Drop-loaded commodities (`commodity_key IS NOT NULL`) with zero native
    quarterly `IndexValue` rows — the ones that are otherwise invisible in the
    Index Library grid, since the grid was built entirely off `IndexValue` and
    never updated when the monthly layer landed (unlike the single-value
    costing-engine lookup, which was)."""
    has_quarterly = {cid for (cid,) in db.query(IndexValue.commodity_id).distinct().all()}
    drop_ids = {
        cid for (cid,) in db.query(CommodityIndex.id)
        .filter(CommodityIndex.commodity_key.isnot(None)).all()
    }
    return drop_ids - has_quarterly


def _monthly_synthetic_quarterly_rows(
    db: Session, monthly_only_ids: set[int], region, commodity_name_filter,
    commodity_ids, year, quarter, from_year, from_quarter, to_year, to_quarter,
) -> list:
    """Quarter-mean rows derived from `IndexMonthlyValue` for the commodities
    above, shaped to duck-type the `scraped` query rows below so every
    downstream step (team overrides, placeholders) treats them identically.

    Actual-only, no carry-forward: this is a raw listing, not the costing
    engine's forward-resolution path — a quarter with no observed months
    simply doesn't produce a row, exactly like a quarter with no scraped
    `IndexValue` today produces no row either.
    """
    if not monthly_only_ids:
        return []
    q = db.query(CommodityIndex).filter(CommodityIndex.id.in_(monthly_only_ids))
    if commodity_name_filter:
        q = q.filter(CommodityIndex.name == commodity_name_filter)
    if commodity_ids is not None:
        q = q.filter(CommodityIndex.id.in_(commodity_ids))
    commodities = q.all()
    if not commodities:
        return []

    cards = {
        c.commodity_id: c.region for c in
        db.query(IndexCard).filter(IndexCard.commodity_id.in_([c.id for c in commodities])).all()
    }

    now = datetime.now()
    ty = to_year if to_year is not None else now.year + 1
    tq = to_quarter if to_quarter is not None else 4
    if year is not None:
        ty = year
        tq = quarter if quarter is not None else 4

    rows = []
    for c in commodities:
        # Region lives on the card in this layer, not the series (Scrum 74) —
        # mapped through the same table the catalog loader itself uses, so a
        # combo's region and its index's displayed region always agree.
        mapped_region = REGION_MAP.get((cards.get(c.id) or "").upper(), "GLOBAL")
        if region and mapped_region != region:
            continue

        if year is not None:
            fy, fq = year, quarter if quarter is not None else 1
        elif from_year is not None:
            fy, fq = from_year, from_quarter if from_quarter is not None else 1
        else:
            # No lower bound given — the caller wants everything this series
            # actually has, not an arbitrary recent window (the bug this
            # replaced: a hardcoded "last year" default silently dropped real
            # older history for commodities whose monthly data goes back
            # further, e.g. lab-eu's 2023 actuals never surfaced at all).
            earliest = (
                db.query(IndexMonthlyValue.year, IndexMonthlyValue.month)
                .filter(IndexMonthlyValue.commodity_id == c.id, IndexMonthlyValue.kind == "actual")
                .order_by(IndexMonthlyValue.year, IndexMonthlyValue.month)
                .first()
            )
            if earliest is None:
                continue
            fy, fq = earliest[0], (earliest[1] - 1) // 3 + 1

        y, per = fy, fq
        while (y, per) <= (ty, tq):
            value = _monthly_quarter_mean(db, c.id, y, per)
            if value is not None:
                rows.append(SimpleNamespace(
                    commodity_id=c.id, commodity_name=c.name, region=mapped_region,
                    year=y, quarter=per, value=value, scraped_at=None,
                ))
            per += 1
            if per > 4:
                per, y = 1, y + 1
    return rows


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

    # Drop-loaded commodities with no native quarterly IndexValue row at all
    # would otherwise never appear in this grid — see _monthly_only_commodity_ids.
    monthly_only_ids = _monthly_only_commodity_ids(db)
    if monthly_only_ids:
        scraped = list(scraped) + _monthly_synthetic_quarterly_rows(
            db, monthly_only_ids, region, commodity_name_filter, commodity_ids,
            year, quarter, from_year, from_quarter, to_year, to_quarter,
        )

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
                source="team_blank" if o.value is None else _override_source_label(o),
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
            source=_override_source_label(o),
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
    for ci in comp_q.all():
        # A composite pinned to a region reports under that region; an unpinned one
        # follows the requested region and falls back to GLOBAL, as before.
        comp_region = ci.composite_region or region or "GLOBAL"
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
            # WHERE an input is read from is decided per input, NOT by the label on
            # the composite.
            #
            # `composite_region` says what this composite IS (a European index); it
            # must not dictate where its inputs come from. A European product
            # legitimately depends on global feedstocks — Brent has no European
            # series — and letting the label force every unpinned input to Europe
            # silently changed which number the maths used for any commodity that
            # happens to carry both a Europe and a GLOBAL series.
            #
            # So: an explicit pin wins; otherwise a pinned composite reads the
            # neutral GLOBAL series, and only an UNPINNED composite (a polymorphic
            # formula shape) follows the region it was asked for.
            var_region = var_def.get("region") or (
                "GLOBAL" if ci.composite_region else region
            )
            val = get_single_index_value(
                db, team_id, var_def["commodity_id"], var_region, year, quarter,
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


def get_forward_index_value(
    db: Session,
    team_id: uuid.UUID,
    commodity_id: int,
    region: str,
    year: int,
    quarter: int,
) -> tuple[float | None, dict | None]:
    """Resolve a FUTURE-period value for the forward should-cost path (Scrum 70
    Part 2) only — never used for historical/current periods.

    Priority: a team's fixed source, then an explicit team override for that
    future quarter (a team's own forward data legitimately applies — same top
    two tiers as get_single_index_value_detailed), then the latest projection
    vintage (Scrum 70 Part 1) for (commodity_id, region).

    Deliberately does NOT fall through to scraped_region/scraped_global/
    scraped_any_region/scraped_temporal_carry_forward — those tiers exist so a
    historical ratio never flattens to 1.0 for lack of data, but reusing them
    here would let a flat carry-forward pose as a real forecast, exactly what
    the projection service exists to prevent.

    Composite/calculated indexes are not forward-resolved in this pass (their
    components would each need their own forward resolution) — they return
    the explicit no-forecast result below, same as any other unprojected series.

    Returns (None, None) — an explicit gap — when nothing resolves.
    """
    from app.services.index_projection import latest_projection

    fixed_source = db.query(TeamIndexSource).filter(
        TeamIndexSource.team_id == team_id,
        TeamIndexSource.commodity_id == commodity_id,
        TeamIndexSource.region == region,
        TeamIndexSource.source_type == "fixed",
    ).first()
    if fixed_source and fixed_source.fixed_value is not None:
        return float(fixed_source.fixed_value), {"method": "fixed", "status": "fixed"}

    override = db.query(IndexOverride).filter(
        IndexOverride.team_id == team_id,
        IndexOverride.commodity_id == commodity_id,
        IndexOverride.region == region,
        IndexOverride.year == year,
        IndexOverride.quarter == quarter,
    ).first()
    if override and override.value is not None:
        return float(override.value), {"method": "team_override", "status": "team_override"}

    run = latest_projection(db, commodity_id, region)
    if run is None:
        return None, None
    point = next((p for p in run.points if p.year == year and p.quarter == quarter), None)
    if point is None:
        return None, None
    return float(point.value), {
        "vintage": run.vintage_at,
        "method": run.method,
        "status": run.status,
        "ci_lo": float(point.ci_lo) if point.ci_lo is not None else None,
        "ci_hi": float(point.ci_hi) if point.ci_hi is not None else None,
    }


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
    value, _source = get_single_index_value_detailed(
        db, team_id, commodity_id, region, year, quarter, _resolving=_resolving,
    )
    return value


# ── The drop's monthly series (Wave 3, unit 12 follow-up) ────────────────────
#
# `IndexValue` is quarterly and region-keyed; the 2026-07 drop's 121 series
# landed in `index_monthly_values` instead, and this resolver could not see
# them. Measured on the live catalogue: **76 of the 98 commodities the catalog's
# cost lines reference are monthly-only**, so three quarters of the catalog was
# invisible to the costing engine.
#
# Three rules this tier follows, none of them optional:
#
# * **Actual only.** 726 of the monthly rows are `forecast`. A forecast reaching
#   a historical should-cost would be fabrication, not a fallback.
# * **Quarterly is the mean of the quarter's months** — that is how the drop's
#   own quarterly rollups were produced (verified reproducible to the last
#   decimal when the layer was built), so this reads the same way rather than
#   inventing a second convention. A partial quarter averages what it has and
#   says so through its source label, because refusing a two-month quarter would
#   report "no data" for a period that has data.
# * **No region fallback.** Region is baked into the series key in this layer
#   (`ammonia-eu` and `ammonia-in` are different series), so there is no region
#   dimension here to fall back through — the region argument does not apply.


def _monthly_quarter_mean(
    db: Session, commodity_id: int, year: int, quarter: int
) -> float | None:
    """Mean of the actual monthly observations inside one quarter."""
    months = [quarter * 3 - 2, quarter * 3 - 1, quarter * 3]
    rows = (
        db.query(IndexMonthlyValue.value)
        .filter(
            IndexMonthlyValue.commodity_id == commodity_id,
            IndexMonthlyValue.year == year,
            IndexMonthlyValue.month.in_(months),
            IndexMonthlyValue.kind == "actual",
        )
        .all()
    )
    if not rows:
        return None
    return sum(float(v) for (v,) in rows) / len(rows)


def _monthly_carry_forward(
    db: Session, commodity_id: int, year: int, quarter: int
) -> float | None:
    """The most recent actual month at or before the end of the requested quarter.

    The quarterly tier above has carried values forward since long before this
    layer existed, precisely so a future reference quarter does not flatten every
    ratio to 1.0. A monthly-only series needs the same protection, or it would
    resolve for history and silently go flat the moment a model reaches past its
    last observation.
    """
    end_month = quarter * 3
    row = (
        db.query(IndexMonthlyValue.value)
        .filter(
            IndexMonthlyValue.commodity_id == commodity_id,
            IndexMonthlyValue.kind == "actual",
            or_(
                IndexMonthlyValue.year < year,
                and_(IndexMonthlyValue.year == year,
                     IndexMonthlyValue.month <= end_month),
            ),
        )
        .order_by(IndexMonthlyValue.year.desc(), IndexMonthlyValue.month.desc())
        .first()
    )
    return float(row[0]) if row else None


def get_single_index_value_detailed(
    db: Session,
    team_id: uuid.UUID,
    commodity_id: int,
    region: str,
    year: int,
    quarter: int,
    _resolving: set | None = None,
) -> tuple[float | None, str | None]:
    """Same resolution as `get_single_index_value`, but also returns which branch of
    the priority chain produced the value — for surfacing index provenance in a
    should-cost breakdown (Scrum 17). Source labels: "composite", "fixed",
    "provider", "team_override", "scraped_region", "scraped_global",
    "scraped_any_region", "scraped_temporal_carry_forward", or None if nothing
    resolved."""
    # Composite / calculated index: compute live from its components (with cycle guard).
    ci = db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first()
    if ci is not None and ci.composite_expression:
        _resolving = _resolving or set()
        if commodity_id in _resolving:
            return None, None  # cycle — a composite (transitively) references itself
        val = compute_composite_value(
            db, team_id, ci, region, year, quarter, _resolving | {commodity_id},
        )
        return val, ("composite" if val is not None else None)

    # A "fixed" team source means the value is constant across all periods.
    fixed_source = db.query(TeamIndexSource).filter(
        TeamIndexSource.team_id == team_id,
        TeamIndexSource.commodity_id == commodity_id,
        TeamIndexSource.region == region,
        TeamIndexSource.source_type == "fixed",
    ).first()
    if fixed_source and fixed_source.fixed_value is not None:
        return float(fixed_source.fixed_value), "fixed"

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
        if override.value is not None:
            return float(override.value), _override_source_label(override)
        return None, None

    # Fall back to scraped
    iv = db.query(IndexValue).filter(
        IndexValue.commodity_id == commodity_id,
        IndexValue.region == region,
        IndexValue.year == year,
        IndexValue.quarter == quarter,
    ).first()

    if iv:
        return float(iv.value), "scraped_region"

    # Fall back to GLOBAL region if region-specific value not found
    if region != "GLOBAL":
        iv = db.query(IndexValue).filter(
            IndexValue.commodity_id == commodity_id,
            IndexValue.region == "GLOBAL",
            IndexValue.year == year,
            IndexValue.quarter == quarter,
        ).first()
        if iv:
            return float(iv.value), "scraped_global"

    # Fall back to any region that has data for this commodity/period
    iv = db.query(IndexValue).filter(
        IndexValue.commodity_id == commodity_id,
        IndexValue.year == year,
        IndexValue.quarter == quarter,
    ).first()
    if iv:
        return float(iv.value), "scraped_any_region"

    # The drop's monthly series, aggregated to the requested quarter. Placed
    # BEFORE the carry-forward tiers because a real observation for the period
    # asked about beats a stale one carried from an earlier quarter.
    #
    # No commodity currently has rows in both stores (measured: 24 quarterly, 121
    # monthly, zero overlap), so this reorders nothing that resolves today. The
    # ordering is chosen for the day that changes: if this sat after the
    # carry-forward instead, a single stray quarterly row on a drop series would
    # be carried forward forever in preference to that series' real monthly data.
    monthly = _monthly_quarter_mean(db, commodity_id, year, quarter)
    if monthly is not None:
        months_found = (
            db.query(func.count(IndexMonthlyValue.id))
            .filter(
                IndexMonthlyValue.commodity_id == commodity_id,
                IndexMonthlyValue.year == year,
                IndexMonthlyValue.month.in_([quarter * 3 - 2, quarter * 3 - 1, quarter * 3]),
                IndexMonthlyValue.kind == "actual",
            ).scalar()
        )
        # A partial quarter is still an observation, but the label says so — a
        # consumer showing provenance should not present one month as three.
        return monthly, ("monthly_actual" if months_found == 3 else "monthly_partial_quarter")

    # Temporal fallback: carry forward the most recent available value.
    # This handles cases where the requested period (e.g. a future reference
    # quarter) doesn't have data yet — use the latest known value instead of
    # returning None (which would flatten all ratios to 1.0).
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
        return float(iv.value), "scraped_temporal_carry_forward"

    # Same carry-forward, over the monthly store. Last, so it never displaces a
    # quarterly value; present at all so a monthly-only series is not left to go
    # flat past its final observation while a quarterly one is protected.
    carried = _monthly_carry_forward(db, commodity_id, year, quarter)
    if carried is not None:
        return carried, "monthly_carry_forward"

    return None, None
