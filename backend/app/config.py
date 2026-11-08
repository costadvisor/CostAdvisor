from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Environment: "development" or "production". Controls cookie security flags.
    environment: str = "development"

    # SameSite for all auth cookies when is_prod (staging + production both —
    # app.*/api.* live on different subdomains of the same registrable domain,
    # which the SameSite spec treats as same-site, so "strict" should work for
    # the app's own XHR/fetch calls to the API. Kept as its own env-overridable
    # setting rather than hardcoded: if a cross-subdomain login flow ever breaks
    # in practice, this can be flipped back to "lax" or "none" via a Railway env
    # var + redeploy — no git revert or resync needed.
    cookie_samesite: str = "strict"

    # Database
    database_url: str = "postgresql://costadvisor:costadvisor@localhost:5432/costadvisor"

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""

    # Google Calendar — Fernet key for encrypting per-host refresh tokens.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    google_calendar_encryption_key: str = ""

    # Team-supplied provider credentials (Fastmarkets/Argus/ICIS, Scrum 26) — a
    # SEPARATE Fernet key from google_calendar_encryption_key above; different
    # secret domain, must not be shared. Generate the same way.
    provider_credential_encryption_key: str = ""

    # Web push (VAPID). Public key is public by definition and is served from
    # the API rather than baked into the frontend build, so rotating it does
    # not need a rebuild. Both empty = push disabled, and every path checks.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_contact_email: str = "support@costadvisor.org"

    # JWT
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 72          # used by admin impersonation tokens (create_jwt default)
    access_token_minutes: int = 15      # short-lived ca_token issued at /login; refreshed via /auth/refresh
    refresh_token_days: int = 7

    # URLs
    app_url: str = "http://localhost:5173"
    api_url: str = "http://localhost:8000"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Support + signup
    support_email: str = "alexis@staminachem.com"
    allow_signup: bool = True  # Flip to false to restrict to existing users only
    # Google Workspace domains whose accounts may sign up without a team invite
    # or an approved access request (our own staff). Comma-separated; empty
    # string = nobody bypasses the gate. Checked against Google's verified `hd`
    # claim, never the email text alone (see routers/auth.is_staff_account).
    staff_email_domains: str = "staminachem.com"

    # Observability (wired in Phase 13)
    sentry_dsn: str = ""

    # EIA (Energy Information Administration) API key for oil/gas scraping
    eia_api_key: str = ""

    # FRED (Federal Reserve Economic Data) API key — free at https://fred.stlouisfed.org/docs/api/api_key.html
    fred_api_key: str = ""

    # External data API base URLs (no auth needed)
    ecb_api_base: str = "https://data-api.ecb.europa.eu/service"
    eurostat_api_base: str = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1"
    worldbank_api_base: str = "https://api.worldbank.org/v2"

    # Email (SMTP — stdlib smtplib, no third-party SDK)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: bool = True   # STARTTLS on 587; set False + port 465 for implicit SSL
    email_from: str = "noreply@costadvisor.org"

    # Ollama (local LLM)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_timeout: int = 60
    # When false, ollama_generate() returns None on cache miss instead of calling Ollama.
    # Used in production with a pre-warmed Redis cache so no live LLM is needed.
    llm_enabled: bool = True

    # Anthropic (Claude API) — Wave 4 monthly news (SCRUM-213/214/215).
    # Separate from Ollama on purpose: news search + sorting run on Claude via
    # the organisation's key, and do not depend on the Hetzner box (which
    # staging cannot reach). Empty key = the news features report "not
    # configured" instead of failing mid-request.
    anthropic_api_key: str = ""
    anthropic_api_url: str = "https://api.anthropic.com"
    anthropic_timeout: int = 180
    # Model for search + profile drafting (needs judgement and web search).
    anthropic_model: str = "claude-sonnet-5-5"
    # A profile test run is capped so an editor's click can't run up a bill.
    news_test_run_max_searches: int = 5
    # Seconds a test run may take in total: under the ~100s proxy cutoff in front
    # of the API (Cloudflare), so the editor always gets an answer, not an error.
    news_test_run_time_budget: int = 75
    # Who can see the news features (SCRUM-213+) while they are being built.
    # Comma-separated emails; "*" = everyone with the Content Editor role;
    # empty = nobody, so the feature is off unless an environment switches it on.
    # Lets the code ride on dev/staging, visible only to the people testing it.
    news_preview_emails: str = ""
    # Monthly news sweep (SCRUM-214). Off unless an environment switches it on,
    # so the schedule never spends money somewhere nobody asked it to.
    news_sweep_enabled: bool = False
    # A run stops cleanly once it has spent this much (USD). The epic budgets
    # about $50 a month for news out of $500, so one run gets headroom, not more.
    news_run_spend_cap_usd: float = 75.0
    # Web searches Claude may run per profile in a sweep (the test run uses fewer).
    news_sweep_searches_per_profile: int = 8
    # Sent with every feed request. SEC EDGAR asks for a contact email in it:
    # set e.g. "CostAdvisor news (ops@yourdomain)" before adding SEC feeds.
    news_http_user_agent: str = "CostAdvisor news sweep"
    # "Run now" normally hands the run to the Celery worker. Where no worker
    # runs (a laptop, the local demo kit), set this to run it in the API process.
    news_runs_inline: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()