"""Intelligence reference API (design §4.2).

    GET /api/intel/lines
    GET /api/intel/lines/{ref}            ref = id, current line key or former key
    GET /api/intel/reports/{slug}
    GET /api/intel/industries
    GET /api/intel/industries/{slug}
    GET /api/intel/suppliers
    GET /api/intel/suppliers/{producer_id}

Read-only platform reference. The contract for the page builders is
`docs/api/intel_reference.md`.

**Auth, not a team permission.** Everything here is platform data with no team
filter, so the gate is `get_current_user` and there is no `team_id` parameter.

**Lines are addressed by id.** A line key (`Family|||Line`) is still accepted,
current or former, and answers with the current line, so old links keep
working. Keys contain spaces, `&` and `/`; the route uses the `path` converter
so an encoded `%2F` still reaches the handler as one parameter. Clients send
`encodeURIComponent(line_key)`.

**`?status=`** (lines): a comma list of `live`, `supply_exception`,
`supply_pending`, `not_audited`, or `all`. Absent means every listed card. It
narrows each line's `product_count`, `pids` and (on the detail) `products`;
`counts.listed` / `counts.default_view` always give the full numbers.

The work is in `services/intel_reference.py` (one in-process snapshot, keyed
by a load-version tuple, so every endpoint answers from memory).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.intel_reference import (
    IndustryDetailOut, IndustryListOut, LineDetailOut, LineListOut, ReportOut,
    SupplierDetailOut, SupplierListOut,
)
from app.services import intel_reference as svc

router = APIRouter()

STATUS_HELP = ("Comma list of live, supply_exception, supply_pending, not_audited; or 'all'. "
               "Absent = every listed card.")


def _status_or_422(call, /, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except svc.BadStatusFilter as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/lines", response_model=LineListOut)
def list_lines(
    family_id: int | None = Query(None),
    subfamily_id: int | None = Query(None),
    industry: str | None = Query(None, description="Industry slug or name"),
    fn: str | None = Query(None, description="Function name"),
    q: str | None = Query(None, description="Substring of line, family, sub-family, "
                                            "platform, product code or product name"),
    has_report: bool | None = Query(None),
    status: str | None = Query(None, description=STATUS_HELP),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _status_or_422(svc.list_lines, db, family_id=family_id, subfamily_id=subfamily_id,
                          industry=industry, fn=fn, q=q, has_report=has_report, status=status)


@router.get("/lines/{ref:path}", response_model=LineDetailOut)
def get_line(ref: str, status: str | None = Query(None, description=STATUS_HELP),
             db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    out = _status_or_422(svc.get_line, db, ref, status=status)
    if out is None:
        raise HTTPException(404, "Product line not found")
    return out


@router.get("/reports/{slug}", response_model=ReportOut)
def get_report(slug: str, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    out = svc.get_report(db, slug)
    if out is None:
        raise HTTPException(404, "Report not found")
    return out


@router.get("/industries", response_model=IndustryListOut)
def list_industries(db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    return svc.list_industries(db)


@router.get("/industries/{slug}", response_model=IndustryDetailOut)
def get_industry(slug: str, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    out = svc.get_industry(db, slug)
    if out is None:
        raise HTTPException(404, "Industry not found")
    return out


@router.get("/suppliers", response_model=SupplierListOut)
def list_suppliers(
    q: str | None = Query(None, description="Substring of the producer name or an alias"),
    family_id: int | None = Query(None),
    industry: str | None = Query(None, description="Industry slug or name"),
    min_products: int = Query(1, ge=1),
    integrated: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return svc.list_suppliers(db, q=q, family_id=family_id, industry=industry,
                              min_products=min_products, integrated=integrated,
                              limit=limit, offset=offset)


@router.get("/suppliers/{producer_id}", response_model=SupplierDetailOut)
def get_supplier(producer_id: uuid.UUID, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    out = svc.get_supplier(db, producer_id)
    if out is None:
        raise HTTPException(404, "Supplier not found")
    return out
