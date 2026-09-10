"""Team-supplied provider credential storage (Scrum 26).

Encryption mirrors services/google_calendar.py's Fernet pattern exactly, but
keyed off a separate setting (different secret domain) and operating on a
JSON dict rather than a single token string, since credential shape varies
per provider (API key vs client-id+secret vs basic auth).
"""
import json
import uuid

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.index_data import TeamProviderCredential

settings = get_settings()


def _fernet():
    from cryptography.fernet import Fernet
    key = settings.provider_credential_encryption_key
    if not key:
        raise RuntimeError("PROVIDER_CREDENTIAL_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_credential(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_credential(encrypted: str) -> dict:
    return json.loads(_fernet().decrypt(encrypted.encode()).decode())


def get_credential(db: Session, team_id: uuid.UUID, provider: str) -> TeamProviderCredential | None:
    """Look up a team's credential row for a provider. `provider` is
    normalized the same way it's stored — case/whitespace must not cause a
    lookup miss that reads as a false "missing credential" bug report."""
    provider = provider.strip().lower()
    return (
        db.query(TeamProviderCredential)
        .filter(TeamProviderCredential.team_id == team_id, TeamProviderCredential.provider == provider)
        .first()
    )
