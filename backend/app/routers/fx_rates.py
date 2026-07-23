import io
import csv
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db, bypass_rls_var
from app.models.user import User
from app.models.fx_rate import FxRate
from app.models.custom_fx_rate import CustomFxRate
from app.models.fx_pair import FxPair
from app.routers.auth import get_current_user
from app.schemas.fx_rate import FxRateOut, FxRateUpsert, CustomFxRateOut, CustomFxRateUpsert, FxDailyRateOut
from app.schemas.fx_pair import FxPairOut, FxPairCreate, FxPairUpdate
from app.services.file_parser import parse_fx_upload
from app.services.permissions import require_permission, has_platform_permission, require_platform_permission
from app.rate_limit import limiter

router = APIRouter()


def require_super_admin(user: User):
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin required")


def require_fx_manager(db: Session, user: User):
    """FX Manager platform permission OR super admin."""
    if user.is_super_admin:
        return
    require_platform_permission(db, user, "fx_rates.edit")


# ── FX Pairs (platform-level) ─────────────────────────────────────────────────

@router.get("/pairs", response_model=list[FxPairOut])
def list_fx_pairs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(FxPair).order_by(FxPair.name).all()


@router.post("/pairs", response_model=FxPairOut)
def create_fx_pair(
    payload: FxPairCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_fx_manager(db, current_user)
    existing = db.query(FxPair).filter(
        FxPair.from_currency == payload.from_currency,
        FxPair.to_currency == payload.to_currency,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Pair already exists")
    pair = FxPair(
        from_currency=payload.from_currency.upper(),
        to_currency=payload.to_currency.upper(),
        name=payload.name,
        source_type=payload.source_type,
        scrape_url=payload.scrape_url,
        scrape_enabled=payload.scrape_enabled,
        created_by=current_user.id,
    )
    db.add(pair)
    db.flush()
    pair_id = pair.id
    db.expunge(pair)
    db.commit()
    return db.query(FxPair).filter(FxPair.id == pair_id).first()


@router.put("/pairs/{pair_id}", response_model=FxPairOut)
def update_fx_pair(
    pair_id: int,
    payload: FxPairUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_fx_manager(db, current_user)
    pair = db.query(FxPair).filter(FxPair.id == pair_id).first()
    if not pair:
        raise HTTPException(status_code=404, detail="FX pair not found")
    if payload.name is not None:
        pair.name = payload.name
    if payload.source_type is not None:
        pair.source_type = payload.source_type
    if payload.scrape_url is not None:
        pair.scrape_url = payload.scrape_url
    if payload.scrape_enabled is not None:
        pair.scrape_enabled = payload.scrape_enabled
    db.flush()
    db.expunge(pair)
    db.commit()
    return db.query(FxPair).filter(FxPair.id == pair_id).first()


@router.delete("/pairs/{pair_id}", status_code=204)
def delete_fx_pair(
    pair_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_fx_manager(db, current_user)
    pair = db.query(FxPair).filter(FxPair.id == pair_id).first()
    if not pair:
        raise HTTPException(status_code=404, detail="FX pair not found")
    db.delete(pair)
    db.commit()


@router.post("/pairs/{pair_id}/scrape-live")
async def scrape_pair_live(
    pair_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger an on-demand live rate scrape for a single FX pair."""
    require_fx_manager(db, current_user)
    pair = db.query(FxPair).filter(FxPair.id == pair_id).first()
    if not pair:
        raise HTTPException(status_code=404, detail="FX pair not found")
    if not pair.scrape_url:
        raise HTTPException(status_code=400, detail="Pair has no scrape URL configured")

    bypass_rls_var.set(True)
    try:
        if pair.source_type == "ecb":
            from app.services.scrapers.ecb import ECBLiveScraper
            live = await ECBLiveScraper(pair.name, pair.scrape_url).fetch_live()
        elif pair.source_type in ("frankfurter", "google_finance"):
            from app.services.scrapers.frankfurter import FrankfurterScraper
            live = await FrankfurterScraper(pair.scrape_url).fetch_live()
        else:
            live = None

        if live is None:
            raise HTTPException(status_code=502, detail="No live rate returned from source")

        pair.live_rate = live
        pair.live_scraped_at = datetime.now(timezone.utc)
        db.flush()
        db.expunge(pair)
        db.commit()
        refreshed = db.query(FxPair).filter(FxPair.id == pair_id).first()
        return {"pair": pair.name, "live_rate": live, "scraped_at": refreshed.live_scraped_at}
    finally:
        bypass_rls_var.set(False)


@router.post("/scrape-live")
async def scrape_all_pairs_live(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger live rate scrape for all enabled FX pairs."""
    require_fx_manager(db, current_user)
    bypass_rls_var.set(True)
    try:
        from app.services.scrapers.ecb import ECBLiveScraper
        pairs = db.query(FxPair).filter(
            FxPair.scrape_enabled == True,  # noqa: E712
            FxPair.scrape_url != None,  # noqa: E711
        ).all()
        results = {}
        for pair in pairs:
            if pair.source_type == "ecb":
                live = await ECBLiveScraper(pair.name, pair.scrape_url).fetch_live()
            elif pair.source_type in ("frankfurter", "google_finance"):
                from app.services.scrapers.frankfurter import FrankfurterScraper
                live = await FrankfurterScraper(pair.scrape_url).fetch_live()
            else:
                live = None
            if live is not None:
                pair.live_rate = live
                pair.live_scraped_at = datetime.now(timezone.utc)
                results[pair.name] = live
            else:
                results[pair.name] = None
        db.commit()
        return {"results": results}
    finally:
        bypass_rls_var.set(False)


# ── Platform default quarterly rates ─────────────────────────────────────────

@router.get("/", response_model=list[FxRateOut])
def list_fx_rates(
    from_currency: str | None = Query(None),
    to_currency: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(FxRate)
    if from_currency:
        query = query.filter(FxRate.from_currency == from_currency)
    if to_currency:
        query = query.filter(FxRate.to_currency == to_currency)
    return query.order_by(FxRate.year, FxRate.quarter).all()


@router.put("/", response_model=FxRateOut)
def upsert_fx_rate(
    payload: FxRateUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_fx_manager(db, current_user)
    existing = db.query(FxRate).filter(
        FxRate.from_currency == payload.from_currency,
        FxRate.to_currency == payload.to_currency,
        FxRate.year == payload.year,
        FxRate.quarter == payload.quarter,
    ).first()
    if existing:
        existing.rate = payload.rate
        existing.uploaded_by = current_user.id
        existing.uploaded_at = datetime.now(timezone.utc)
    else:
        existing = FxRate(
            from_currency=payload.from_currency,
            to_currency=payload.to_currency,
            year=payload.year,
            quarter=payload.quarter,
            rate=payload.rate,
            uploaded_by=current_user.id,
        )
        db.add(existing)
    db.flush()
    rate_id = existing.id
    db.expunge(existing)
    db.commit()
    return db.query(FxRate).filter(FxRate.id == rate_id).first()


@router.delete("/{rate_id}", status_code=204)
def delete_fx_rate(
    rate_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_fx_manager(db, current_user)
    rate = db.query(FxRate).filter(FxRate.id == rate_id).first()
    if not rate:
        raise HTTPException(status_code=404, detail="FX rate not found")
    db.delete(rate)
    db.commit()


@router.post("/scrape")
async def scrape_fx_rates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger an on-demand quarterly scrape for all ECB FX pairs."""
    require_fx_manager(db, current_user)
    from app.services.scraper import SCRAPER_REGISTRY
    from app.services.fx_sync import sync_fx_rates
    from app.models.index_data import CommodityIndex
    from app.services.scrapers.ecb import ECBUrlScraper

    bypass_rls_var.set(True)
    try:
        # Scrape via registered commodity scrapers (legacy path)
        fx_commodities = db.query(CommodityIndex).filter(
            CommodityIndex.category == "FX",
            CommodityIndex.scrape_enabled == True,  # noqa: E712
        ).all()
        scraped, pairs = 0, []
        for commodity in fx_commodities:
            scraper_cls = SCRAPER_REGISTRY.get(commodity.name)
            if not scraper_cls:
                continue
            count = await scraper_cls().run(db)
            scraped += count
            pairs.append(commodity.name)

        # Also scrape any fx_pairs rows with source_type=ecb that aren't in SCRAPER_REGISTRY
        db_pairs = db.query(FxPair).filter(
            FxPair.scrape_enabled == True,  # noqa: E712
            FxPair.source_type == "ecb",
            FxPair.scrape_url != None,  # noqa: E711
        ).all()
        for fp in db_pairs:
            if fp.name in SCRAPER_REGISTRY:
                continue  # already scraped above
            try:
                from app.services.scraper import BaseScraper
                url_scraper = ECBUrlScraper(fp.name, fp.scrape_url)
                count = await url_scraper.run(db)
                scraped += count
                pairs.append(fp.name)
            except Exception:
                pass

        synced = sync_fx_rates(db)

        # Backfill from Frankfurter for all Frankfurter-source pairs. One daily
        # series fetch per pair feeds BOTH the daily history table and the
        # quarterly averages (no duplicate HTTP).
        from app.services.scrapers.frankfurter import fetch_daily_series
        from app.models.fx_rate import FxRate
        from app.models.fx_daily_rate import FxDailyRate
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from datetime import datetime, timezone

        frankfurter_pairs = db.query(FxPair).filter(
            FxPair.scrape_enabled == True,  # noqa: E712
            FxPair.source_type.in_(["frankfurter", "google_finance"]),
        ).all()
        frankfurter_scraped = 0
        daily_rows = 0
        for fp in frankfurter_pairs:
            daily = await fetch_daily_series(fp.from_currency, fp.to_currency)
            if not daily:
                continue

            # Bulk-upsert the daily series (one statement per pair)
            rows = [
                {"from_currency": fp.from_currency, "to_currency": fp.to_currency, "date": d, "rate": r}
                for d, r in daily.items()
            ]
            stmt = pg_insert(FxDailyRate).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["from_currency", "to_currency", "date"],
                set_={"rate": stmt.excluded.rate},
            )
            db.execute(stmt)
            daily_rows += len(rows)

            # Quarterly averages derived from the same daily data
            buckets: dict[tuple[int, int], list[float]] = {}
            for d, r in daily.items():
                buckets.setdefault((d.year, (d.month - 1) // 3 + 1), []).append(r)
            for (year, quarter), vals in buckets.items():
                rate = sum(vals) / len(vals)
                existing = db.query(FxRate).filter(
                    FxRate.from_currency == fp.from_currency,
                    FxRate.to_currency == fp.to_currency,
                    FxRate.year == year,
                    FxRate.quarter == quarter,
                ).first()
                if existing:
                    existing.rate = rate
                    existing.uploaded_at = datetime.now(timezone.utc)
                else:
                    db.add(FxRate(
                        from_currency=fp.from_currency,
                        to_currency=fp.to_currency,
                        year=year,
                        quarter=quarter,
                        rate=rate,
                        uploaded_by=None,
                    ))
                frankfurter_scraped += 1
            pairs.append(fp.name)
        db.commit()

        return {"scraped": scraped + frankfurter_scraped, "daily_rows": daily_rows,
                "synced": synced, "pairs": pairs}
    finally:
        bypass_rls_var.set(False)


@router.get("/daily", response_model=list[FxDailyRateOut])
def get_daily_history(
    from_currency: str = Query(...),
    to_currency: str = Query(...),
    limit: int = Query(400, le=3000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Daily FX history for one pair, newest-first (for the History tab)."""
    from app.models.fx_daily_rate import FxDailyRate

    return (
        db.query(FxDailyRate)
        .filter(
            FxDailyRate.from_currency == from_currency.upper(),
            FxDailyRate.to_currency == to_currency.upper(),
        )
        .order_by(FxDailyRate.date.desc())
        .limit(limit)
        .all()
    )


@router.get("/public-daily", response_model=list[FxDailyRateOut])
@limiter.limit("60/minute")
def get_public_daily_history(
    request: Request,
    from_currency: str = Query(...),
    to_currency: str = Query(...),
    limit: int = Query(2000, le=3000),
    db: Session = Depends(get_db),
):
    """Public (no-auth) daily FX series for the marketing landing page, oldest-first.
    Platform-level ECB reference data with no tenant scope — safe to expose."""
    from app.models.fx_daily_rate import FxDailyRate

    return (
        db.query(FxDailyRate)
        .filter(
            FxDailyRate.from_currency == from_currency.upper(),
            FxDailyRate.to_currency == to_currency.upper(),
        )
        .order_by(FxDailyRate.date.asc())
        .limit(limit)
        .all()
    )


@router.post("/upload")
async def upload_fx_rates(
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_fx_manager(db, current_user)
    content = await file.read()
    filename = file.filename or "upload"

    try:
        result = parse_fx_upload(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    rows = result["rows"]
    parse_errors = result["errors"]

    if dry_run:
        return {"rows_processed": len(rows), "errors": parse_errors, "dry_run": True, "filename": filename}

    count = 0
    for row in rows:
        existing = db.query(FxRate).filter(
            FxRate.from_currency == row["from_currency"],
            FxRate.to_currency == row["to_currency"],
            FxRate.year == row["year"],
            FxRate.quarter == row["quarter"],
        ).first()
        if existing:
            existing.rate = row["rate"]
            existing.uploaded_by = current_user.id
        else:
            db.add(FxRate(
                from_currency=row["from_currency"],
                to_currency=row["to_currency"],
                year=row["year"],
                quarter=row["quarter"],
                rate=row["rate"],
                uploaded_by=current_user.id,
            ))
        count += 1
    db.commit()
    return {"status": "uploaded", "rows_processed": count, "errors": parse_errors, "filename": filename}


@router.get("/template")
def download_template(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a CSV template pre-populated with known pairs and recent quarters."""
    pairs = db.query(FxPair).order_by(FxPair.name).all()

    # Build 5 recent quarters relative to now
    now = datetime.now(timezone.utc)
    current_year = now.year
    current_q = (now.month - 1) // 3 + 1
    quarters = []
    y, q = current_year, current_q
    for _ in range(5):
        quarters.append((y, q))
        q -= 1
        if q == 0:
            q = 4
            y -= 1
    quarters.reverse()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["from_currency", "to_currency", "period", "rate"])
    for pair in pairs[:10]:  # cap at 10 pairs to keep template readable
        for yr, qt in quarters:
            writer.writerow([pair.from_currency, pair.to_currency, f"Q{qt}-{yr}", ""])
    if not pairs:
        writer.writerow(["EUR", "USD", "Q1-2026", "1.0800"])
        writer.writerow(["GBP", "EUR", "Q1-2026", "1.1700"])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fx_rates_template.csv"},
    )


# ── Custom team FX rates ──────────────────────────────────────────────────────

@router.get("/can-manage-pairs")
def can_manage_pairs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Check whether the current user can create/edit/delete FX pairs (FX Manager or super admin)."""
    ok = current_user.is_super_admin or has_platform_permission(db, current_user, "fx_rates.edit")
    return {"can_manage": ok}


@router.get("/can-edit-custom")
def can_edit_custom(
    team_id: uuid.UUID = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.permissions import has_permission
    return {"can_edit": has_permission(db, current_user, team_id, "fx_rates.edit")}


@router.get("/custom", response_model=list[CustomFxRateOut])
def list_custom_fx_rates(
    team_id: uuid.UUID = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "fx_rates.view")
    return db.query(CustomFxRate).filter(
        CustomFxRate.team_id == team_id
    ).order_by(CustomFxRate.year, CustomFxRate.quarter).all()


@router.put("/custom", response_model=CustomFxRateOut)
def upsert_custom_fx_rate(
    payload: CustomFxRateUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, payload.team_id, "fx_rates.edit")
    now = datetime.now(timezone.utc)
    rate_val = payload.rate if payload.value_type == "fixed" else None
    existing = db.query(CustomFxRate).filter(
        CustomFxRate.team_id == payload.team_id,
        CustomFxRate.from_currency == payload.from_currency,
        CustomFxRate.to_currency == payload.to_currency,
        CustomFxRate.year == payload.year,
        CustomFxRate.quarter == payload.quarter,
    ).first()
    if existing:
        existing.value_type = payload.value_type
        existing.rate = rate_val
        existing.ref_year = payload.ref_year
        existing.ref_quarter = payload.ref_quarter
        existing.updated_by = current_user.id
        existing.updated_at = now
    else:
        existing = CustomFxRate(
            team_id=payload.team_id,
            from_currency=payload.from_currency,
            to_currency=payload.to_currency,
            year=payload.year,
            quarter=payload.quarter,
            value_type=payload.value_type,
            rate=rate_val,
            ref_year=payload.ref_year,
            ref_quarter=payload.ref_quarter,
            updated_by=current_user.id,
            updated_at=now,
        )
        db.add(existing)
    db.flush()
    # Build the response from in-memory values BEFORE commit — the RLS GUCs are
    # transaction-local, so re-querying (or refreshing) after commit can return
    # None and 500 the response_model.
    result = CustomFxRateOut(
        id=existing.id, team_id=existing.team_id,
        from_currency=existing.from_currency, to_currency=existing.to_currency,
        year=existing.year, quarter=existing.quarter, value_type=existing.value_type,
        rate=float(rate_val) if rate_val is not None else None,
        ref_year=existing.ref_year, ref_quarter=existing.ref_quarter, updated_at=now,
    )
    db.commit()
    return result


@router.delete("/custom/{rate_id}", status_code=204)
def delete_custom_fx_rate(
    rate_id: uuid.UUID,
    team_id: uuid.UUID = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "fx_rates.edit")
    rate = db.query(CustomFxRate).filter(
        CustomFxRate.id == rate_id,
        CustomFxRate.team_id == team_id,
    ).first()
    if not rate:
        raise HTTPException(status_code=404, detail="Custom FX rate not found")
    db.delete(rate)
    db.commit()


@router.post("/custom/upload")
async def upload_custom_fx_rates(
    team_id: uuid.UUID = Query(...),
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "fx_rates.edit")
    content = await file.read()
    filename = file.filename or "upload"

    try:
        result = parse_fx_upload(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    rows = result["rows"]
    parse_errors = result["errors"]

    if dry_run:
        return {"rows_processed": len(rows), "errors": parse_errors, "dry_run": True, "filename": filename}

    count = 0
    for row in rows:
        existing = db.query(CustomFxRate).filter(
            CustomFxRate.team_id == team_id,
            CustomFxRate.from_currency == row["from_currency"],
            CustomFxRate.to_currency == row["to_currency"],
            CustomFxRate.year == row["year"],
            CustomFxRate.quarter == row["quarter"],
        ).first()
        if existing:
            existing.value_type = "fixed"
            existing.rate = row["rate"]
            existing.ref_year = None
            existing.ref_quarter = None
            existing.updated_by = current_user.id
            existing.updated_at = datetime.now(timezone.utc)
        else:
            db.add(CustomFxRate(
                team_id=team_id,
                from_currency=row["from_currency"],
                to_currency=row["to_currency"],
                year=row["year"],
                quarter=row["quarter"],
                value_type="fixed",
                rate=row["rate"],
                updated_by=current_user.id,
            ))
        count += 1
    db.commit()
    return {"status": "uploaded", "rows_processed": count, "errors": parse_errors, "filename": filename}


@router.delete("/custom-by-key", status_code=204)
def delete_custom_fx_rate_by_key(
    team_id: uuid.UUID = Query(...),
    from_currency: str = Query(...),
    to_currency: str = Query(...),
    year: int = Query(...),
    quarter: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_permission(db, current_user, team_id, "fx_rates.edit")
    rate = db.query(CustomFxRate).filter(
        CustomFxRate.team_id == team_id,
        CustomFxRate.from_currency == from_currency,
        CustomFxRate.to_currency == to_currency,
        CustomFxRate.year == year,
        CustomFxRate.quarter == quarter,
    ).first()
    if not rate:
        raise HTTPException(status_code=404, detail="Custom FX rate not found")
    db.delete(rate)
    db.commit()


@router.post("/custom/sync-periods")
def sync_custom_periods(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Multi-currency, multi-period sync from platform defaults into custom overrides.

    Body: {
      "team_id": "...",
      "selections": [
        {"from_currency": "EUR", "to_currency": "USD", "periods": [{"year": 2026, "quarter": 1}]}
      ]
    }
    """
    team_id = uuid.UUID(str(payload["team_id"]))
    require_permission(db, current_user, team_id, "fx_rates.edit")
    selections = payload.get("selections", [])
    count = 0
    for sel in selections:
        from_c = sel["from_currency"]
        to_c = sel["to_currency"]
        for period in sel.get("periods", []):
            y, q = period["year"], period["quarter"]
            default = db.query(FxRate).filter(
                FxRate.from_currency == from_c,
                FxRate.to_currency == to_c,
                FxRate.year == y,
                FxRate.quarter == q,
            ).first()
            if not default:
                continue
            existing = db.query(CustomFxRate).filter(
                CustomFxRate.team_id == team_id,
                CustomFxRate.from_currency == from_c,
                CustomFxRate.to_currency == to_c,
                CustomFxRate.year == y,
                CustomFxRate.quarter == q,
            ).first()
            if existing:
                existing.value_type = "fixed"
                existing.rate = default.rate
                existing.ref_year = None
                existing.ref_quarter = None
                existing.updated_by = current_user.id
                existing.updated_at = datetime.now(timezone.utc)
            else:
                db.add(CustomFxRate(
                    team_id=team_id,
                    from_currency=from_c,
                    to_currency=to_c,
                    year=y,
                    quarter=q,
                    value_type="fixed",
                    rate=default.rate,
                    updated_by=current_user.id,
                ))
            count += 1
    db.commit()
    return {"synced": count}


@router.post("/custom/copy-from-default", response_model=dict)
def copy_default_fx_rates(
    team_id: uuid.UUID = Query(...),
    year: int = Query(...),
    quarter: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Copy all platform default rates for one period into team custom overrides (legacy)."""
    require_permission(db, current_user, team_id, "fx_rates.edit")
    defaults = db.query(FxRate).filter(
        FxRate.year == year,
        FxRate.quarter == quarter,
    ).all()
    count = 0
    for d in defaults:
        existing = db.query(CustomFxRate).filter(
            CustomFxRate.team_id == team_id,
            CustomFxRate.from_currency == d.from_currency,
            CustomFxRate.to_currency == d.to_currency,
            CustomFxRate.year == d.year,
            CustomFxRate.quarter == d.quarter,
        ).first()
        if existing:
            existing.value_type = "fixed"
            existing.rate = d.rate
            existing.updated_by = current_user.id
        else:
            db.add(CustomFxRate(
                team_id=team_id,
                from_currency=d.from_currency,
                to_currency=d.to_currency,
                year=d.year,
                quarter=d.quarter,
                value_type="fixed",
                rate=d.rate,
                updated_by=current_user.id,
            ))
        count += 1
    db.commit()
    return {"copied": count, "year": year, "quarter": quarter}
