import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr


class DemoRequestCreate(BaseModel):
    email: EmailStr
    name: str
    phone: Optional[str] = None
    company: str
    requested_date: str     # "YYYY-MM-DD"
    requested_start: str    # "HH:MM"
    requested_end: str      # "HH:MM"
    visitor_timezone: str = "UTC"


class DemoRequestOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    phone: Optional[str]
    company: str
    requested_date: str
    requested_start: str
    requested_end: str
    visitor_timezone: str
    status: str
    meet_link: Optional[str]
    remarks: Optional[str]
    created_at: datetime
    reviewed_at: Optional[datetime]
    reviewed_by_name: Optional[str] = None
    assigned_host_name: Optional[str] = None

    model_config = {"from_attributes": True}


class DemoHostCreate(BaseModel):
    user_id: uuid.UUID
    timezone: str = "UTC"
    slot_duration_minutes: int = 30
    working_days: list[int] = [0, 1, 2, 3, 4]
    working_start: str = "09:00"
    working_end: str = "18:00"


class DemoHostUpdate(BaseModel):
    is_active: Optional[bool] = None
    timezone: Optional[str] = None
    slot_duration_minutes: Optional[int] = None
    working_days: Optional[list[int]] = None
    working_start: Optional[str] = None
    working_end: Optional[str] = None


class DemoHostOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    is_active: bool
    timezone: str
    slot_duration_minutes: int
    working_days: list[int]
    working_start: str
    working_end: str
    calendar_connected: bool
    google_email: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class BlockedSlotCreate(BaseModel):
    blocked_date: str   # "YYYY-MM-DD"
    start_time: str     # "HH:MM"
    end_time: str       # "HH:MM"


class BlockedSlotOut(BaseModel):
    id: uuid.UUID
    host_id: uuid.UUID
    blocked_date: str
    start_time: str
    end_time: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AvailableSlot(BaseModel):
    start_time: str   # "HH:MM"
    end_time: str     # "HH:MM"


class DemoRemarkUpdate(BaseModel):
    remarks: str
