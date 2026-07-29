import uuid
from datetime import datetime

from pydantic import BaseModel, Field

TRIGGER_TYPES = {"index_move", "gap", "buy_window"}
CHANNELS = {"email", "slack"}


class SubscriptionCreate(BaseModel):
    trigger_type: str
    cost_model_id: uuid.UUID | None = None
    commodity_id: int | None = None
    threshold_pct: float = Field(default=5.0, ge=0, le=100)
    channel: str = "email"


class SubscriptionUpdate(BaseModel):
    threshold_pct: float | None = Field(default=None, ge=0, le=100)
    channel: str | None = None
    active: bool | None = None


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    trigger_type: str
    cost_model_id: uuid.UUID | None = None
    commodity_id: int | None = None
    threshold_pct: float
    channel: str
    active: bool
    created_at: datetime
    scope_label: str | None = None   # "All products", a product name, or a commodity name

    model_config = {"from_attributes": True}


class AlertEventOut(BaseModel):
    id: uuid.UUID
    trigger_type: str
    message: str
    detail: dict | None = None
    channel: str
    delivered: bool
    triggered_at: datetime

    model_config = {"from_attributes": True}


class SlackWebhookUpdate(BaseModel):
    slack_webhook_url: str | None = None


class SlackWebhookOut(BaseModel):
    configured: bool
    slack_webhook_url: str | None = None   # populated for owner/admin only
