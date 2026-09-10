import asyncio
from datetime import datetime, timezone

from app.tasks import celery_app
from app.database import SessionLocal, bypass_rls_var
from app.models.index_data import CommodityIndex, IndexOverride, TeamIndexSource
from app.services.scraper import SCRAPER_REGISTRY, GenericWebScraper
from app.services.fx_sync import sync_fx_rates
from app.services.provider_credentials import get_credential, decrypt_credential
from app.services.providers import get_adapter, ProviderCredentialError
# Side-effect import: registers the before_flush region auto-register listener
# so scraped writes on this worker process also satisfy the region FK.
from app.services import regions as _region_events  # noqa: F401


@celery_app.task(name="app.tasks.scrape_indexes.scrape_all")
def scrape_all():
    """Run all registered scrapers."""
    bypass_rls_var.set(True)  # System task — no user context
    db = SessionLocal()
    try:
        commodities = db.query(CommodityIndex).filter(
            CommodityIndex.scrape_enabled == True  # noqa: E712
        ).all()

        results = {}
        for commodity in commodities:
            scraper_cls = SCRAPER_REGISTRY.get(commodity.name)
            if not scraper_cls:
                results[commodity.name] = "no_scraper"
                continue

            scraper = scraper_cls()
            count = asyncio.run(scraper.run(db))
            results[commodity.name] = f"updated_{count}"

        # After scraping, sync FX rates into the fx_rates table
        fx_count = sync_fx_rates(db)
        results["_fx_synced"] = fx_count

        return results
    finally:
        db.close()


@celery_app.task(name="app.tasks.scrape_indexes.scrape_one")
def scrape_one(commodity_name: str):
    """Scrape a single commodity."""
    scraper_cls = SCRAPER_REGISTRY.get(commodity_name)
    if not scraper_cls:
        return {"error": f"No scraper for {commodity_name}"}

    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        scraper = scraper_cls()
        count = asyncio.run(scraper.run(db))
        return {"commodity": commodity_name, "updated": count}
    finally:
        db.close()


@celery_app.task(name="app.tasks.scrape_indexes.scrape_team_sources")
def scrape_team_sources():
    """Scrape all team-configured URL sources and upsert into IndexOverride."""
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        sources = db.query(TeamIndexSource).filter(
            TeamIndexSource.source_type == "scrape_url"
        ).all()

        results = {}
        for source in sources:
            key = f"{source.team_id}:{source.commodity_id}:{source.region}"
            if not source.scrape_url:
                results[key] = "no_url"
                continue

            try:
                scraper = GenericWebScraper(source.scrape_url, source.scrape_config)
                value = asyncio.run(scraper.scrape())
                _upsert_override(db, source, value)
                results[key] = f"ok:{value}"
            except Exception as exc:
                results[key] = f"error:{exc}"

        return results
    finally:
        db.close()


@celery_app.task(name="app.tasks.scrape_indexes.fetch_provider_sources")
def fetch_provider_sources():
    """Fetch all team-configured provider-credential sources (Scrum 26) and
    upsert the latest value into IndexOverride — same current-quarter-only
    shape as scrape_team_sources above (full multi-period history is the
    on-demand /scrape-now path's job). Kept separate from scrape_team_sources:
    a different failure domain (vendor auth) and it updates
    TeamProviderCredential.status, which a generic URL scrape shouldn't know
    about."""
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        sources = db.query(TeamIndexSource).filter(
            TeamIndexSource.source_type == "provider_credential"
        ).all()

        results = {}
        for source in sources:
            key = f"{source.team_id}:{source.commodity_id}:{source.region}"
            cfg = source.scrape_config or {}
            provider = (cfg.get("provider") or "").strip().lower()
            series_id = cfg.get("series_id")

            cred_row = get_credential(db, source.team_id, provider)
            if not cred_row:
                results[key] = "error:missing credential"
                continue

            try:
                adapter = get_adapter(provider)
                points = asyncio.run(
                    adapter.fetch_series(decrypt_credential(cred_row.credential_encrypted), series_id, source.region)
                )
                if not points:
                    raise ValueError("No data returned from provider")
                latest = max(points, key=lambda p: (p.year, p.quarter))
                cred_row.status, cred_row.last_error = "ok", None
                cred_row.last_verified_at = datetime.now(timezone.utc)
                _upsert_provider_override(db, source, latest.value, provider, series_id)
                results[key] = f"ok:{latest.value}"
            except ProviderCredentialError as exc:
                cred_row.status, cred_row.last_error = exc.reason, exc.detail
                db.commit()
                results[key] = f"error:{exc.reason}"
            except Exception as exc:
                results[key] = f"error:{exc}"

        return results
    finally:
        db.close()


@celery_app.task(name="app.tasks.scrape_indexes.scrape_fx_live")
def scrape_fx_live():
    """Fetch today's live rate for all enabled FX pairs, store in fx_pairs.live_rate,
    and append today's row to the fx_daily_rate history series."""
    from datetime import date
    from app.models.fx_pair import FxPair
    from app.models.fx_daily_rate import FxDailyRate
    from app.services.scrapers.ecb import ECBLiveScraper
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        pairs = db.query(FxPair).filter(
            FxPair.scrape_enabled == True,  # noqa: E712
            FxPair.scrape_url != None,  # noqa: E711
        ).all()

        today = date.today()
        results = {}
        for pair in pairs:
            try:
                if pair.source_type == "ecb":
                    live = asyncio.run(ECBLiveScraper(pair.name, pair.scrape_url).fetch_live())
                elif pair.source_type in ("frankfurter", "google_finance"):
                    from app.services.scrapers.frankfurter import FrankfurterScraper
                    live = asyncio.run(FrankfurterScraper(pair.scrape_url).fetch_live())
                else:
                    live = None

                if live is not None:
                    pair.live_rate = live
                    pair.live_scraped_at = datetime.now(timezone.utc)
                    # Append to the daily history series (idempotent per day)
                    stmt = pg_insert(FxDailyRate).values(
                        from_currency=pair.from_currency, to_currency=pair.to_currency,
                        date=today, rate=live,
                    ).on_conflict_do_update(
                        index_elements=["from_currency", "to_currency", "date"],
                        set_={"rate": live},
                    )
                    db.execute(stmt)
                    results[pair.name] = f"ok:{live}"
                else:
                    results[pair.name] = "no_data"
            except Exception as exc:
                results[pair.name] = f"error:{exc}"

        db.commit()
        return results
    finally:
        db.close()


def _upsert_override(db, source: TeamIndexSource, value: float):
    """Write a scraped value into IndexOverride for the current quarter."""
    now = datetime.now(timezone.utc)
    year = now.year
    quarter = (now.month - 1) // 3 + 1

    existing = db.query(IndexOverride).filter(
        IndexOverride.team_id == source.team_id,
        IndexOverride.commodity_id == source.commodity_id,
        IndexOverride.region == source.region,
        IndexOverride.year == year,
        IndexOverride.quarter == quarter,
    ).first()

    if existing:
        existing.value = value
        existing.uploaded_by = source.created_by
        existing.source_file = f"scrape:{source.scrape_url}"
        existing.uploaded_at = now
    else:
        override = IndexOverride(
            team_id=source.team_id,
            commodity_id=source.commodity_id,
            region=source.region,
            year=year,
            quarter=quarter,
            value=value,
            uploaded_by=source.created_by,
            source_file=f"scrape:{source.scrape_url}",
        )
        db.add(override)

    db.commit()


def _upsert_provider_override(db, source: TeamIndexSource, value: float, provider: str, series_id: str):
    """Write a provider-fetched value into IndexOverride for the current
    quarter — same shape as _upsert_override above, tagged so
    data_resolver.py's provenance labelling reports "provider" for this row."""
    now = datetime.now(timezone.utc)
    year, quarter = now.year, (now.month - 1) // 3 + 1
    source_tag = f"provider:{provider}:{series_id}"

    existing = db.query(IndexOverride).filter(
        IndexOverride.team_id == source.team_id,
        IndexOverride.commodity_id == source.commodity_id,
        IndexOverride.region == source.region,
        IndexOverride.year == year,
        IndexOverride.quarter == quarter,
    ).first()

    if existing:
        existing.value = value
        existing.uploaded_by = source.created_by
        existing.source_file = source_tag
        existing.uploaded_at = now
    else:
        db.add(IndexOverride(
            team_id=source.team_id,
            commodity_id=source.commodity_id,
            region=source.region,
            year=year,
            quarter=quarter,
            value=value,
            uploaded_by=source.created_by,
            source_file=source_tag,
        ))

    db.commit()
