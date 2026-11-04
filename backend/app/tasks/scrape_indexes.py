import asyncio
from datetime import datetime, timezone

from app.tasks import celery_app
from app.database import SessionLocal, bypass_rls_var
from app.models.index_data import CommodityIndex, IndexOverride, TeamIndexSource
from app.services.scraper import SCRAPER_REGISTRY, GenericWebScraper
from app.services.fx_sync import sync_fx_rates
from app.services.provider_credentials import get_credential, decrypt_credential
from app.services.providers import get_adapter, ProviderCredentialError
from app.services.refresh_log import finish_run, record, start_run
from app.services.safe_refresh import FetchFailed, check_points, fetch_with_retry, latest_period
# Side-effect import: registers the before_flush region auto-register listener
# so scraped writes on this worker process also satisfy the region FK.
from app.services import regions as _region_events  # noqa: F401


@celery_app.task(name="app.tasks.scrape_indexes.scrape_all")
def scrape_all():
    """Run all registered scrapers, one index at a time (Jira 209).

    Each index is its own attempt: a failure is logged and the loop moves on,
    so one broken source no longer stops every scraper after it (or the FX
    sync). Transient errors are retried; fetched values are checked before
    they are written, and a rejected fetch leaves the last good values alone.
    Every attempt lands in `index_refresh_attempts`.
    """
    bypass_rls_var.set(True)  # System task — no user context
    db = SessionLocal()
    try:
        run = start_run(db, "scrape_all")
        commodities = db.query(CommodityIndex).filter(
            CommodityIndex.scrape_enabled == True  # noqa: E712
        ).all()

        results = {}
        for commodity in commodities:
            scraper_cls = SCRAPER_REGISTRY.get(commodity.name)
            if not scraper_cls:
                results[commodity.name] = "no_scraper"
                continue
            results[commodity.name] = _refresh_one(db, run, commodity, scraper_cls())

        # After scraping, sync FX rates into the fx_rates table. Its own guard:
        # it used to be skipped whenever any scraper before it raised.
        try:
            results["_fx_synced"] = sync_fx_rates(db)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            results["_fx_synced"] = f"failed: {exc}"

        finish_run(db, run)
        return results
    finally:
        db.close()


def _refresh_one(db, run, commodity, scraper) -> str:
    """Fetch, check and write one index; record the attempt. Never raises."""
    started = datetime.now(timezone.utc)
    common = dict(commodity_id=commodity.id, label=commodity.name, route="scraper",
                  started_at=started)
    try:
        points, tries = asyncio.run(fetch_with_retry(scraper.fetch))
    except FetchFailed as exc:
        db.rollback()
        record(db, run, outcome="failed", error=str(exc), tries=exc.tries, **common)
        return f"failed: {exc}"

    if not points:
        record(db, run, outcome="no_data", tries=tries, **common)
        return "no_data"

    reason = check_points(db, commodity.id, points)
    if reason:
        record(db, run, outcome="rejected", error=reason, tries=tries, **common)
        return f"rejected: {reason}"

    try:
        count = scraper.write(db, commodity, points)
    except Exception as exc:  # noqa: BLE001 — a write failure is this index's, not the run's
        db.rollback()
        record(db, run, outcome="failed", error=f"write: {type(exc).__name__}: {exc}",
               tries=tries, **common)
        return f"failed: {exc}"
    record(db, run, outcome="ok", rows=count, latest_period=latest_period(points),
           tries=tries, **common)
    return f"updated_{count}"


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
        run = start_run(db, "scrape_team_sources")
        sources = db.query(TeamIndexSource).filter(
            TeamIndexSource.source_type == "scrape_url"
        ).all()

        results = {}
        for source in sources:
            key = f"{source.team_id}:{source.commodity_id}:{source.region}"
            if not source.scrape_url:
                results[key] = "no_url"
                continue

            # Recorded by id only: the label stays free of the team's URL.
            common = dict(commodity_id=source.commodity_id, team_source_id=source.id,
                          label=f"team source {source.id}", route="team_source")
            try:
                scraper = GenericWebScraper(source.scrape_url, source.scrape_config)
                value = asyncio.run(scraper.scrape())
                _upsert_override(db, source, value)
                results[key] = f"ok:{value}"
                record(db, run, outcome="ok", rows=1, **common)
            except Exception as exc:
                # Kept in the task result too, but that expires with Redis;
                # the attempt row is what makes the error visible (Jira 209).
                db.rollback()
                results[key] = f"error:{exc}"
                record(db, run, outcome="failed", error=f"{type(exc).__name__}: {exc}", **common)

        finish_run(db, run)
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
        run = start_run(db, "fetch_provider_sources")
        sources = db.query(TeamIndexSource).filter(
            TeamIndexSource.source_type == "provider_credential"
        ).all()

        results = {}
        for source in sources:
            key = f"{source.team_id}:{source.commodity_id}:{source.region}"
            cfg = source.scrape_config or {}
            provider = (cfg.get("provider") or "").strip().lower()
            series_id = cfg.get("series_id")
            common = dict(commodity_id=source.commodity_id, team_source_id=source.id,
                          label=f"team source {source.id} ({provider or 'no provider'})",
                          route="provider")

            cred_row = get_credential(db, source.team_id, provider)
            if not cred_row:
                results[key] = "error:missing credential"
                record(db, run, outcome="failed", error="missing credential", **common)
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
                record(db, run, outcome="ok", rows=1,
                       latest_period=f"{latest.year}Q{latest.quarter}", **common)
            except ProviderCredentialError as exc:
                cred_row.status, cred_row.last_error = exc.reason, exc.detail
                db.commit()
                results[key] = f"error:{exc.reason}"
                record(db, run, outcome="failed", error=f"credential: {exc.reason}", **common)
            except Exception as exc:
                db.rollback()
                results[key] = f"error:{exc}"
                outcome = "no_data" if "No data returned" in str(exc) else "failed"
                record(db, run, outcome=outcome, error=f"{type(exc).__name__}: {exc}", **common)

        finish_run(db, run)
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
