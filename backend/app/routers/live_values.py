"""Live-number picker API (Jira 210 S7).

    GET  /api/live/series?q=           series a token can point at (search by name or key)
    GET  /api/live/recipe-lines?code=  the lines of one platform recipe, for a weight token
    POST /api/live/preview             what a token would show right now

For whoever edits text: the same `content.edit` permission as saving a block,
on the team or at platform level (super admins pass). Nothing here writes.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.formula_template import FormulaTemplate, FormulaTemplateComponent
from app.models.index_data import CommodityIndex
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.live_values import (
    LivePreviewIn, LivePreviewOut, LiveRecipeLineOut, LiveSeriesOut,
)
from app.services.live_values import LiveText
from app.services.permissions import has_permission, has_platform_permission
from app.services.series_health import series_health_bulk

router = APIRouter()


def _require_editor(db: Session, user: User, team_id: uuid.UUID) -> None:
    if not (has_permission(db, user, team_id, "content.edit")
            or has_platform_permission(db, user, "content.edit")):
        raise HTTPException(status_code=403, detail="Permission required: content.edit")


@router.get("/series", response_model=list[LiveSeriesOut])
def search_series(team_id: uuid.UUID, q: str = Query("", max_length=100),
                  limit: int = Query(20, ge=1, le=100),
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Series with a key (the ones a token can name), best matches first."""
    _require_editor(db, current_user, team_id)
    query = db.query(CommodityIndex).filter(CommodityIndex.commodity_key.isnot(None))
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(CommodityIndex.name.ilike(like),
                                 CommodityIndex.commodity_key.ilike(like)))
    rows = query.order_by(CommodityIndex.name).limit(limit).all()
    health = {h.commodity_id: h for h in series_health_bulk(db, commodity_ids=[r.id for r in rows])}
    return [LiveSeriesOut(key=r.commodity_key, name=r.name, frequency=r.frequency,
                          state=health[r.id].state, last_actual=health[r.id].last_actual)
            for r in rows]


@router.get("/recipe-lines", response_model=list[LiveRecipeLineOut])
def recipe_lines(team_id: uuid.UUID, code: str = Query(..., max_length=32),
                 db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_editor(db, current_user, team_id)
    rows = (db.query(FormulaTemplateComponent)
            .join(FormulaTemplate, FormulaTemplate.id == FormulaTemplateComponent.template_id)
            .filter(FormulaTemplate.team_id.is_(None), FormulaTemplate.code == code.strip())
            .order_by(FormulaTemplateComponent.region, FormulaTemplateComponent.sort_order)
            .all())
    if not rows:
        raise HTTPException(status_code=404, detail=f"No platform recipe {code!r}")
    return [LiveRecipeLineOut(region=r.region, variant=r.variant or "", name=r.name,
                              weight_pct=float(r.weight_pct)) for r in rows]


@router.post("/preview", response_model=LivePreviewOut)
def preview(team_id: uuid.UUID, data: LivePreviewIn,
            db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_editor(db, current_user, team_id)
    return LiveText(db).preview(data.token, doc_as_of=data.doc_as_of)
