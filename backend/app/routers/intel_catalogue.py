"""Intelligence catalogue API (design §4.2).

    GET /api/intel/facets
    GET /api/intel/products
    GET /api/intel/products/{pid}
    GET /api/intel/products/{pid}/market

Read-only platform reference. Shapes: docs/api/intel_catalogue.md.

**Permission.** Any authenticated user: the content is platform reference data
with no team dimension, so there is no `team_id` and no team filter. Nothing
here writes.

**The grid gets every card.** `GET /products` with no `limit` returns every
card that matches the filters (`total == len(items)`). The page sends no
limit; `limit` / `offset` exist for scripts and are capped at 5,000.

**Status filter.** `status` is a comma list of `live`, `supply_exception`,
`supply_pending`, `not_audited`, or `all`. Absent means every listed card.
The page sends its default (`live,supply_exception`) to both `/facets` and
`/products`, so the sidebar counts and the grid agree.

**Speed.** The catalogue is computed once per data load and held in process
(`services/intel_catalogue.get_catalogue`, warmed at start by
`services/intel_warm.warm`); a request pays one cheap version query plus a
filter. The detail and market pages reuse the same snapshot for recipes and
series and read their editorial blocks and makers per request.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.intel_catalogue import FacetsOut, MarketOut, ProductDetailOut, ProductListOut
from app.services.intel_catalogue import (
    NotFound, facets, filter_items, get_catalogue, normalize_region, parse_statuses,
    product_detail, product_market,
)

router = APIRouter()

TRENDS = ("up", "flat", "down")
MAX_LIMIT = 5000
STATUS_HELP = ("Comma list of live, supply_exception, supply_pending, not_audited; "
               "or all. Absent = every listed card.")


def _statuses(value: str | None):
    try:
        return parse_statuses(value)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/facets", response_model=FacetsOut)
def get_facets(
    status: str | None = Query(None, description=STATUS_HELP),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sidebar facets and header counts. Facet counts cover the cards in
    `status`; `statuses`, `counts.listed` and `counts.default_view` always
    cover every listed card."""
    return facets(get_catalogue(db), _statuses(status))


@router.get("/products", response_model=ProductListOut)
def list_products(
    status: str | None = Query(None, description=STATUS_HELP),
    family_id: int | None = Query(None, description="chemical_families.id"),
    subfamily_id: int | None = Query(None, description="subfamilies.id"),
    line_id: int | None = Query(None, description="product_lines.id"),
    industry: str | None = Query(None, description="Industry name (demand tree), exact"),
    fn: str | None = Query(None, description="Function (demand tree), exact"),
    supplier: str | None = Query(None, description="Canonical producer name, exact; counting makers only"),
    q: str | None = Query(None, description="Substring of pid, name, full name, line, sub-family, family or CAS"),
    has_report: bool | None = Query(None, description="true = the card's product line has a market report"),
    region: str | None = Query(None, description="Drop region code (EU, NA, CN, IN, APAC, MEA, LA, GL)"),
    trend: str | None = Query(None, description="up | flat | down (vs Jan 2023, ±2 points)"),
    limit: int | None = Query(None, ge=1, le=MAX_LIMIT,
                              description="Absent = every matching card (the grid sends none)"),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The listed cards: family name, then status (verified first), then name."""
    if trend and trend not in TRENDS:
        raise HTTPException(422, f"Invalid trend. Allowed: {list(TRENDS)}")
    statuses = _statuses(status)
    cat = get_catalogue(db)
    items = filter_items(cat, statuses=statuses, family_id=family_id, subfamily_id=subfamily_id,
                         line_id=line_id, industry=industry, fn=fn, supplier=supplier, q=q,
                         has_report=has_report, region=normalize_region(region), trend=trend)
    page = items[offset:] if limit is None else items[offset:offset + limit]
    return {"total": len(items), "limit": limit, "offset": offset, "items": page}


@router.get("/products/{pid}", response_model=ProductDetailOut)
def get_product(pid: str, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """Header + Product Intelligence tab. Answers for every catalogue card,
    listed or not. A pointer or duplicate PID answers with its target and
    `redirected_from`."""
    try:
        return product_detail(db, get_catalogue(db), pid)
    except NotFound as exc:
        raise HTTPException(404, str(exc))


@router.get("/products/{pid}/market", response_model=MarketOut)
def get_product_market(
    pid: str,
    region: str | None = Query(None, description="Drop region code; default EU, else the first priced region"),
    variant: str | None = Query(None, description="Recipe variant within the region (e.g. treated)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Market & Costs tab: monthly should-cost index for every region, the
    forecast (dashed, no cone), components, build-up, dynamics, cycle,
    seasonality, volatility, trust and warnings. Redirects like the detail.
    404 on an unknown product, or a region / variant it is not priced in."""
    try:
        return product_market(db, get_catalogue(db), pid, region=normalize_region(region),
                              variant=variant or None)
    except NotFound as exc:
        raise HTTPException(404, str(exc))
