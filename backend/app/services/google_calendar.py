"""Google Calendar / Meet integration.

Each demo host connects their Google account once via the /auth/google-calendar/start
OAuth flow. Their refresh token is stored encrypted (Fernet) in the DemoHost row.
When a demo request is accepted, create_google_meet() uses that token to create a
Calendar event with a Google Meet conference on the host's primary calendar.
"""

import uuid
from datetime import datetime

from app.config import get_settings

settings = get_settings()


def _fernet():
    from cryptography.fernet import Fernet
    key = settings.google_calendar_encryption_key
    if not key:
        raise RuntimeError("GOOGLE_CALENDAR_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()


def create_google_meet(
    host,  # DemoHost model instance
    requester_email: str,
    requester_name: str,
    company: str,
    start_dt: datetime,
    end_dt: datetime,
) -> tuple[str, str]:
    """Create a Google Calendar event with Meet on the host's calendar.

    Returns (hangout_link, event_id). Raises on any Google API error.
    sendUpdates='all' causes Google to email calendar invites to both attendees.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    refresh_token = decrypt_token(host.google_refresh_token_encrypted)

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )
    creds.refresh(Request())

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    event_body = {
        "summary": f"CostAdvisor Demo — {company or requester_name}",
        "description": (
            f"Product demo with {requester_name} ({requester_email})\n"
            f"Company: {company}"
        ),
        "start": {
            "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
            "timeZone": "UTC",
        },
        "attendees": [
            {"email": host.google_email, "organizer": True},
            {"email": requester_email},
        ],
        "conferenceData": {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 24 * 60},
                {"method": "popup", "minutes": 15},
            ],
        },
    }

    result = (
        service.events()
        .insert(
            calendarId="primary",
            body=event_body,
            conferenceDataVersion=1,
            sendUpdates="all",
        )
        .execute()
    )

    meet_link = result.get("hangoutLink", "")
    event_id = result["id"]
    return meet_link, event_id
