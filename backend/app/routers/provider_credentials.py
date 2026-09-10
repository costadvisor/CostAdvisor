"""Team-supplied provider credential management (Scrum 26).

Credential CRUD is gated stricter than plain `indexes.edit` — a vendor
credential is more sensitive than a scrape URL, so this mirrors the Scrum 23
supplier-benchmark precedent of gating sensitive data on team role
(owner/admin) rather than a broader custom permission.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.index_data import TeamProviderCredential
from app.routers.auth import get_current_user
from app.routers.indexes import require_team_access
from app.routers.teams import require_team_role
from app.schemas.provider_credential import (
    TeamProviderCredentialCreate, TeamProviderCredentialOut, ProviderInfoOut,
)
from app.services.audit import log_event
from app.services.provider_credentials import encrypt_credential, decrypt_credential
from app.services.providers import get_adapter, KNOWN_PROVIDERS, ProviderCredentialError

router = APIRouter()


@router.get("/provider-credentials/providers", response_model=list[ProviderInfoOut])
def list_providers(current_user: User = Depends(get_current_user)):
    """Which providers can be configured at all, and which have a real
    adapter today vs. are named but not yet supported."""
    return KNOWN_PROVIDERS


@router.get("/provider-credentials", response_model=list[TeamProviderCredentialOut])
def list_provider_credentials(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Never includes the secret — TeamProviderCredentialOut has no field for it."""
    require_team_access(db, current_user, team_id)
    return (
        db.query(TeamProviderCredential)
        .filter(TeamProviderCredential.team_id == team_id)
        .order_by(TeamProviderCredential.provider)
        .all()
    )


@router.post("/provider-credentials", response_model=TeamProviderCredentialOut)
def create_or_rotate_provider_credential(
    body: TeamProviderCredentialCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_team_role(db, current_user, body.team_id, ["owner", "admin"])
    provider = body.provider.strip().lower()

    existing = db.query(TeamProviderCredential).filter(
        TeamProviderCredential.team_id == body.team_id,
        TeamProviderCredential.provider == provider,
    ).first()
    rotated = existing is not None

    if existing:
        existing.credential_encrypted = encrypt_credential(body.credential)
        existing.status = "unverified"
        existing.last_error = None
        existing.updated_at = datetime.now(timezone.utc)
        credential = existing
    else:
        credential = TeamProviderCredential(
            team_id=body.team_id, provider=provider,
            credential_encrypted=encrypt_credential(body.credential),
            created_by=current_user.id,
        )
        db.add(credential)
    db.flush()

    # new_value carries only metadata — the plaintext credential never appears
    # in an audit row.
    log_event(
        db, body.team_id, current_user.id, "rotate" if rotated else "create",
        "team_provider_credential", str(credential.id),
        new_value={"provider": provider, "rotated": rotated},
    )
    db.commit()
    return credential


@router.delete("/provider-credentials/{credential_id}")
def delete_provider_credential(
    credential_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    credential = db.query(TeamProviderCredential).filter(TeamProviderCredential.id == credential_id).first()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")
    require_team_role(db, current_user, credential.team_id, ["owner", "admin"])

    # TeamIndexSource rows pointing at this provider are left in place —
    # they'll simply degrade to "missing" on their next fetch attempt
    # (acceptance criterion 3), no cleanup needed here.
    log_event(
        db, credential.team_id, current_user.id, "delete",
        "team_provider_credential", str(credential.id),
        previous_value={"provider": credential.provider},
    )
    db.delete(credential)
    db.commit()
    return {"status": "deleted"}


@router.post("/provider-credentials/{credential_id}/verify", response_model=TeamProviderCredentialOut)
async def verify_provider_credential(
    credential_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Explicit "test connection" action — one adapter call, no series data
    is written anywhere. Updates status/last_error/last_verified_at so the
    credential's health is visible without waiting for a real fetch."""
    credential = db.query(TeamProviderCredential).filter(TeamProviderCredential.id == credential_id).first()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")
    require_team_role(db, current_user, credential.team_id, ["owner", "admin"])

    try:
        adapter = get_adapter(credential.provider)
        await adapter.fetch_series(decrypt_credential(credential.credential_encrypted), "__verify__", "GLOBAL")
        credential.status, credential.last_error = "ok", None
    except ProviderCredentialError as exc:
        credential.status, credential.last_error = exc.reason, exc.detail
    credential.last_verified_at = datetime.now(timezone.utc)
    db.commit()
    return credential
