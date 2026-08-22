"""Scrum 24 — Alerts API.

Subscriptions are per-user, team-scoped (any member may subscribe: costing.view).
Alert history is team-wide. The team Slack webhook and the on-demand evaluate
run are owner/admin only (they configure delivery / send notifications)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.team import Team
from app.models.cost_model import CostModel
from app.models.index_data import CommodityIndex
from app.models.alerts import AlertSubscription, AlertEvent
from app.routers.auth import get_current_user
from app.routers.teams import require_team_role
from app.schemas.alerts import (
    SubscriptionCreate, SubscriptionUpdate, SubscriptionOut, AlertEventOut,
    SlackWebhookUpdate, SlackWebhookOut, TRIGGER_TYPES, CHANNELS,
)
from app.services.audit import log_event
from app.services.permissions import require_permission, has_permission

router = APIRouter()


def _scope_label(db: Session, sub: AlertSubscription) -> str:
    if sub.cost_model_id:
        cm = db.query(CostModel).filter(CostModel.id == sub.cost_model_id).first()
        return (cm.product.name if cm and cm.product else "product")
    if sub.commodity_id:
        ci = db.query(CommodityIndex).filter(CommodityIndex.id == sub.commodity_id).first()
        return ci.name if ci else "index"
    return "All products" if sub.trigger_type in ("gap", "buy_window") else "All indexes"


def _out(db, sub) -> SubscriptionOut:
    return SubscriptionOut(
        id=sub.id, trigger_type=sub.trigger_type, cost_model_id=sub.cost_model_id,
        commodity_id=sub.commodity_id, threshold_pct=float(sub.threshold_pct),
        channel=sub.channel, active=sub.active, created_at=sub.created_at,
        scope_label=_scope_label(db, sub),
    )


@router.get("/subscriptions", response_model=list[SubscriptionOut])
def list_subscriptions(team_id: uuid.UUID, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    require_permission(db, current_user, team_id, "costing.view")
    subs = (
        db.query(AlertSubscription)
        .filter(AlertSubscription.team_id == team_id, AlertSubscription.user_id == current_user.id)
        .order_by(AlertSubscription.created_at.desc())
        .all()
    )
    return [_out(db, s) for s in subs]


@router.post("/subscriptions", response_model=SubscriptionOut, status_code=201)
def create_subscription(team_id: uuid.UUID, data: SubscriptionCreate,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    require_permission(db, current_user, team_id, "costing.view")
    if data.trigger_type not in TRIGGER_TYPES:
        raise HTTPException(422, f"Invalid trigger_type. Allowed: {sorted(TRIGGER_TYPES)}")
    if data.channel not in CHANNELS:
        raise HTTPException(422, f"Invalid channel. Allowed: {sorted(CHANNELS)}")
    # index_move scopes an index; gap/buy_window scope a product.
    if data.trigger_type == "index_move" and data.cost_model_id:
        raise HTTPException(422, "index_move alerts scope an index (commodity_id), not a product")
    if data.trigger_type in ("gap", "buy_window") and data.commodity_id:
        raise HTTPException(422, "gap / buy_window alerts scope a product (cost_model_id), not an index")
    # Validate the scoped entity belongs to the team.
    if data.cost_model_id:
        cm = db.query(CostModel).filter(CostModel.id == data.cost_model_id,
                                        CostModel.team_id == team_id).first()
        if not cm:
            raise HTTPException(404, "Cost model not found in this team")
    if data.commodity_id and not db.query(CommodityIndex).filter(CommodityIndex.id == data.commodity_id).first():
        raise HTTPException(404, "Commodity index not found")

    sub = AlertSubscription(
        team_id=team_id, user_id=current_user.id, trigger_type=data.trigger_type,
        cost_model_id=data.cost_model_id, commodity_id=data.commodity_id,
        threshold_pct=data.threshold_pct, channel=data.channel, active=True,
    )
    db.add(sub)
    db.flush()
    out = _out(db, sub)
    log_event(db, team_id, current_user.id, "create", "alert_subscription", str(sub.id),
              new_value={"trigger": data.trigger_type, "channel": data.channel})
    db.commit()
    return out


@router.put("/subscriptions/{sub_id}", response_model=SubscriptionOut)
def update_subscription(sub_id: uuid.UUID, data: SubscriptionUpdate,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    sub = db.query(AlertSubscription).filter(AlertSubscription.id == sub_id).first()
    if not sub or sub.user_id != current_user.id:
        raise HTTPException(404, "Subscription not found")
    require_permission(db, current_user, sub.team_id, "costing.view")
    if data.channel is not None:
        if data.channel not in CHANNELS:
            raise HTTPException(422, f"Invalid channel. Allowed: {sorted(CHANNELS)}")
        sub.channel = data.channel
    if data.threshold_pct is not None:
        sub.threshold_pct = data.threshold_pct
    if data.active is not None:
        sub.active = data.active
    out = _out(db, sub)
    db.commit()
    return out


@router.delete("/subscriptions/{sub_id}")
def delete_subscription(sub_id: uuid.UUID, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    sub = db.query(AlertSubscription).filter(AlertSubscription.id == sub_id).first()
    if not sub or sub.user_id != current_user.id:
        raise HTTPException(404, "Subscription not found")
    require_permission(db, current_user, sub.team_id, "costing.view")
    log_event(db, sub.team_id, current_user.id, "delete", "alert_subscription", str(sub.id))
    db.delete(sub)
    db.commit()
    return {"status": "deleted"}


@router.get("/history", response_model=list[AlertEventOut])
def alert_history(team_id: uuid.UUID, limit: int = 100, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    require_permission(db, current_user, team_id, "costing.view")
    return (
        db.query(AlertEvent).filter(AlertEvent.team_id == team_id)
        .order_by(AlertEvent.triggered_at.desc()).limit(min(limit, 500)).all()
    )


@router.get("/slack-webhook", response_model=SlackWebhookOut)
def get_slack_webhook(team_id: uuid.UUID, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    require_permission(db, current_user, team_id, "costing.view")
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(404, "Team not found")
    is_admin = has_permission(db, current_user, team_id, "costing.edit")
    return SlackWebhookOut(
        configured=bool(team.slack_webhook_url),
        slack_webhook_url=team.slack_webhook_url if is_admin else None,
    )


@router.put("/slack-webhook", response_model=SlackWebhookOut)
def set_slack_webhook(team_id: uuid.UUID, data: SlackWebhookUpdate,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    require_team_role(db, current_user, team_id, ["owner", "admin"])
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(404, "Team not found")
    url = (data.slack_webhook_url or "").strip() or None
    if url and not url.startswith("https://"):
        raise HTTPException(422, "Slack webhook must be an https URL")
    team.slack_webhook_url = url
    log_event(db, team_id, current_user.id, "update", "team_slack_webhook", str(team_id),
              new_value={"configured": bool(url)})
    db.commit()
    return SlackWebhookOut(configured=bool(url), slack_webhook_url=url)


@router.post("/evaluate")
def evaluate_now(team_id: uuid.UUID, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """Run alert evaluation for the team on demand (owner/admin). The nightly
    Celery task does the same; this is for immediate checks/demos."""
    require_team_role(db, current_user, team_id, ["owner", "admin"])
    from app.services.alerts import evaluate_team_alerts
    created = evaluate_team_alerts(db, team_id)
    return {"alerts_created": len(created)}
