"""Application configuration. All secrets live in environment variables (.env)."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_str(name: str, default: str) -> str:
    """Environment string, treating empty values as unset (e.g. DATABASE_URL="")."""
    val = os.getenv(name, "").strip()
    return val if val else default


def load_env_file() -> None:
    """Load .env from repo root; silently ignore if missing (dev/test fall back to defaults)."""
    env_file = ROOT_DIR / ".env"
    if env_file.exists():
        load_dotenv(env_file)


load_env_file()

DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings:
    APP_NAME: str = os.getenv("APP_NAME", "DriveDoc Control")
    APP_ENV: str = os.getenv("APP_ENV", "development")
    DEBUG: bool = _env_bool("DEBUG", True)

    # --- Database ---
    SQLITE_PATH: Path = DATA_DIR / "app.db"
    DATABASE_URL: str = _env_str(
        "DATABASE_URL",
        f"sqlite:///{SQLITE_PATH.as_posix()}",
    )

    # --- Security ---
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = _env_int("ACCESS_TOKEN_EXPIRE_MINUTES", 60 * 24 * 30)
    TOKEN_COOKIE_NAME: str = "drivedoc_session"
    SESSION_COOKIE_SECURE: bool = _env_bool("SESSION_COOKIE_SECURE", False)
    SESSION_COOKIE_SAMESITE: str = os.getenv("SESSION_COOKIE_SAMESITE", "lax")

    # --- Google OAuth ---
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    OAUTH_AUTH_URI: str = "https://accounts.google.com/o/oauth2/v2/auth"
    OAUTH_TOKEN_URI: str = "https://oauth2.googleapis.com/token"
    OAUTH_USERINFO_URI: str = "https://openidconnect.googleapis.com/v1/userinfo"
    OAUTH_REDIRECT_URI: str = _env_str(
        "OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/callback"
    )
    GOOGLE_SCOPES: str = os.getenv(
        "GOOGLE_SCOPES",
        "openid email profile https://www.googleapis.com/auth/drive",
    )

    # --- Monitoring / Sync ---
    MONITOR_ENABLED: bool = _env_bool("MONITOR_ENABLED", True)
    SYNC_INTERVAL_SECONDS: int = _env_int("SYNC_INTERVAL_SECONDS", 60)
    SYNC_RETRY_BASE_SECONDS: int = _env_int("SYNC_RETRY_BASE_SECONDS", 2)
    SYNC_MAX_BACKOFF_SECONDS: int = _env_int("SYNC_MAX_BACKOFF_SECONDS", 300)
    SYNC_DELAY_BETWEEN_REQUESTS: float = float(_env_int("SYNC_DELAY_BETWEEN_REQUESTS_MS", 50)) / 1000.0

    # --- Attachments / uploads ---
    MAX_ATTACHMENT_BYTES: int = _env_int("MAX_ATTACHMENT_BYTES", 25 * 1024 * 1024)

    # --- Optional public webhook base URL for Google Drive push notifications ---
    # Drive push notifications require a public HTTPS endpoint. When configured,
    # the monitoring engine registers/renews watch channels on project roots.
    WEBHOOK_BASE_URL: str = os.getenv("WEBHOOK_BASE_URL", "")

    # --- First run helper ---
    def ensure_secret_key(self) -> str:
        """Return an app secret, generating + persisting one for local development."""
        if self.SECRET_KEY:
            return self.SECRET_KEY
        key_file = DATA_DIR / "secret.key"
        if key_file.exists():
            self.SECRET_KEY = key_file.read_text().strip()
        else:
            self.SECRET_KEY = secrets.token_urlsafe(64)
            key_file.write_text(self.SECRET_KEY)
        return self.SECRET_KEY


settings = Settings()