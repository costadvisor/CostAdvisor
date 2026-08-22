from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from jose import jwt
from authlib.integrations.httpx_client import AsyncOAuth2Client

from app.config import get_settings
from app.database import get_db, current_user_id_var, bypass_rls_var, impersonating_admin_email_var
from app.models.user import User
from app.models.invite import TeamInvite
from app.models.access_request import PlatformAccessRequest
from app.models.demo import DemoHost
from app.models.refresh_token import RefreshToken
from app.rate_limit import limiter
from app.schemas.user import UserOut, UserWithTeams
from app.services.email import send_welcome_email
from app.services.audit import log_auth_event

router = APIRouter()
settings = get_settings()

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def create_jwt(user_id: uuid.UUID, expiry_hours: float | None = None) -> str:
    """Create a JWT token for a user. Defaults to `jwt_expiry_hours` (used by admin
    impersonation tokens, unchanged); /login passes a short `access_token_minutes`
    override instead (Scrum 9 — short-lived access token + rotating refresh token)."""
    hours = expiry_hours if expiry_hours is not None else settings.jwt_expiry_hours
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=hours),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _issue_refresh_token(db: Session, user_id: uuid.UUID) -> str:
    """Mint a new opaque refresh token, store only its hash, return the raw value
    (only ever sent once, as the ca_refresh cookie)."""
    raw = secrets.token_urlsafe(48)
    db.add(RefreshToken(
        user_id=user_id,
        token_hash=_hash_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
    ))
    return raw


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: extract and validate JWT from cookie, return User.

    Also sets the per-request RLS context so Postgres row-level-security
    policies can filter by the authenticated user's team memberships.
    """
    token = request.cookies.get("ca_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # The users table is global (no RLS), so we can look the user up before
    # the RLS context is established. After this point, every subsequent
    # query in this request runs under the user's identity.
    bypass_rls_var.set(True)
    user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()
    bypass_rls_var.set(False)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="User not found")

    current_user_id_var.set(str(user.id))
    # Super-admins bypass RLS policies entirely (covers admin.py endpoints
    # that read across teams). App-layer `require_super_admin` still gates
    # these routes; bypass here just lets the query itself return rows.
    if user.is_super_admin:
        bypass_rls_var.set(True)

    # If ca_admin_token is present this request is running as an impersonated user.
    # Decode it so audit log entries can be marked with who is impersonating.
    admin_cookie = request.cookies.get("ca_admin_token")
    if admin_cookie:
        try:
            admin_payload = jwt.decode(admin_cookie, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            admin_uid = admin_payload.get("sub")
            if admin_uid:
                bypass_rls_var.set(True)
                admin_user = db.query(User).filter(User.id == uuid.UUID(admin_uid)).first()
                bypass_rls_var.set(False)
                if admin_user:
                    impersonating_admin_email_var.set(admin_user.email)
        except Exception:
            pass

    # Attach the user to any error reports Sentry captures on this request.
    from app.observability import set_user_context
    set_user_context(str(user.id), user.email)
    return user


@router.get("/login")
@limiter.limit("10/minute")
async def login(request: Request):
    """Redirect user to Google OAuth consent screen.

    Scrum 9 — PKCE (S256) + a real per-request `state`, both verified in
    /callback. Previously `state` was generated then discarded (bound to `_state`)
    and never checked against anything, and no PKCE verifier was used at all."""
    client = AsyncOAuth2Client(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=f"{settings.api_url}/auth/callback",
        scope="openid email profile",
        code_challenge_method="S256",
    )
    verifier = secrets.token_urlsafe(64)
    uri, state = client.create_authorization_url(GOOGLE_AUTH_URL, code_verifier=verifier)

    response = RedirectResponse(url=uri)
    is_prod = settings.environment != "development"
    response.set_cookie(
        "oauth_state",
        f"{state}:{verifier}",
        httponly=True,
        secure=is_prod,
        samesite="none" if is_prod else "lax",
        max_age=600,
    )
    return response


@router.get("/callback")
@limiter.limit("10/minute")
async def callback(request: Request, db: Session = Depends(get_db)):
    """Handle Google OAuth callback.

    Scrum 9 — validates `state` against the cookie /login set (previously not
    read or checked at all) and completes the PKCE exchange with the matching
    `code_verifier`."""
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    state_param = request.query_params.get("state")
    oauth_state_cookie = request.cookies.get("oauth_state", "")
    if not state_param or not oauth_state_cookie or ":" not in oauth_state_cookie:
        raise HTTPException(status_code=400, detail="Missing state")
    stored_state, verifier = oauth_state_cookie.split(":", 1)
    if state_param != stored_state:
        raise HTTPException(status_code=400, detail="State mismatch")

    client = AsyncOAuth2Client(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=f"{settings.api_url}/auth/callback",
    )

    # Exchange code for tokens
    token = await client.fetch_token(GOOGLE_TOKEN_URL, code=code, code_verifier=verifier)

    # Get user info from Google
    client.token = token
    resp = await client.get(GOOGLE_USERINFO_URL)
    userinfo = resp.json()

    google_id = userinfo["sub"]
    email = userinfo["email"]
    display_name = userinfo.get("name", email.split("@")[0])
    avatar_url = userinfo.get("picture")

    # RLS bypass: no user identity is established yet during the OAuth callback.
    bypass_rls_var.set(True)
    user = db.query(User).filter(User.google_id == google_id).first()

    # Account linking: bind a pre-provisioned account (seeded or otherwise
    # created ahead of first login, matched by its Google-verified, unique
    # email) to this Google identity on first sign-in. Without this the row
    # would never match by google_id and a new-signup insert would collide on
    # the unique email. A linked user then flows through the returning-user
    # path below (no signup gate, since it was provisioned deliberately).
    if user is None:
        prelinked = db.query(User).filter(
            User.email == email,
            User.deleted_at == None,  # noqa: E711
        ).first()
        if prelinked is not None:
            prelinked.google_id = google_id
            user = prelinked

    if user is None:
        if not settings.allow_signup:
            log_auth_event(db, email, "login_failed", reason="signup_disabled", request=request)
            db.commit()
            bypass_rls_var.set(False)
            return RedirectResponse(
                url=f"{settings.app_url}?login_error=signup_disabled", status_code=302
            )

        # Gate: new users must have a pending team invite OR an accepted access request.
        has_team_invite = db.query(TeamInvite).filter(
            TeamInvite.invited_email == email,
            TeamInvite.status == "pending",
            TeamInvite.expires_at > datetime.now(timezone.utc),
        ).first()

        has_access = db.query(PlatformAccessRequest).filter(
            PlatformAccessRequest.email == email,
            PlatformAccessRequest.status == "accepted",
        ).first()

        if not has_team_invite and not has_access:
            # Determine the right error to show in the UI
            pending_req = db.query(PlatformAccessRequest).filter(
                PlatformAccessRequest.email == email,
                PlatformAccessRequest.status == "pending",
            ).first()
            rejected_req = db.query(PlatformAccessRequest).filter(
                PlatformAccessRequest.email == email,
                PlatformAccessRequest.status == "rejected",
            ).first()
            if pending_req:
                error = "access_pending"
            elif rejected_req:
                error = "access_rejected"
            else:
                error = "access_needed"
            log_auth_event(db, email, "login_failed", reason=error, request=request)
            db.commit()
            bypass_rls_var.set(False)
            return RedirectResponse(
                url=f"{settings.app_url}?login_error={error}", status_code=302
            )

        user = User(
            google_id=google_id,
            email=email,
            display_name=display_name,
            avatar_url=avatar_url,
            company=has_access.company if has_access else None,
        )
        db.add(user)
        db.flush()
        user.last_login_at = datetime.now(timezone.utc)

        # Send welcome email for users coming through the team-invite bypass.
        # (Access-request approvals get their own email at accept time via admin.)
        if has_team_invite and not has_access:
            send_welcome_email(email, display_name, settings.app_url)
    else:
        user.last_login_at = datetime.now(timezone.utc)
        # Don't overwrite display_name on returning users — they may have set
        # their own in /profile. Only seed it if it was never populated.
        if not user.display_name:
            user.display_name = display_name
        user.avatar_url = avatar_url

    log_auth_event(db, email, "login_success", user_id=user.id, request=request)
    db.commit()
    bypass_rls_var.set(False)

    # Issue a short-lived access token (ca_token) + a rotating refresh token
    # (ca_refresh, scoped to /auth/refresh) and redirect to the frontend.
    token_str = create_jwt(user.id, expiry_hours=settings.access_token_minutes / 60)
    refresh_raw = _issue_refresh_token(db, user.id)
    db.commit()

    response = RedirectResponse(url=settings.app_url, status_code=302)
    is_prod = settings.environment != "development"
    response.set_cookie(
        key="ca_token",
        value=token_str,
        httponly=True,
        secure=is_prod,
        samesite="none" if is_prod else "lax",
        max_age=settings.access_token_minutes * 60,
    )
    response.set_cookie(
        key="ca_refresh",
        value=refresh_raw,
        httponly=True,
        secure=is_prod,
        samesite="none" if is_prod else "lax",
        max_age=settings.refresh_token_days * 86400,
        path="/auth/refresh",
    )
    response.delete_cookie("oauth_state", secure=is_prod, samesite="none" if is_prod else "lax")
    return response


@router.get("/me", response_model=UserWithTeams)
def get_me(current_user: User = Depends(get_current_user)):
    """Return the current authenticated user with their team memberships."""
    return current_user


@router.put("/me", response_model=UserOut)
def update_me(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update the current user's editable profile fields."""
    if "display_name" in payload:
        name = (payload.get("display_name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="display_name cannot be empty")
        if len(name) > 128:
            raise HTTPException(status_code=400, detail="display_name too long")
        current_user.display_name = name
    if "company" in payload:
        company = (payload.get("company") or "").strip() or None
        if company and len(company) > 128:
            raise HTTPException(status_code=400, detail="company too long")
        current_user.company = company
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


ALLOWED_THEMES = {"default", "light", "amber", "staminachem"}


@router.put("/me/theme", response_model=UserOut)
def set_my_theme(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update the current user's theme preference."""
    theme = payload.get("theme")
    if theme not in ALLOWED_THEMES:
        raise HTTPException(status_code=400, detail="invalid theme")
    current_user.theme = theme
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/refresh")
@limiter.limit("30/minute")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    """Scrum 9 — exchange the ca_refresh cookie for a new short-lived ca_token,
    rotating the refresh token itself (the old one is revoked and cannot be reused).
    Called by the frontend's api.js on a 401, transparently to the user."""
    raw = request.cookies.get("ca_refresh")
    if not raw:
        raise HTTPException(status_code=401, detail="No refresh token")

    bypass_rls_var.set(True)
    try:
        row = db.query(RefreshToken).filter(RefreshToken.token_hash == _hash_token(raw)).first()
        if not row or row.revoked_at is not None or row.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

        row.revoked_at = datetime.now(timezone.utc)
        new_raw = _issue_refresh_token(db, row.user_id)
        db.flush()
        new_row = db.query(RefreshToken).filter(RefreshToken.token_hash == _hash_token(new_raw)).first()
        row.replaced_by_id = new_row.id
        db.commit()
        user_id = row.user_id
    finally:
        bypass_rls_var.set(False)

    is_prod = settings.environment != "development"
    token_str = create_jwt(user_id, expiry_hours=settings.access_token_minutes / 60)
    response.set_cookie(
        key="ca_token", value=token_str, httponly=True, secure=is_prod,
        samesite="none" if is_prod else "lax", max_age=settings.access_token_minutes * 60,
    )
    response.set_cookie(
        key="ca_refresh", value=new_raw, httponly=True, secure=is_prod,
        samesite="none" if is_prod else "lax", max_age=settings.refresh_token_days * 86400,
        path="/auth/refresh",
    )
    return {"status": "refreshed"}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Clear the auth cookies and revoke the current refresh token so it can't be
    replayed after logout.

    Deliberately does NOT depend on get_current_user — logout must succeed even
    with an already-expired/invalid ca_token, so the audit-event user lookup is
    best-effort (via ca_refresh's stored user_id, else a soft ca_token decode)."""
    raw = request.cookies.get("ca_refresh")
    logout_user_id = None
    bypass_rls_var.set(True)
    try:
        if raw:
            row = db.query(RefreshToken).filter(RefreshToken.token_hash == _hash_token(raw)).first()
            if row and row.revoked_at is None:
                row.revoked_at = datetime.now(timezone.utc)
                logout_user_id = row.user_id
                db.commit()
        if logout_user_id is None:
            token = request.cookies.get("ca_token")
            if token:
                try:
                    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
                    logout_user_id = uuid.UUID(payload["sub"])
                except Exception:
                    pass
        if logout_user_id is not None:
            user = db.query(User).filter(User.id == logout_user_id).first()
            if user:
                log_auth_event(db, user.email, "logout", user_id=user.id, request=request)
                db.commit()
    finally:
        bypass_rls_var.set(False)

    is_prod = settings.environment != "development"
    same_site = "none" if is_prod else "lax"
    # Matching attributes to how the cookies were set — a mismatched Secure/SameSite
    # on delete_cookie can leave the browser holding onto the original cookie.
    response.delete_cookie("ca_token", httponly=True, secure=is_prod, samesite=same_site)
    response.delete_cookie("ca_refresh", httponly=True, secure=is_prod, samesite=same_site, path="/auth/refresh")
    from app.observability import clear_user_context
    clear_user_context()
    return {"status": "logged out"}


GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"


@router.get("/google-calendar/start")
@limiter.limit("10/minute")
async def google_calendar_start(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Start the Google Calendar OAuth flow for a demo host.

    Stores {state}:{user_id} in a short-lived HttpOnly cookie so the callback
    can verify state and know which user to update.
    """
    client = AsyncOAuth2Client(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=f"{settings.api_url}/auth/google-calendar/callback",
        scope=f"openid email profile {GOOGLE_CALENDAR_SCOPE}",
    )
    uri, state = client.create_authorization_url(
        GOOGLE_AUTH_URL,
        access_type="offline",
        prompt="consent",  # Forces refresh token issuance even for existing grants
    )
    response = RedirectResponse(url=uri)
    is_prod = settings.environment != "development"
    response.set_cookie(
        "gc_state",
        f"{state}:{str(current_user.id)}",
        httponly=True,
        secure=is_prod,
        samesite="none" if is_prod else "lax",
        max_age=600,
    )
    return response


@router.get("/google-calendar/callback")
@limiter.limit("10/minute")
async def google_calendar_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    """Handle the Google Calendar OAuth callback.

    Stores the encrypted refresh token in the DemoHost row for this user,
    then redirects back to the admin settings page.
    """
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    gc_state_cookie = request.cookies.get("gc_state", "")
    if not gc_state_cookie or ":" not in gc_state_cookie:
        raise HTTPException(status_code=400, detail="Missing or invalid state")

    # Cookie format: "{state}:{user_id}" — split on last colon
    last_colon = gc_state_cookie.rfind(":")
    user_id_str = gc_state_cookie[last_colon + 1:]

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid state")

    client = AsyncOAuth2Client(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=f"{settings.api_url}/auth/google-calendar/callback",
    )
    token = await client.fetch_token(GOOGLE_TOKEN_URL, code=code)

    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status_code=400,
            detail="No refresh token returned. Re-authorise and ensure 'prompt=consent' is set.",
        )

    # Fetch Google email for this token
    client.token = token
    resp = await client.get(GOOGLE_USERINFO_URL)
    google_email = resp.json().get("email", "")

    from app.services.google_calendar import encrypt_token
    encrypted = encrypt_token(refresh_token)

    bypass_rls_var.set(True)
    try:
        host = db.query(DemoHost).filter(DemoHost.user_id == user_id).first()
        if not host:
            host = DemoHost(user_id=user_id)
            db.add(host)
        host.google_email = google_email
        host.google_refresh_token_encrypted = encrypted
        host.google_token_expiry = None  # refresh token has no fixed expiry
        db.commit()
    finally:
        bypass_rls_var.set(False)

    response = RedirectResponse(url=f"{settings.app_url}/admin?tab=settings", status_code=302)
    is_prod = settings.environment != "development"
    response.delete_cookie("gc_state", secure=is_prod, samesite="none" if is_prod else "lax")
    return response