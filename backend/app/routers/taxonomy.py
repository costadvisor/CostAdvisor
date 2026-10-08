"""Supply taxonomy API (design §4.2).

    GET /api/taxonomy             family › sub-family › product line, with card counts
    GET /api/taxonomy/families    the families (filters, dropdowns)
    GET /api/taxonomy/lines       the product-line picker: ?q=&family_id=&subfamily_id=&limit=

Platform reference: the gate is `get_current_user`, with no team parameter.
The taxonomy replaces the team forks of families and sub-families
(`/api/chemical-families`, `/api/subfamilies`, removed). Contract:
`docs/api/taxonomy.md`. The work is in `services/taxonomy.py`.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.taxonomy import FamilyListOut, LinePickerOut, TaxonomyOut
from app.services import taxonomy as svc

router = APIRouter()


@router.get("", response_model=TaxonomyOut)
def get_taxonomy(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return svc.get_tree(db)


@router.get("/families", response_model=FamilyListOut)
def list_families(db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    return svc.list_families(db)


@router.get("/lines", response_model=LinePickerOut)
def search_lines(
    q: str | None = Query(None, description="Substring of the line, family, sub-family, "
                                            "platform or a former line name"),
    family_id: int | None = Query(None),
    subfamily_id: int | None = Query(None),
    limit: int | None = Query(None, ge=1, le=svc.PICKER_MAX),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return svc.search_lines(db, q=q, family_id=family_id, subfamily_id=subfamily_id, limit=limit)
