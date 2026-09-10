import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# SCRUM-79 adds the window-backed trigger. The set stays closed and
# validated on create — a typo should 422, not create a subscription that
# silently never fires.
TRIGGER_TYPES = {"index_move", "gap", "buy_window", "negotiation_window"}
THRESHOLD_UNITS = {"pct", "currency"}
CHANNELS = {"email", "slack"}


class SubscriptionCreate(BaseModel):
    trigger_type: str
    cost_model_id: uuid.UUID | None = None
    commodity_id: int | None = None
    # SCRUM-79: a window can be scoped to a supplier or a contract.
    supplier_id: int | None = None
    contract_id: uuid.UUID | None = None
    # None = inherit the team default (services/thresholds.effective_threshold).
    threshold_pct: float | None = Field(default=None, ge=0, le=100)
    threshold_unit: str | None = None
    channel: str = "email"


class SubscriptionUpdate(BaseModel):
    threshold_pct: float | None = Field(default=None, ge=0, le=100)
    threshold_unit: str | None = None
    channel: str | None = None
    active: bool | None = None
    # Explicit opt-back-in to the team default; a null `threshold_pct` on a
    # PATCH-style body is indistinguishable from "not supplied", so clearing
    # needs its own flag.
    inherit_threshold: bool | None = None


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    trigger_type: str
    cost_model_id: uuid.UUID | None = None
    commodity_id: int | None = None
    supplier_id: int | None = None
    contract_id: uuid.UUID | None = None
    # The raw override (null = inheriting) plus what actually applies, so a UI
    # can show "10% (team default)" without a second call.
    threshold_pct: float | None = None
    threshold_unit: str | None = None
    effective_threshold_pct: float | None = None
    effective_threshold_unit: str | None = None
    threshold_source: str | None = None
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


class TeamThresholdUpdate(BaseModel):
    default_threshold_pct: float = Field(ge=0, le=100)
    default_threshold_unit: str = "pct"


class TeamThresholdOut(BaseModel):
    default_threshold_pct: float
    default_threshold_unit: str


class SlackWebhookUpdate(BaseModel):
    slack_webhook_url: str | None = None


class SlackWebhookOut(BaseModel):
    configured: bool
    slack_webhook_url: str | None = None   # populated for owner/admin only
