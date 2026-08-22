from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Environment: "development" or "production". Controls cookie security flags.
    environment: str = "development"

    # Database
    database_url: str = "postgresql://costadvisor:costadvisor@localhost:5432/costadvisor"

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""

    # Google Calendar — Fernet key for encrypting per-host refresh tokens.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    google_calendar_encryption_key: str = ""

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()