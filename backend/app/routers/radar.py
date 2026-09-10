"""Trigger radar API (Wave 3, SCRUM-79 / MON-1).

    POST /api/radar/run                run the radar for a team
    GET  /api/radar/windows            the open book, filterable by state/driver
    GET  /api/radar/windows/{id}       the full inspection payload
    POST /api/radar/windows/{id}/dismiss
    GET  /api/radar/coverage           tri-valued per-product coverage
    GET/POST/DELETE /api/radar/signals manual + platform market signals

This story does not own a UI — `MonitorArea.jsx` says in its own header comment
that the trigger radar is deliberately Wave 3, and it is left where it is. These
endpoints are the inspection surface the acceptance criteria are written against.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.cost_model import CostModel
from app.models.index_data import CommodityIndex
from app.models.radar import (
    MarketSignal, NegotiationWindow, NegotiationWindowCostModel,
    SIGNAL_TYPES, WINDOW_DRIVERS, WINDOW_STATES,
)
from app.models.supplier import Supplier
from app.models.user import User
from app.routers.auth import get_current_user
from app.routers.teams import require_team_role
from app.schemas.radar import (
    CoverageReportOut, ModelCoverageOut, RadarRunOut, SignalIn, SignalOut,
    WindowDetailOut, WindowOut, WindowProductOut,
)
from app.services.audit import log_event
from app.services.permissions import require_permission
from app.services.trigger_radar import run_radar, team_coverage

router = APIRouter()


def _product_names(db: Session, ids: list[uuid.UUID]) -> dict:
    if not ids:
        return {}
    return {
        cm.id: (cm.product.name if cm.product else None)
        for cm in db.query(CostModel).filter(CostModel.id.in_(ids)).all()
    }


def _window_out(db: Session, win: NegotiationWindow, cls=WindowOut):
    rows = (
        db.query(NegotiationWindowCostModel)
        .filter(NegotiationWindowCostModel.window_id == win.id)
        .all()
    )
    names = _product_names(db, [r.cost_model_id for r in rows])
    closes_in = None
    if win.closes_on:
        closes_in = (win.closes_on - datetime.now(timezone.utc).date()).days
    return cls(
        id=win.id, driver=win.driver, driver_key=win.driver_key,
        scope_type=win.scope_type, scope_supplier_id=win.scope_supplier_id,
        scope_contract_id=win.scope_contract_id,
        scope_cost_model_id=win.scope_cost_model_id,
        scope_commodity_id=win.scope_commodity_id,
        headline=win.headline, opens_on=win.opens_on, closes_on=win.closes_on,
        close_basis=win.close_basis, state=win.state, coverage=win.coverage,
        threshold_value=float(win.threshold_value) if win.threshold_value is not None else None,
        threshold_unit=win.threshold_unit,
        products=[
            WindowProductOut(
                cost_model_id=r.cost_model_id, product=names.get(r.cost_model_id),
                exposure_pct=float(r.exposure_pct) if r.exposure_pct is not None else None,
                via_proxy=r.via_proxy,
            )
            for r in rows
        ],
        opened_at=win.opened_at, closes_in_days=closes_in,
    )


@router.post("/run", response_model=RadarRunOut)
def run(team_id: uuid.UUID, db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)):
    """Run every feed for a team on demand (owner/admin — same gate as
    `POST /api/alerts/evaluate`, which this is the upstream half of)."""
    require_team_role(db, current_user, team_id, ["owner", "admin"])
    result = run_radar(db, team_id)
    open_count = (
        db.query(NegotiationWindow)
        .filter(NegotiationWindow.team_id == team_id,
                NegotiationWindow.state == "open")
        .count()
    )
    return RadarRunOut(**result.summary, windows_open=open_count)


@router.get("/windows", response_model=list[WindowOut])
def list_windows(team_id: uuid.UUID,
                 state: str | None = Query(None),
                 driver: str | None = Query(None),
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    require_permission(db, current_user, team_id, "costing.view")
    if state and state not in WINDOW_STATES:
        raise HTTPException(422, f"Invalid state. Allowed: {sorted(WINDOW_STATES)}")
    if driver and driver not in WINDOW_DRIVERS:
        raise HTTPException(422, f"Invalid driver. Allowed: {sorted(WINDOW_DRIVERS)}")
    q = db.query(NegotiationWindow).filter(NegotiationWindow.team_id == team_id)
    if state:
        q = q.filter(NegotiationWindow.state == state)
    if driver:
        q = q.filter(NegotiationWindow.driver == driver)
    rows = q.order_by(
        NegotiationWindow.closes_on.asc().nullslast(),
        NegotiationWindow.opened_at.desc(),
    ).all()
    return [_window_out(db, w) for w in rows]


@router.get("/windows/{window_id}", response_model=WindowDetailOut)
def get_window(window_id: uuid.UUID, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    """The full inspection payload: driver, evidence, threshold + unit, the
    resolution path with proxy state, and open/close with the close basis."""
    win = db.query(NegotiationWindow).filter(NegotiationWindow.id == window_id).first()
    if not win:
        raise HTTPException(404, "Window not found")
    require_permission(db, current_user, win.team_id, "costing.view")

    out = _window_out(db, win, cls=WindowDetailOut)
    out.evidence = win.evidence
    out.suggested_negotiation_state = (win.evidence or {}).get("suggested_negotiation_state")
    # Shown alongside the suggestion so the difference is visible: the radar
    # never writes this flag (trigger_radar.SUGGESTS_NOT_SETS).
    ids = [p.cost_model_id for p in out.products]
    if ids:
        out.current_negotiation_states = {
            str(cm.id): cm.negotiation_state
            for cm in db.query(CostModel).filter(CostModel.id.in_(ids)).all()
        }
    return out


@router.post("/windows/{window_id}/dismiss", response_model=WindowOut)
def dismiss_window(window_id: uuid.UUID, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """A person saying "not this one". A dismissed window is never reopened by a
    later radar run — the upsert leaves it alone."""
    win = db.query(NegotiationWindow).filter(NegotiationWindow.id == window_id).first()
    if not win:
        raise HTTPException(404, "Window not found")
    require_permission(db, current_user, win.team_id, "costing.edit")
    win.state = "dismissed"
    win.closed_at = datetime.now(timezone.utc)
    log_event(db, win.team_id, current_user.id, "dismiss", "negotiation_window",
              str(win.id), new_value={"driver": win.driver})
    db.flush()
    out = _window_out(db, win)
    db.commit()
    return out


@router.get("/coverage", response_model=CoverageReportOut)
def coverage(team_id: uuid.UUID, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)):
    """Tri-valued coverage per product, reported whether or not a window opened.

    This is the endpoint that stops "no signal" being served for a product
    whose biggest cost line has never had a price.
    """
    require_permission(db, current_user, team_id, "costing.view")
    report = team_coverage(db, team_id)
    counts: dict[str, int] = {}
    models = []
    for c in report:
        counts[c.coverage] = counts.get(c.coverage, 0) + 1
        index_lines = [l for l in c.lines if l.reason != "fixed line"]
        models.append(ModelCoverageOut(
            cost_model_id=c.cost_model_id, product=c.product, coverage=c.coverage,
            unresolved_type_codes=sorted(set(c.unresolved_codes)),
            fallback_reason=c.fallback_reason,
            resolved_lines=sum(1 for l in index_lines if l.resolved),
            total_index_lines=len(index_lines),
        ))
    return CoverageReportOut(models=models, counts=counts)


# ── Market signals ───────────────────────────────────────────────────────────
#
# The supplier-announcement / disruption feed has no live producer, so the
# manual path is what makes the radar usable on day one: an analyst who hears
# about a force majeure can put it on the radar without a deploy. `origin`
# distinguishes that from a future imported-editorial or connector feed.
#
# Gated on `indexes.edit` — a market signal is market reference data, the same
# family as an index override, and that key already exists in every plan and
# role tier. Platform-scoped signals (visible to every team) are super-admin.

@router.get("/signals", response_model=list[SignalOut])
def list_signals(team_id: uuid.UUID, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    require_permission(db, current_user, team_id, "indexes.view")
    rows = (
        db.query(MarketSignal)
        .filter((MarketSignal.team_id == team_id) | (MarketSignal.team_id.is_(None)))
        .order_by(MarketSignal.as_of_date.desc())
        .all()
    )
    return rows


@router.post("/signals", response_model=SignalOut, status_code=201)
def create_signal(team_id: uuid.UUID, data: SignalIn, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    if data.platform:
        if not current_user.is_super_admin:
            raise HTTPException(403, "Platform-wide signals are super-admin only")
    else:
        require_permission(db, current_user, team_id, "indexes.edit")

    if data.signal_type not in SIGNAL_TYPES:
        raise HTTPException(422, f"Invalid signal_type. Allowed: {sorted(SIGNAL_TYPES)}")
    if data.expires_at and data.expires_at < data.as_of_date:
        raise HTTPException(422, "expires_at cannot precede as_of_date")
    if data.supplier_id and not db.query(Supplier).filter(
            Supplier.id == data.supplier_id, Supplier.team_id == team_id).first():
        raise HTTPException(404, "Supplier not found in this team")
    if data.commodity_id and not db.query(CommodityIndex).filter(
            CommodityIndex.id == data.commodity_id).first():
        raise HTTPException(404, "Commodity index not found")

    sig = MarketSignal(
        team_id=None if data.platform else team_id,
        # Entered by a person, so the date is authored, not inferred — an
        # imported editorial signal is the case that sets `as_of_inferred`.
        origin="manual", as_of_inferred=False,
        signal_type=data.signal_type, headline=data.headline, body=data.body,
        supplier_id=data.supplier_id, commodity_id=data.commodity_id,
        region=data.region, as_of_date=data.as_of_date, expires_at=data.expires_at,
        source_url=data.source_url, created_by=current_user.id,
    )
    db.add(sig)
    db.flush()
    out = SignalOut.model_validate(sig)
    log_event(db, team_id, current_user.id, "create", "market_signal", str(sig.id),
              new_value={"signal_type": sig.signal_type, "platform": data.platform})
    db.commit()
    return out


@router.delete("/signals/{signal_id}")
def delete_signal(signal_id: uuid.UUID, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    sig = db.query(MarketSignal).filter(MarketSignal.id == signal_id).first()
    if not sig:
        raise HTTPException(404, "Signal not found")
    if sig.team_id is None:
        if not current_user.is_super_admin:
            raise HTTPException(403, "Platform-wide signals are super-admin only")
    else:
        require_permission(db, current_user, sig.team_id, "indexes.edit")
    if sig.team_id:
        log_event(db, sig.team_id, current_user.id, "delete", "market_signal", str(sig.id))
    db.delete(sig)
    db.commit()
    return {"status": "deleted"}
