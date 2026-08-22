"""Public demo-scheduling endpoints — no authentication required."""

from calendar import monthrange
from datetime import datetime, timezone, date as date_type
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db, bypass_rls_var
from app.models.demo import DemoHost, DemoBlockedSlot, DemoRequest
from app.schemas.demo import DemoRequestCreate, AvailableSlot
from app.services.email import send_demo_request_received_email

router = APIRouter()


def _time_to_minutes(t: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _minutes_to_time(mins: int) -> str:
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _slots_for_host(host: DemoHost, date_str: str, accepted_starts: set[str]) -> list[str]:
    """Return available slot start-times (HH:MM) for a host on a given date.

    Removes: blocked slots, already-accepted slots.
    Returns empty list if the date is not in the host's working_days.
    """
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return []

    # weekday(): Monday=0 … Sunday=6 — matches our working_days encoding
    if d.weekday() not in (host.working_days or []):
        return []

    start_mins = _time_to_minutes(host.working_start)
    end_mins = _time_to_minutes(host.working_end)
    duration = host.slot_duration_minutes or 30

    # Build full slot grid
    slots = []
    cur = start_mins
    while cur + duration <= end_mins:
        slots.append(_minutes_to_time(cur))
        cur += duration

    # Remove slots the host has blocked
    blocked_starts: set[str] = set()
    for bs in (host.blocked_slots or []):
        if bs.blocked_date == date_str:
            bs_start = _time_to_minutes(bs.start_time)
            bs_end = _time_to_minutes(bs.end_time)
            for s in slots:
                sm = _time_to_minutes(s)
                if sm >= bs_start and sm < bs_end:
                    blocked_starts.add(s)

    # Remove slots already accepted for this host
    available = [s for s in slots if s not in blocked_starts and s not in accepted_starts]
    return available


@router.get("/available-slots", response_model=list[AvailableSlot])
def get_available_slots(
    date: str = Query(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """Return available demo time slots for the given date across all active hosts.

    A slot is available if at least one active host is free at that time.
    """
    bypass_rls_var.set(True)
    try:
        hosts = db.query(DemoHost).filter(DemoHost.is_active == True).all()  # noqa: E712
        if not hosts:
            return []

        # For each host, find their already-accepted slots on this date
        slot_set: set[str] = set()
        for host in hosts:
            accepted = db.query(DemoRequest).filter(
                DemoRequest.assigned_host_id == host.id,
                DemoRequest.requested_date == date,
                DemoRequest.status == "accepted",
            ).all()
            accepted_starts = {r.requested_start for r in accepted}
            available = _slots_for_host(host, date, accepted_starts)
            slot_set.update(available)
    finally:
        bypass_rls_var.set(False)

    # Return sorted AvailableSlot objects
    sorted_starts = sorted(slot_set)
    result = []
    for start in sorted_starts:
        sm = _time_to_minutes(start)
        # Determine duration from any host that has this slot (use first match)
        duration = 30
        for host in hosts:
            if start in _slots_for_host(host, date, set()):
                duration = host.slot_duration_minutes or 30
                break
        end = _minutes_to_time(sm + duration)
        result.append(AvailableSlot(start_time=start, end_time=end))
    return result


@router.get("/available-dates", response_model=list[str])
def get_available_dates(
    year: int = Query(...),
    month: int = Query(...),
    db: Session = Depends(get_db),
):
    """Return YYYY-MM-DD strings that have at least one slot available in the given month.

    Only today and future dates are included — past dates are always omitted.
    """
    _, days_in_month = monthrange(year, month)
    today = date_type.today()

    bypass_rls_var.set(True)
    try:
        hosts = db.query(DemoHost).filter(DemoHost.is_active == True).all()  # noqa: E712
        if not hosts:
            return []

        # Load all accepted bookings for this month in one query
        month_prefix = f"{year:04d}-{month:02d}-"
        accepted_reqs = db.query(DemoRequest).filter(
            DemoRequest.status == "accepted",
            DemoRequest.requested_date.like(f"{month_prefix}%"),
        ).all()

        # Index accepted starts per host per date
        host_accepted: dict = {}
        for req in accepted_reqs:
            hid = str(req.assigned_host_id)
            host_accepted.setdefault(hid, {}).setdefault(req.requested_date, set()).add(req.requested_start)

        available_dates: list[str] = []
        for day in range(1, days_in_month + 1):
            d = date_type(year, month, day)
            if d < today:
                continue
            date_str = d.strftime("%Y-%m-%d")
            for host in hosts:
                hid = str(host.id)
                accepted_starts = host_accepted.get(hid, {}).get(date_str, set())
                if _slots_for_host(host, date_str, accepted_starts):
                    available_dates.append(date_str)
                    break
    finally:
        bypass_rls_var.set(False)

    return available_dates


@router.post("")
def submit_demo_request(
    payload: DemoRequestCreate,
    db: Session = Depends(get_db),
):
    """Public — submit a demo request. One active request allowed per email."""
    email = payload.email.lower().strip()

    bypass_rls_var.set(True)
    try:
        # Check for existing active request
        existing = db.query(DemoRequest).filter(
            DemoRequest.email == email,
            DemoRequest.status.in_(["pending", "accepted"]),
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail="A demo request already exists for this email. Check your inbox for updates.",
            )

        # Validate the requested slot is still available
        hosts = db.query(DemoHost).filter(DemoHost.is_active == True).all()  # noqa: E712
        slot_available = False
        for host in hosts:
            accepted = db.query(DemoRequest).filter(
                DemoRequest.assigned_host_id == host.id,
                DemoRequest.requested_date == payload.requested_date,
                DemoRequest.status == "accepted",
            ).all()
            accepted_starts = {r.requested_start for r in accepted}
            if payload.requested_start in _slots_for_host(host, payload.requested_date, accepted_starts):
                slot_available = True
                break

        if not slot_available:
            raise HTTPException(
                status_code=400,
                detail="This time slot is no longer available. Please choose another.",
            )

        req = DemoRequest(
            email=email,
            name=payload.name.strip(),
            phone=(payload.phone or "").strip() or None,
            company=payload.company.strip(),
            requested_date=payload.requested_date,
            requested_start=payload.requested_start,
            requested_end=payload.requested_end,
            visitor_timezone=payload.visitor_timezone,
        )
        db.add(req)
        db.commit()
    finally:
        bypass_rls_var.set(False)

    send_demo_request_received_email(email, payload.name)
    return {"status": "submitted"}
