"""Scrum 24 — alert evaluation + delivery.

`evaluate_team_alerts` walks a team's active subscriptions, computes each
trigger against real data (index moves, should-cost-vs-actual gaps, buy-window
signals — the same engine outputs the dashboards use), dedups per target/quarter,
writes an `AlertEvent` (the in-app history), and delivers by email or Slack.
Deliberately reuses existing computation so alerts never diverge from the app.
"""
import uuid

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.alerts import AlertSubscription, AlertEvent
from app.models.cost_model import CostModel
from app.models.index_data import CommodityIndex, IndexValue
from app.models.price_data import ActualPrice
from app.models.team import Team
from app.models.user import User

settings = get_settings()


def _latest_two_levels(db: Session, commodity_id: int):
    """The two most recent quarters' index level (avg across regions) for a
    commodity, newest first: [(year, quarter, level), ...]."""
    rows = (
        db.query(IndexValue.year, IndexValue.quarter, func.avg(IndexValue.value))
        .filter(IndexValue.commodity_id == commodity_id)
        .group_by(IndexValue.year, IndexValue.quarter)
        .order_by(IndexValue.year.desc(), IndexValue.quarter.desc())
        .limit(2)
        .all()
    )
    return [(int(y), int(q), float(v)) for y, q, v in rows]


def _index_move_trigger(db, commodity_id, threshold):
    levels = _latest_two_levels(db, commodity_id)
    if len(levels) < 2 or not levels[1][2]:
        return None
    (y, q, cur), (_, _, prev) = levels[0], levels[1]
    move = (cur - prev) / prev * 100
    if abs(move) < float(threshold):
        return None
    ci = db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first()
    name = ci.name if ci else f"index {commodity_id}"
    direction = "up" if move > 0 else "down"
    return {
        "dedup": f"index_move:{commodity_id}:{y}Q{q}:{direction}",
        "message": f"{name} moved {move:+.1f}% ({direction}) in Q{q} {y} — above your {threshold}% alert.",
        "detail": {"commodity_id": commodity_id, "commodity": name, "move_pct": round(move, 2),
                   "year": y, "quarter": q},
    }


def _gap_trigger(db, cm, threshold):
    from app.services.costing_engine import calculate_should_cost
    if not cm.current_formula:
        return None
    sc = calculate_should_cost(db, cm).should_cost
    latest = (
        db.query(ActualPrice).filter(ActualPrice.cost_model_id == cm.id)
        .order_by(ActualPrice.year.desc(), ActualPrice.quarter.desc()).first()
    )
    if not latest or not sc:
        return None
    gap_pct = (float(latest.price) - sc) / sc * 100
    if abs(gap_pct) < float(threshold):     # any gap beyond the threshold, either direction
        return None
    name = cm.product.name if cm.product else "product"
    direction = "above" if gap_pct > 0 else "below"
    return {
        "dedup": f"gap:{cm.id}:{latest.year}Q{latest.quarter}:{'up' if gap_pct > 0 else 'down'}",
        "message": f"{name} is {abs(gap_pct):.1f}% {direction} should-cost in Q{latest.quarter} {latest.year} "
                   f"— beyond your {threshold}% alert.",
        "detail": {"cost_model_id": str(cm.id), "product": name, "gap_pct": round(gap_pct, 2),
                   "should_cost": round(sc, 2), "actual": float(latest.price),
                   "year": latest.year, "quarter": latest.quarter},
    }


def _buy_window_trigger(db, cm):
    from app.routers.portfolio import _buy_signal
    sig = _buy_signal(db, cm)
    if sig is None or sig.signal in ("neutral", "insufficient"):
        return None
    name = cm.product.name if cm.product else "product"
    verb = "cheap" if sig.signal == "cheap" else "expensive"
    return {
        "dedup": f"buy_window:{cm.id}:{sig.signal}:{sig.deviation_pct}",
        "message": f"{name} looks {verb} now ({sig.deviation_pct:+.1f}% vs its 4-quarter average).",
        "detail": {"cost_model_id": str(cm.id), "product": name, "signal": sig.signal,
                   "deviation_pct": sig.deviation_pct},
    }


def _triggers_for_subscription(db: Session, sub: AlertSubscription):
    """Yield trigger dicts for one subscription, expanding portfolio-wide scope."""
    thr = sub.threshold_pct
    if sub.trigger_type == "index_move":
        if sub.commodity_id:
            t = _index_move_trigger(db, sub.commodity_id, thr)
            if t:
                yield t
        else:  # portfolio-wide: every commodity referenced by the team's formulas
            cids = set()
            for cm in db.query(CostModel).filter(CostModel.team_id == sub.team_id).all():
                fv = cm.current_formula
                if fv:
                    for c in fv.components:
                        if c.commodity_id:
                            cids.add(c.commodity_id)
            for cid in cids:
                t = _index_move_trigger(db, cid, thr)
                if t:
                    yield t
    elif sub.trigger_type in ("gap", "buy_window"):
        models = ([db.query(CostModel).filter(CostModel.id == sub.cost_model_id).first()]
                  if sub.cost_model_id
                  else db.query(CostModel).filter(CostModel.team_id == sub.team_id).all())
        for cm in models:
            if not cm:
                continue
            t = _gap_trigger(db, cm, thr) if sub.trigger_type == "gap" else _buy_window_trigger(db, cm)
            if t:
                yield t


def _deliver(db: Session, sub: AlertSubscription, team: Team, message: str) -> bool:
    """Send one alert over its channel. Returns True if delivered."""
    try:
        if sub.channel == "slack":
            if not team.slack_webhook_url:
                return False
            httpx.post(team.slack_webhook_url, json={"text": f":rotating_light: {message}"}, timeout=10)
            return True
        # default: email the subscribing user
        from app.services.email import send_alert_email
        user = db.query(User).filter(User.id == sub.user_id).first()
        if not user:
            return False
        return send_alert_email(user.email, message, f"{settings.app_url}")
    except Exception:
        return False


def evaluate_team_alerts(db: Session, team_id: uuid.UUID) -> list[AlertEvent]:
    """Evaluate all active subscriptions for a team; create + deliver new alerts.
    Deduped against existing AlertEvents by dedup_key. Returns created events."""
    subs = (
        db.query(AlertSubscription)
        .filter(AlertSubscription.team_id == team_id, AlertSubscription.active == True)  # noqa: E712
        .all()
    )
    team = db.query(Team).filter(Team.id == team_id).first()
    created: list[AlertEvent] = []
    for sub in subs:
        for trig in _triggers_for_subscription(db, sub):
            exists = (
                db.query(AlertEvent)
                .filter(AlertEvent.team_id == team_id, AlertEvent.dedup_key == trig["dedup"])
                .first()
            )
            if exists:
                continue
            delivered = _deliver(db, sub, team, trig["message"])
            ev = AlertEvent(
                team_id=team_id, subscription_id=sub.id, trigger_type=sub.trigger_type,
                message=trig["message"], detail=trig["detail"], dedup_key=trig["dedup"],
                channel=sub.channel, delivered=delivered,
            )
            db.add(ev)
            db.flush()
            created.append(ev)
    db.commit()
    return created
