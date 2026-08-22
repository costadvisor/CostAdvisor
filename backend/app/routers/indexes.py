import asyncio
import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.rate_limit import limiter
from app.database import get_db, current_user_id_var, bypass_rls_var
from app.models.user import User
from app.models.index_data import (
    CommodityIndex, IndexValue, IndexOverride, TeamIndexSource,
)
from app.models.cost_model import CostModel, FormulaVersion, FormulaComponent
from app.models.product import Product
from app.models.supplier import Supplier
from app.routers.auth import get_current_user
from app.services.permissions import require_permission as _require_permission
from app.schemas.index_data import (
    CommodityIndexOut, IndexValueOut,
    TeamIndexSourceCreate, TeamIndexSourceOut, ScrapeNowResult,
    CellOverrideRequest, BulkOverrideRequest,
    FilterOptionsOut, IndexImpactItem, IndexImpactResponse,
    IndexValuePublicOut, PublicQuarterPoint, ProxyLogicUpdate, CompositeUpdate,
)
from app.services.data_resolver import resolve_index_values
from app.services.file_parser import parse_index_upload
from app.services.scraper import GenericWebScraper, smart_scrape, smart_scrape_all, detect_source_type, ScrapedDataPoint
from app.services.audit import log_event

router = APIRouter()


def require_super_admin(user: User):
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin required")


def require_team_access(db: Session, user: User, team_id: uuid.UUID, perm: str = "indexes.view"):
    _require_permission(db, user, team_id, perm)


# Curated headline commodities for the public marketing landing page — the top / most
# important ones with continuous, recent data. NOT the full index list. Easy to re-curate.
PUBLIC_HEADLINE_COMMODITIES = [
    "Caustic Soda", "Chlorine", "Sulfuric Acid", "Natural Gas", "Naphtha",
]


@router.get("/public-quarterly", response_model=list[IndexValuePublicOut])
@limiter.limit("60/minute")
def get_public_quarterly_indexes(
    request: Request,
    commodities: str | None = Query(None, description="Comma-separated commodity names; omit for the curated headline set"),
    region: str | None = Query(None, description="Region filter; defaults to each commodity's most recent region"),
    limit: int = Query(12, le=40, description="Max quarters per commodity (newest)"),
    db: Session = Depends(get_db),
):
    """Public (no-auth) quarterly commodity-index series for the marketing landing page.
    Platform-level scraped data only — `IndexValue` has no tenant column, so no RLS bypass
    is needed and no team/override data is ever exposed (mirrors `/api/fx-rates/public-daily`).
    Returns one entry per commodity with an oldest-first `points` series + QoQ delta."""
    names = (
        [n.strip() for n in commodities.split(",") if n.strip()]
        if commodities else PUBLIC_HEADLINE_COMMODITIES
    )

    out: list[IndexValuePublicOut] = []
    for name in names:
        ci = db.query(CommodityIndex).filter(CommodityIndex.name == name).first()
        if not ci:
            continue
        rows = (
            db.query(IndexValue)
            .filter(IndexValue.commodity_id == ci.id)
            .order_by(IndexValue.year.desc(), IndexValue.quarter.desc())
            .all()
        )
        if not rows:
            continue
        # Pick the region: requested one, else the region of the most recent row (avoids
        # mixing regions in a single series when a commodity is quoted in several).
        target_region = region or rows[0].region
        rows = [r for r in rows if r.region == target_region][:limit]
        if not rows:
            continue
        points = [
            PublicQuarterPoint(year=r.year, quarter=r.quarter, value=float(r.value))
            for r in reversed(rows)  # oldest-first for charting
        ]
        latest = points[-1].value
        prev = points[-2].value if len(points) >= 2 else None
        qoq = ((latest - prev) / prev * 100) if prev else None
        out.append(IndexValuePublicOut(
            commodity_name=ci.name,
            category=ci.category,
            unit=ci.unit,
            currency=ci.currency,
            source_url=ci.source_url,
            region=target_region,
            points=points,
            latest=latest,
            prev=prev,
            qoq_pct=qoq,
        ))
    return out


@router.put("/{commodity_id}/proxy-logic", response_model=CommodityIndexOut)
def update_proxy_logic(
    commodity_id: int,
    body: ProxyLogicUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Super-admin edits a commodity index's structured `proxy_logic` (Scrum 67 / SCRUM-67
    in the code comments). FD-1 (SCRUM-80) executes whatever is set here to produce estimates.
    Platform-level metadata (no tenant column) — super-admin gated, audit-logged."""
    require_super_admin(current_user)
    current_user_id_var.set(str(current_user.id))
    bypass_rls_var.set(True)

    from app.constants.index_metadata import validate_proxy_logic, RETRIEVAL_STATUSES

    ci = db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first()
    if not ci:
        raise HTTPException(status_code=404, detail="Commodity index not found")

    try:
        validated = validate_proxy_logic(body.proxy_logic)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if body.retrieval_status is not None and body.retrieval_status not in RETRIEVAL_STATUSES:
        raise HTTPException(status_code=422, detail=f"retrieval_status must be one of {list(RETRIEVAL_STATUSES)}")

    old = {"proxy_logic": ci.proxy_logic, "retrieval_status": ci.retrieval_status}
    ci.proxy_logic = validated
    if body.retrieval_status is not None:
        ci.retrieval_status = body.retrieval_status

    # Build the response + capture fields BEFORE commit (transaction-local RLS GUCs reset on commit).
    out = CommodityIndexOut.model_validate(ci)
    name, new_status = ci.name, ci.retrieval_status
    db.commit()

    # Audit is best-effort: platform-level index metadata has no team, and audit_logs.team_id
    # is NOT NULL with an FK to teams (the Scrum-10 platform-audit gap). Never fail the save on it.
    try:
        log_event(
            db, uuid.UUID("00000000-0000-0000-0000-000000000000"), current_user.id,
            "update", "index_proxy_logic", name,
            previous_value=old, new_value={"proxy_logic": validated, "retrieval_status": new_status},
        )
        db.commit()
    except Exception:
        db.rollback()
    return out


@router.put("/{commodity_id}/composite", response_model=CommodityIndexOut)
def update_composite(
    commodity_id: int,
    body: CompositeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Super-admin defines/edits a composite (calculated) index — its value is computed
    live from other indexes via an advanced expression (e.g. `0.6*Graphite + 0.3*Wood`).
    Platform-level (no tenant column). A null/blank expression clears the composite."""
    require_super_admin(current_user)
    current_user_id_var.set(str(current_user.id))
    bypass_rls_var.set(True)

    from app.constants.index_metadata import validate_composite_structure

    ci = db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first()
    if not ci:
        raise HTTPException(status_code=404, detail="Commodity index not found")

    try:
        expr, variables = validate_composite_structure(body.composite_expression, body.composite_variables)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # DB-level checks (need the session): referenced commodities exist, no self-reference,
    # and no immediate cycle (a component that is itself composite must not reference this one).
    if expr is not None:
        for name, spec in (variables or {}).items():
            if spec.get("type") != "index":
                continue
            cid = spec["commodity_id"]
            if cid == commodity_id:
                raise HTTPException(status_code=422, detail=f"variable '{name}' cannot reference the composite itself")
            comp = db.query(CommodityIndex).filter(CommodityIndex.id == cid).first()
            if not comp:
                raise HTTPException(status_code=422, detail=f"variable '{name}' references unknown index id {cid}")
            # A pinned region must be a real region. Validated explicitly here rather
            # than trusting the free-text auto-register net, so a typo fails loudly
            # instead of silently pinning to a region that will never resolve.
            pinned = spec.get("region")
            if pinned:
                from app.models.region import Region
                if not db.query(Region).filter(Region.code == pinned).first():
                    raise HTTPException(
                        status_code=422,
                        detail=f"variable '{name}' pins unknown region '{pinned}'",
                    )
            # Direct cycle: a composite component that references this index back.
            if comp.composite_expression and comp.composite_variables:
                back = {v.get("commodity_id") for v in comp.composite_variables.values() if v.get("type") == "index"}
                if commodity_id in back:
                    raise HTTPException(status_code=422, detail=f"cyclic reference between '{ci.name}' and '{comp.name}'")

    # The composite's own region. Validated against the real regions table for the
    # same reason a variable's pin is: a typo would otherwise silently produce an
    # index that never resolves. Clearing it (None/"") restores region-agnostic.
    comp_region = (body.composite_region or "").strip() or None
    if comp_region:
        from app.models.region import Region
        if not db.query(Region).filter(Region.code == comp_region).first():
            raise HTTPException(status_code=422, detail=f"unknown region '{comp_region}'")

    old = {
        "composite_expression": ci.composite_expression,
        "composite_variables": ci.composite_variables,
        "composite_region": ci.composite_region,
    }
    ci.composite_expression = expr
    ci.composite_variables = variables
    # Clearing the expression clears the region with it — a non-composite index has
    # no business carrying one.
    ci.composite_region = comp_region if expr is not None else None
    out = CommodityIndexOut.model_validate(ci)
    name = ci.name
    db.commit()

    try:
        log_event(
            db, uuid.UUID("00000000-0000-0000-0000-000000000000"), current_user.id,
            "update", "index_composite", name,
            previous_value=old, new_value={"composite_expression": expr, "composite_variables": variables},
        )
        db.commit()
    except Exception:
        db.rollback()
    return out


@router.get("/", response_model=list[CommodityIndexOut])
def list_commodities(
    has_data: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(CommodityIndex)
    if has_data:
        q = q.filter(
            db.query(IndexValue.commodity_id)
            .filter(IndexValue.commodity_id == CommodityIndex.id)
            .exists()
        )
    rows = q.order_by(CommodityIndex.name).all()

    # Attach the regions each index carries values for. One grouped DISTINCT rather
    # than a per-row query, so this stays a single extra round trip regardless of
    # catalog size. The index table itself has no region column by design (Scrum 57)
    # — region lives on index_values — but pickers need it to tell apart entries
    # whose names differ only by the region they cover.
    region_map: dict[int, set[str]] = {}
    for commodity_id, region in (
        db.query(IndexValue.commodity_id, IndexValue.region).distinct().all()
    ):
        if region:
            region_map.setdefault(commodity_id, set()).add(region)

    out = []
    for row in rows:
        item = CommodityIndexOut.model_validate(row)
        item.regions = sorted(region_map.get(row.id, ()))
        out.append(item)
    return out


@router.post("/commodities", response_model=CommodityIndexOut)
def create_commodity(
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a custom commodity index (user-defined, not a built-in scraper source)."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    existing = db.query(CommodityIndex).filter(CommodityIndex.name == name).first()
    if existing:
        return existing  # idempotent — return existing if same name
    commodity = CommodityIndex(
        name=name,
        unit=body.get("unit"),
        currency=body.get("currency"),
        category=body.get("category") or "Custom",
        scrape_enabled=False,
    )
    db.add(commodity)
    db.commit()
    db.refresh(commodity)
    return commodity


@router.get("/values", response_model=list[IndexValueOut])
def get_index_values(
    team_id: uuid.UUID,
    region: str | None = Query(None),
    commodity_name: str | None = Query(None),
    year: int | None = Query(None),
    quarter: int | None = Query(None),
    product_id: uuid.UUID | None = Query(None),
    supplier_id: int | None = Query(None),
    from_year: int | None = Query(None),
    from_quarter: int | None = Query(None),
    to_year: int | None = Query(None),
    to_quarter: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_team_access(db, current_user, team_id)
    # Resolve commodity IDs for product/supplier filter
    commodity_ids = None
    if product_id or supplier_id:
        commodity_ids = _resolve_commodity_ids(db, team_id, product_id, supplier_id)

    return resolve_index_values(
        db=db,
        team_id=team_id,
        region=region,
        commodity_name=commodity_name,
        year=year,
        quarter=quarter,
        commodity_ids=commodity_ids,
        from_year=from_year,
        from_quarter=from_quarter,
        to_year=to_year,
        to_quarter=to_quarter,
    )


@router.get("/template")
def download_index_template(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a CSV template pre-populated with the team's tracked commodities and regions."""
    require_team_access(db, current_user, team_id)

    # Get distinct commodity+region combos the team has overrides or sources for
    from sqlalchemy import distinct as sa_distinct
    from app.models.index_data import TeamIndexSource
    pairs = (
        db.query(sa_distinct(IndexOverride.commodity_id), IndexOverride.region)
        .filter(IndexOverride.team_id == team_id)
        .all()
    )
    # Fall back to all commodities with data if team has no overrides yet
    if not pairs:
        pairs = (
            db.query(sa_distinct(IndexValue.commodity_id), IndexValue.region)
            .limit(20)
            .all()
        )

    # Build 4 upcoming quarters as blank template rows
    now = datetime.now(timezone.utc)
    y, q = now.year, (now.month - 1) // 3 + 1
    quarters = []
    for _ in range(4):
        quarters.append((y, q))
        q -= 1
        if q == 0:
            q = 4
            y -= 1
    quarters.reverse()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["material", "region", "period", "value"])

    commodity_cache = {}
    for commodity_id, region in pairs[:20]:  # cap at 20 to keep template readable
        if commodity_id not in commodity_cache:
            c = db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first()
            commodity_cache[commodity_id] = c.name if c else str(commodity_id)
        for yr, qt in quarters:
            writer.writerow([commodity_cache[commodity_id], region, f"Q{qt}-{yr}", ""])

    if not pairs:
        for yr, qt in quarters:
            writer.writerow(["Ammonia", "Europe", f"Q{qt}-{yr}", ""])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=index_overrides_template.csv"},
    )


@router.post("/upload")
async def upload_global_indexes(
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload global index data (super admin only). Writes to index_values table."""
    current_user_id_var.set(str(current_user.id))
    bypass_rls_var.set(True)  # super admin only — verified by require_super_admin below
    require_super_admin(current_user)

    content = await file.read()
    filename = file.filename or "upload"

    try:
        result = parse_index_upload(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    rows = result["rows"]
    parse_errors = result["errors"]

    if dry_run:
        # Validate commodity names without writing
        missing_commodities = []
        for row in rows:
            c = db.query(CommodityIndex).filter(CommodityIndex.name == row["material"]).first()
            if not c:
                missing_commodities.append(row["material"])
        if missing_commodities:
            unique_missing = list(dict.fromkeys(missing_commodities))
            parse_errors = parse_errors + [
                {"row": None, "message": f"Unknown material: {m}"} for m in unique_missing
            ]
        valid_rows = [r for r in rows if r["material"] not in missing_commodities]
        return {"rows_processed": len(valid_rows), "errors": parse_errors, "dry_run": True, "filename": filename}

    count = 0
    for row in rows:
        commodity = db.query(CommodityIndex).filter(
            CommodityIndex.name == row["material"]
        ).first()
        if not commodity:
            parse_errors.append({"row": None, "message": f"Unknown material: {row['material']} (skipped)"})
            continue

        existing = db.query(IndexValue).filter(
            IndexValue.commodity_id == commodity.id,
            IndexValue.region == row["region"],
            IndexValue.year == row["year"],
            IndexValue.quarter == row["quarter"],
        ).first()

        if existing:
            existing.value = row["value"]
            existing.source = "admin_upload"
        else:
            iv = IndexValue(
                commodity_id=commodity.id,
                region=row["region"],
                year=row["year"],
                quarter=row["quarter"],
                value=row["value"],
                source="admin_upload",
            )
            db.add(iv)
        count += 1

    log_event(db, uuid.UUID("00000000-0000-0000-0000-000000000000"), current_user.id,
              "upload", "global_indexes", filename, new_value={"rows": count})
    db.commit()
    return {"status": "uploaded", "rows_processed": count, "errors": parse_errors, "filename": filename}


@router.post("/overrides")
async def upload_index_overrides(
    team_id: uuid.UUID,
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload team-specific index overrides."""
    current_user_id_var.set(str(current_user.id))
    if current_user.is_super_admin:
        bypass_rls_var.set(True)
    require_team_access(db, current_user, team_id, "indexes.import")
    content = await file.read()
    filename = file.filename or "upload"

    try:
        result = parse_index_upload(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    rows = result["rows"]
    parse_errors = result["errors"]

    if dry_run:
        missing = []
        for row in rows:
            c = db.query(CommodityIndex).filter(CommodityIndex.name == row["material"]).first()
            if not c:
                missing.append(row["material"])
        if missing:
            for m in dict.fromkeys(missing):
                parse_errors = parse_errors + [{"row": None, "message": f"Unknown material: {m}"}]
        valid = [r for r in rows if r["material"] not in missing]
        return {"rows_processed": len(valid), "errors": parse_errors, "dry_run": True, "filename": filename}

    count = 0
    for row in rows:
        commodity = db.query(CommodityIndex).filter(
            CommodityIndex.name == row["material"]
        ).first()
        if not commodity:
            parse_errors.append({"row": None, "message": f"Unknown material: {row['material']} (skipped)"})
            continue

        existing = db.query(IndexOverride).filter(
            IndexOverride.team_id == team_id,
            IndexOverride.commodity_id == commodity.id,
            IndexOverride.region == row["region"],
            IndexOverride.year == row["year"],
            IndexOverride.quarter == row["quarter"],
        ).first()

        if existing:
            existing.value = row["value"]
            existing.uploaded_by = current_user.id
            existing.source_file = filename
        else:
            override = IndexOverride(
                team_id=team_id,
                commodity_id=commodity.id,
                region=row["region"],
                year=row["year"],
                quarter=row["quarter"],
                value=row["value"],
                uploaded_by=current_user.id,
                source_file=filename,
            )
            db.add(override)
        count += 1

    log_event(db, team_id, current_user.id, "upload", "index_overrides", filename,
              new_value={"rows": count})
    db.commit()
    return {"status": "uploaded", "rows_processed": count, "errors": parse_errors, "filename": filename}


# --- Cell-level and bulk override endpoints ---


@router.put("/overrides/cell", response_model=IndexValueOut)
def cell_override(
    body: CellOverrideRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upsert a single cell override. Returns the updated enriched IndexValueOut."""
    require_team_access(db, current_user, body.team_id, "indexes.edit")
    # Verify commodity exists
    commodity = db.query(CommodityIndex).filter(
        CommodityIndex.id == body.commodity_id
    ).first()
    if not commodity:
        raise HTTPException(status_code=404, detail="Commodity not found")

    now = datetime.now(timezone.utc)

    existing = db.query(IndexOverride).filter(
        IndexOverride.team_id == body.team_id,
        IndexOverride.commodity_id == body.commodity_id,
        IndexOverride.region == body.region,
        IndexOverride.year == body.year,
        IndexOverride.quarter == body.quarter,
    ).first()

    if existing:
        existing.value = body.value
        existing.uploaded_by = current_user.id
        existing.source_file = "inline_edit"
        existing.uploaded_at = now
        override = existing
    else:
        override = IndexOverride(
            team_id=body.team_id,
            commodity_id=body.commodity_id,
            region=body.region,
            year=body.year,
            quarter=body.quarter,
            value=body.value,
            uploaded_by=current_user.id,
            source_file="inline_edit",
            uploaded_at=now,
        )
        db.add(override)
        db.flush()

    # Get the scraped value for this cell
    iv = db.query(IndexValue).filter(
        IndexValue.commodity_id == body.commodity_id,
        IndexValue.region == body.region,
        IndexValue.year == body.year,
        IndexValue.quarter == body.quarter,
    ).first()

    log_event(db, body.team_id, current_user.id, "override", "index_cell",
              f"{commodity.name}/{body.region}/Q{body.quarter}-{body.year}",
              new_value={"value": body.value})
    # Capture before commit — post-commit attribute access triggers a refresh
    # under a transaction with no RLS GUC set, which can fail / return nothing.
    override_id = override.id
    scraped = float(iv.value) if iv else None
    display_name = current_user.display_name
    db.commit()

    return IndexValueOut(
        commodity_id=body.commodity_id,
        commodity_name=commodity.name,
        region=body.region,
        year=body.year,
        quarter=body.quarter,
        value=float(body.value),
        source="team_override",
        scraped_value=scraped,
        override_id=override_id,
        override_by=display_name,
        override_at=now.isoformat(),
    )


@router.put("/overrides/bulk")
def bulk_override(
    body: BulkOverrideRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Apply a value to multiple periods for a commodity+region."""
    require_team_access(db, current_user, body.team_id, "indexes.edit")
    commodity = db.query(CommodityIndex).filter(
        CommodityIndex.id == body.commodity_id
    ).first()
    if not commodity:
        raise HTTPException(status_code=404, detail="Commodity not found")

    now = datetime.now(timezone.utc)
    count = 0

    for period in body.periods:
        yr = period.get("year")
        qtr = period.get("quarter")
        if yr is None or qtr is None:
            continue

        existing = db.query(IndexOverride).filter(
            IndexOverride.team_id == body.team_id,
            IndexOverride.commodity_id == body.commodity_id,
            IndexOverride.region == body.region,
            IndexOverride.year == yr,
            IndexOverride.quarter == qtr,
        ).first()

        if existing:
            existing.value = body.value
            existing.uploaded_by = current_user.id
            existing.source_file = "bulk_edit"
            existing.uploaded_at = now
        else:
            db.add(IndexOverride(
                team_id=body.team_id,
                commodity_id=body.commodity_id,
                region=body.region,
                year=yr,
                quarter=qtr,
                value=body.value,
                uploaded_by=current_user.id,
                source_file="bulk_edit",
                uploaded_at=now,
            ))
        count += 1

    log_event(db, body.team_id, current_user.id, "override", "index_bulk",
              f"{commodity.name}/{body.region}",
              new_value={"value": body.value, "periods": count})
    db.commit()
    return {"status": "ok", "cells_updated": count}


@router.delete("/overrides/bulk")
def delete_overrides_bulk(
    team_id: uuid.UUID,
    commodity_id: int,
    region: str,
    year: int | None = Query(None),
    quarter: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reset overrides. With year+quarter: single cell. Without: all for commodity+region+team."""
    require_team_access(db, current_user, team_id, "indexes.edit")
    query = db.query(IndexOverride).filter(
        IndexOverride.team_id == team_id,
        IndexOverride.commodity_id == commodity_id,
        IndexOverride.region == region,
    )

    if year is not None and quarter is not None:
        query = query.filter(
            IndexOverride.year == year,
            IndexOverride.quarter == quarter,
        )

    count = query.count()
    query.delete(synchronize_session=False)

    log_event(db, team_id, current_user.id, "delete", "index_overrides",
              f"commodity={commodity_id}/region={region}",
              new_value={"deleted": count})
    db.commit()
    return {"status": "deleted", "count": count}


@router.delete("/overrides/{override_id}")
def delete_override(
    override_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    override = db.query(IndexOverride).filter(IndexOverride.id == override_id).first()
    if not override:
        raise HTTPException(status_code=404, detail="Override not found")
    require_team_access(db, current_user, override.team_id, "indexes.edit")
    log_event(db, override.team_id, current_user.id, "delete", "index_override", str(override_id),
              previous_value={
                  "commodity_id": override.commodity_id,
                  "region": override.region,
                  "year": override.year,
                  "quarter": override.quarter,
                  "value": float(override.value) if override.value is not None else None,
              })
    db.delete(override)
    db.commit()
    return {"status": "deleted"}


# --- TeamIndexSource CRUD ---


@router.get("/sources", response_model=list[TeamIndexSourceOut])
def list_team_sources(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all configured index sources for a team, enriched with commodity name and scrape status."""
    require_team_access(db, current_user, team_id)
    sources = (
        db.query(TeamIndexSource)
        .filter(TeamIndexSource.team_id == team_id)
        .order_by(TeamIndexSource.commodity_id, TeamIndexSource.region)
        .all()
    )

    results = []
    for s in sources:
        # Get commodity name
        commodity = db.query(CommodityIndex).filter(
            CommodityIndex.id == s.commodity_id
        ).first()

        # Derive last scrape status from most recent IndexOverride with scrape: source_file
        last_scrape = (
            db.query(IndexOverride)
            .filter(
                IndexOverride.team_id == s.team_id,
                IndexOverride.commodity_id == s.commodity_id,
                IndexOverride.region == s.region,
                IndexOverride.source_file.like("scrape:%"),
            )
            .order_by(IndexOverride.uploaded_at.desc())
            .first()
        )

        results.append(TeamIndexSourceOut(
            id=s.id,
            team_id=s.team_id,
            commodity_id=s.commodity_id,
            region=s.region,
            source_type=s.source_type,
            scrape_url=s.scrape_url,
            scrape_config=s.scrape_config,
            source_file=s.source_file,
            fixed_value=float(s.fixed_value) if s.fixed_value is not None else None,
            created_by=s.created_by,
            updated_at=s.updated_at,
            commodity_name=commodity.name if commodity else None,
            last_scrape_status="ok" if last_scrape else None,
            last_scrape_at=last_scrape.uploaded_at.isoformat() if last_scrape else None,
        ))

    return results


@router.post("/sources", response_model=TeamIndexSourceOut)
async def create_or_update_team_source(
    body: TeamIndexSourceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create or update a team index source configuration.

    When source_type is scrape_url, automatically triggers a scrape on save:
    clears old overrides, populates all returned periods, interpolates gaps,
    and blanks periods outside the new source's range.
    """
    # Sync dependencies (get_current_user) run in a threadpool for async routes;
    # ContextVar.set() there does not propagate to the event loop. Re-set here
    # so every db.query() in this route sees the correct RLS context.
    current_user_id_var.set(str(current_user.id))
    if current_user.is_super_admin:
        bypass_rls_var.set(True)

    require_team_access(db, current_user, body.team_id, "indexes.edit")
    if body.source_type == "scrape_url" and not body.scrape_url:
        raise HTTPException(
            status_code=422, detail="scrape_url required when source_type is scrape_url"
        )
    if body.source_type == "fixed" and body.fixed_value is None:
        raise HTTPException(
            status_code=422, detail="fixed_value required when source_type is fixed"
        )

    # Verify commodity exists
    commodity = db.query(CommodityIndex).filter(
        CommodityIndex.id == body.commodity_id
    ).first()
    if not commodity:
        raise HTTPException(status_code=404, detail="Commodity not found")

    existing = db.query(TeamIndexSource).filter(
        TeamIndexSource.team_id == body.team_id,
        TeamIndexSource.commodity_id == body.commodity_id,
        TeamIndexSource.region == body.region,
    ).first()

    if existing:
        existing.source_type = body.source_type
        existing.scrape_url = body.scrape_url
        existing.scrape_config = body.scrape_config
        existing.fixed_value = body.fixed_value if body.source_type == "fixed" else None
        existing.updated_at = datetime.now(timezone.utc)
        source = existing
    else:
        source = TeamIndexSource(
            team_id=body.team_id,
            commodity_id=body.commodity_id,
            region=body.region,
            source_type=body.source_type,
            scrape_url=body.scrape_url,
            scrape_config=body.scrape_config,
            fixed_value=body.fixed_value if body.source_type == "fixed" else None,
            created_by=current_user.id,
        )
        db.add(source)

    # Flush to assign serial id (for new sources) before building the response.
    db.flush()

    # Build response while source is still in the current transaction (all
    # attributes accessible without lazy-load, no post-commit expiry issues).
    response = TeamIndexSourceOut(
        id=source.id,
        team_id=source.team_id,
        commodity_id=source.commodity_id,
        region=source.region,
        source_type=source.source_type,
        scrape_url=source.scrape_url,
        scrape_config=source.scrape_config,
        source_file=source.source_file,
        fixed_value=float(source.fixed_value) if source.fixed_value is not None else None,
        created_by=source.created_by,
        updated_at=source.updated_at,
        commodity_name=commodity.name,
    )

    db.commit()

    # Auto-scrape after commit. RLS context is now set via current_user_id_var
    # (re-set at the top of this handler), so the after_begin listener will see
    # the correct user ID for every subsequent transaction.
    if body.source_type == "scrape_url" and source.scrape_url:
        try:
            fresh_source = db.query(TeamIndexSource).filter(
                TeamIndexSource.team_id == body.team_id,
                TeamIndexSource.commodity_id == body.commodity_id,
                TeamIndexSource.region == body.region,
            ).first()
            if fresh_source:
                await _scrape_and_replace_overrides(db, fresh_source, current_user)
        except Exception as e:
            import traceback
            traceback.print_exc()
            response.scrape_warning = str(e)

    return response


@router.delete("/sources/{source_id}")
def delete_team_source(
    source_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a team index source configuration."""
    source = db.query(TeamIndexSource).filter(TeamIndexSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    require_team_access(db, current_user, source.team_id, "indexes.edit")
    db.query(IndexOverride).filter(
        IndexOverride.team_id == source.team_id,
        IndexOverride.commodity_id == source.commodity_id,
        IndexOverride.region == source.region,
    ).delete()
    db.delete(source)
    db.commit()
    return {"status": "deleted"}


@router.get("/detect-source")
def detect_source(
    url: str = Query(...),
    current_user: User = Depends(get_current_user),
):
    """Detect the source type for a URL (e.g. INSEE IDBANK detection)."""
    source_type, idbank = detect_source_type(url)
    return {"detected_source": source_type, "idbank": idbank}


@router.post("/sources/{source_id}/scrape-now", response_model=ScrapeNowResult)
async def scrape_now(
    source_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger an immediate scrape for a team source. Uses smart dispatch for known URL patterns."""
    current_user_id_var.set(str(current_user.id))
    if current_user.is_super_admin:
        bypass_rls_var.set(True)
    source = db.query(TeamIndexSource).filter(TeamIndexSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    require_team_access(db, current_user, source.team_id, "indexes.edit")
    if source.source_type != "scrape_url":
        raise HTTPException(status_code=400, detail="Source is not a scrape_url type")
    if not source.scrape_url:
        raise HTTPException(status_code=400, detail="No scrape URL configured")

    try:
        latest_value, detected = await _scrape_and_replace_overrides(db, source, current_user)
    except Exception as exc:
        return ScrapeNowResult(source_id=source_id, status="error", error=str(exc))

    return ScrapeNowResult(source_id=source_id, status="ok", value=latest_value, detected_source=detected)


async def _scrape_and_replace_overrides(
    db: Session,
    source: TeamIndexSource,
    current_user: User,
) -> tuple[float, str]:
    """Scrape all available data, clear old overrides, insert new ones with
    linear interpolation for gaps between scraped periods.

    Returns (latest_value, detected_source).
    Raises on scrape failure or empty data.
    """
    points, detected = await smart_scrape_all(source.scrape_url, source.scrape_config)
    if not points:
        raise ValueError("No data returned from source")

    # Sort by time and interpolate gaps between scraped data points
    points.sort(key=lambda p: (p.year, p.quarter))
    filled = []
    for i, point in enumerate(points):
        filled.append(point)
        if i + 1 < len(points):
            nxt = points[i + 1]
            # Walk quarter-by-quarter between this point and the next
            y, q = point.year, point.quarter
            gaps = []
            while True:
                q += 1
                if q > 4:
                    q = 1
                    y += 1
                if (y, q) == (nxt.year, nxt.quarter):
                    break
                gaps.append((y, q))
            # Linearly interpolate across the gap
            for j, (gy, gq) in enumerate(gaps):
                frac = (j + 1) / (len(gaps) + 1)
                interp = point.value + frac * (nxt.value - point.value)
                filled.append(ScrapedDataPoint(
                    region=source.region,
                    year=gy,
                    quarter=gq,
                    value=round(interp, 4),
                ))

    # Build set of periods covered by the new source
    now = datetime.now(timezone.utc)
    filled_periods = {(p.year, p.quarter) for p in filled}

    # Find all base source periods for this commodity+region that the new
    # source does NOT cover — these need null overrides to blank them out.
    base_periods = db.query(IndexValue.year, IndexValue.quarter).filter(
        IndexValue.commodity_id == source.commodity_id,
        IndexValue.region == source.region,
    ).all()

    # Clear all existing overrides for this team/commodity/region
    db.query(IndexOverride).filter(
        IndexOverride.team_id == source.team_id,
        IndexOverride.commodity_id == source.commodity_id,
        IndexOverride.region == source.region,
    ).delete()

    # Insert overrides for scraped + interpolated periods
    latest_value = None
    for point in filled:
        db.add(IndexOverride(
            team_id=source.team_id,
            commodity_id=source.commodity_id,
            region=source.region,
            year=point.year,
            quarter=point.quarter,
            value=point.value,
            uploaded_by=current_user.id,
            source_file=f"scrape:{source.scrape_url}",
        ))
        latest_value = point.value

    # Insert null overrides to blank out base periods not in the new source
    for year, quarter in base_periods:
        if (year, quarter) not in filled_periods:
            db.add(IndexOverride(
                team_id=source.team_id,
                commodity_id=source.commodity_id,
                region=source.region,
                year=year,
                quarter=quarter,
                value=None,
                uploaded_by=current_user.id,
                source_file=f"scrape:{source.scrape_url}",
            ))

    log_event(db, source.team_id, current_user.id, "scrape", "team_index_source", str(source.id),
              new_value={
                  "scrape_url": source.scrape_url,
                  "commodity_id": source.commodity_id,
                  "region": source.region,
                  "points_written": len(filled),
                  "latest_value": latest_value,
              })
    db.commit()
    return latest_value, detected


# --- Helper: resolve commodity IDs from product/supplier ---


def _resolve_commodity_ids(
    db: Session,
    team_id: uuid.UUID,
    product_id: uuid.UUID | None = None,
    supplier_id: int | None = None,
) -> set[int]:
    """Get the set of commodity_ids used by a product's or supplier's cost models."""
    query = (
        db.query(FormulaComponent.commodity_id)
        .join(FormulaVersion, FormulaVersion.id == FormulaComponent.formula_version_id)
        .join(CostModel, CostModel.id == FormulaVersion.cost_model_id)
        .filter(
            CostModel.team_id == team_id,
            FormulaComponent.commodity_id.isnot(None),
        )
    )
    if product_id:
        query = query.filter(CostModel.product_id == product_id)
    if supplier_id:
        query = query.filter(CostModel.supplier_id == supplier_id)

    return {row[0] for row in query.distinct().all()}


# --- Filter options endpoint ---


@router.get("/filter-options", response_model=FilterOptionsOut)
def get_filter_options(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return filter dropdown options for the indexes page."""
    require_team_access(db, current_user, team_id)
    products = (
        db.query(Product.id, Product.name)
        .filter(Product.team_id == team_id)
        .order_by(Product.name)
        .all()
    )
    suppliers = (
        db.query(Supplier.id, Supplier.name)
        .filter(Supplier.team_id == team_id)
        .order_by(Supplier.name)
        .all()
    )
    regions = (
        db.query(IndexValue.region)
        .distinct()
        .order_by(IndexValue.region)
        .all()
    )
    materials = (
        db.query(CommodityIndex.name)
        .order_by(CommodityIndex.name)
        .all()
    )

    return FilterOptionsOut(
        products=[{"id": str(p.id), "name": p.name} for p in products],
        suppliers=[{"id": s.id, "name": s.name} for s in suppliers],
        regions=[r[0] for r in regions],
        materials=[m[0] for m in materials],
    )


# --- Portfolio impact endpoint ---


@router.get("/{commodity_id}/impact", response_model=IndexImpactResponse)
def get_index_impact(
    commodity_id: int,
    team_id: uuid.UUID = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the portfolio impact of an index: which products use it and how much it's changed."""
    require_team_access(db, current_user, team_id)
    from app.services.data_resolver import get_single_index_value

    commodity = db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first()
    if not commodity:
        raise HTTPException(status_code=404, detail="Commodity not found")

    # Subquery: earliest formula version per cost model (first baseline)
    # Using the earliest base period gives a meaningful index change over time,
    # rather than comparing the latest version's base period to current (which
    # is often the same quarter, yielding 0% change).
    from sqlalchemy import func
    earliest_fv = (
        db.query(
            FormulaVersion.cost_model_id,
            func.min(FormulaVersion.id).label("min_fv_id"),
        )
        .group_by(FormulaVersion.cost_model_id)
        .subquery()
    )

    # Find formula components from the earliest version of each cost model
    components = (
        db.query(
            FormulaComponent,
            FormulaVersion,
            CostModel,
            Product.name.label("product_name"),
            Supplier.name.label("supplier_name"),
        )
        .join(FormulaVersion, FormulaVersion.id == FormulaComponent.formula_version_id)
        .join(CostModel, CostModel.id == FormulaVersion.cost_model_id)
        .join(earliest_fv, earliest_fv.c.min_fv_id == FormulaVersion.id)
        .join(Product, Product.id == CostModel.product_id)
        .outerjoin(Supplier, Supplier.id == CostModel.supplier_id)
        .filter(
            FormulaComponent.commodity_id == commodity_id,
            CostModel.team_id == team_id,
        )
        .all()
    )

    impacts = []
    now = datetime.now(timezone.utc)
    current_year = now.year
    current_quarter = (now.month - 1) // 3 + 1

    for comp, fv, cm, product_name, supplier_name in components:
        # Get base period index value
        base_val = get_single_index_value(
            db, team_id, commodity_id, cm.region,
            fv.base_year, fv.base_quarter,
        )
        # Get current period index value
        current_val = get_single_index_value(
            db, team_id, commodity_id, cm.region,
            current_year, current_quarter,
        )

        change_pct = None
        impact_pct = None
        if base_val and current_val and base_val != 0:
            change_pct = round((current_val / base_val - 1) * 100, 2)
            impact_pct = round(float(comp.weight) * change_pct, 2)

        impacts.append(IndexImpactItem(
            cost_model_id=cm.id,
            product_name=product_name,
            supplier_name=supplier_name,
            region=cm.region,
            component_label=comp.label,
            weight=float(comp.weight),
            base_index_value=base_val,
            current_index_value=current_val,
            index_change_pct=change_pct,
            cost_impact_pct=impact_pct,
        ))

    return IndexImpactResponse(
        commodity_id=commodity_id,
        commodity_name=commodity.name,
        unit=commodity.unit,
        currency=commodity.currency,
        source_url=commodity.source_url,
        impacts=impacts,
    )


@router.post("/scrape-all")
async def scrape_all_indexes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """On-demand refresh of every scrape-enabled commodity index via its registered
    scraper (the same set the nightly Celery `scrape_all` runs). Super-admin only —
    platform data. Runs synchronously and returns per-run counts (mirrors the FX
    `/scrape` action). FX pairs are refreshed separately via /api/fx-rates/scrape."""
    require_super_admin(current_user)
    current_user_id_var.set(str(current_user.id))
    bypass_rls_var.set(True)

    from app.services.scraper import SCRAPER_REGISTRY
    commodities = db.query(CommodityIndex).filter(
        CommodityIndex.scrape_enabled == True,  # noqa: E712
        (CommodityIndex.category != "FX") | (CommodityIndex.category.is_(None)),
    ).all()

    scraped, updated, no_scraper = [], 0, 0
    for c in commodities:
        scraper_cls = SCRAPER_REGISTRY.get(c.name)
        if not scraper_cls:
            no_scraper += 1
            continue
        try:
            updated += await scraper_cls().run(db)
            scraped.append(c.name)
        except Exception:
            # Data ingestion, not a calc path — one bad feed must not abort the batch.
            pass
    db.commit()
    return {"scrapers_run": len(scraped), "values_updated": updated,
            "commodities_without_scraper": no_scraper}
