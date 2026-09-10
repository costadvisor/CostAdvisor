from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler

from app.config import get_settings
from app.database import engine, Base
from app.observability import init_sentry, _SDK_AVAILABLE, SentryAsgiMiddleware
from app.rate_limit import limiter

init_sentry()
from app.routers import (
    auth, teams, products, cost_models, indexes, prices,
    volumes, costing, scenarios, suppliers, chemical_families, subfamilies,
    fx_rates, audit, portfolio, admin, ai, account, freight_lanes,
    invites, access_requests, settings as settings_router, formulas, demo, regions,
    collaboration, alerts, provider_credentials, sheets, quotes, resolution,
    contracts, radar, editorial, dimensions, index_dossier, seasonality,
    intelligence, support,
)
# Imported for its side effect: registers the before_flush listener that
# auto-registers region codes so the region FK never rejects a user write.
from app.services import regions as _region_events  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="CostAdvisor API",
    version="2.0.0",
    lifespan=lifespan,
)

settings = get_settings()

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Scrum 11 — actually wire the Sentry ASGI middleware (previously imported in
# observability.py but never applied to the app at all, even when the SDK was
# available and a DSN configured).
if _SDK_AVAILABLE and settings.sentry_dsn:
    app.add_middleware(SentryAsgiMiddleware)

# The API has no page content to serve and should never be crawled — GSC
# flagged both api.costadvisor.org and api-dev.costadvisor.org as 404s Google
# had bothered to fetch. X-Robots-Tag is the header-level equivalent of a
# noindex meta tag and applies to every response (JSON included), which
# robots.txt alone doesn't guarantee for a crawler that ignores it.
@app.middleware("http")
async def add_robots_header(request, call_next):
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.app_url,  # the app (prod: costadvisor.org; dev: app.dev.costadvisor.org)
        "http://localhost:3333",
        "https://www.costadvisor.org",  # landing page submitting access requests
        "https://costadvisor.org",
        "https://dev.costadvisor.org",   # dev landing — access requests / demos
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(teams.router, prefix="/api/teams", tags=["teams"])
app.include_router(chemical_families.router, prefix="/api/chemical-families", tags=["chemical-families"])
app.include_router(subfamilies.router, prefix="/api/subfamilies", tags=["subfamilies"])
app.include_router(regions.router, prefix="/api/regions", tags=["regions"])
app.include_router(suppliers.router, prefix="/api/suppliers", tags=["suppliers"])
app.include_router(products.router, prefix="/api/products", tags=["products"])
app.include_router(cost_models.router, prefix="/api/cost-models", tags=["cost-models"])
app.include_router(collaboration.router, prefix="/api/cost-models", tags=["collaboration"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
app.include_router(contracts.router, prefix="/api/contracts", tags=["contracts"])
app.include_router(radar.router, prefix="/api/radar", tags=["radar"])
app.include_router(editorial.router, prefix="/api/editorial", tags=["editorial"])
app.include_router(dimensions.router, prefix="/api/dimensions", tags=["dimensions"])
app.include_router(index_dossier.router, prefix="/api/dossiers", tags=["dossiers"])
app.include_router(seasonality.router, prefix="/api/seasonality",
                  tags=["seasonality"])
app.include_router(intelligence.router, prefix="/api/intelligence",
                  tags=["intelligence"])
app.include_router(support.router, prefix="/api/support", tags=["support"])
app.include_router(indexes.router, prefix="/api/indexes", tags=["indexes"])
# Platform-grain index resolution reads (Scrum 74) — deliberately its own
# surface rather than a mode of /api/indexes, which is team-scoped.
app.include_router(resolution.router, prefix="/api/resolution", tags=["resolution"])
app.include_router(provider_credentials.router, prefix="/api/indexes", tags=["provider-credentials"])
app.include_router(sheets.router, prefix="/api/sheets", tags=["sheets"])
app.include_router(quotes.router, prefix="/api/quotes", tags=["quotes"])
app.include_router(prices.router, prefix="/api/prices", tags=["prices"])
app.include_router(volumes.router, prefix="/api/volumes", tags=["volumes"])
app.include_router(fx_rates.router, prefix="/api/fx-rates", tags=["fx-rates"])
app.include_router(costing.router, prefix="/api/costing", tags=["costing"])
app.include_router(scenarios.router, prefix="/api/scenarios", tags=["scenarios"])
app.include_router(audit.router, prefix="/api/audit", tags=["audit"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(ai.router, prefix="/api/ai", tags=["ai"])
app.include_router(account.router, prefix="/api/account", tags=["account"])
app.include_router(freight_lanes.router, prefix="/api/freight-lanes", tags=["freight-lanes"])
app.include_router(invites.router, prefix="/api/invites", tags=["invites"])
app.include_router(access_requests.router, prefix="/api/access-requests", tags=["access-requests"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(formulas.router, prefix="/api/formulas", tags=["formulas"])
app.include_router(demo.router, prefix="/api/demos", tags=["demos"])


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/robots.txt")
def robots_txt():
    return Response(content="User-agent: *\nDisallow: /\n", media_type="text/plain")
