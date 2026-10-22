"""Application configuration using pydantic-settings."""

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Application
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = "sqlite:///./data/app.db"

    # Anthropic / Claude
    ANTHROPIC_API_KEY: Optional[SecretStr] = None
    CLAUDE_MODEL: str = "claude-opus-5-5"
    CLAUDE_EFFORT: str = "medium"
    CLAUDE_MAX_TOKENS: int = 16000
    CLAUDE_TIMEOUT_SECONDS: float = 120.0
    CLAUDE_MAX_RETRIES: int = 2

    # Mistral (alternative / fallback provider)
    MISTRAL_API_KEY: Optional[SecretStr] = None
    MISTRAL_MODEL: str = "ministral-8b-latest"
    MISTRAL_MAX_TOKENS: int = 2048
    # auto = Anthropic if configured (Mistral as fallback on provider failure), else Mistral
    LLM_PROVIDER: str = "auto"

    # Image input: show the AI the asset (images; frames from videos via ffmpeg)
    ENABLE_IMAGE_INPUT: bool = True
    FFMPEG_PATH: Optional[str] = None  # default: ffmpeg on PATH
    MAX_FRAMES: int = 3

    # Media
    MEDIA_UPLOAD_DIR: Path = Path("./data/media")
    MEDIA_SYNC_DIR: Optional[Path] = None
    MAX_IMAGE_MB: int = 25
    MAX_VIDEO_MB: int = 500

    # ---- Phase 2: publishing modes + platforms ----
    # THE only publishing switch (PLAN_PHASE2 §2/§5). off / reminders / dry_run / live.
    PUBLISHING_MODE: str = "off"
    PUBLIC_BASE_URL: Optional[str] = None  # https:// URL; required for live + OAuth
    TOKEN_ENCRYPTION_KEY: Optional[SecretStr] = None  # Fernet.generate_key() output
    MEDIA_URL_SECRET: Optional[SecretStr] = None  # HMAC key for /m/{token} public media URLs

    INSTAGRAM_APP_ID: Optional[str] = None
    INSTAGRAM_APP_SECRET: Optional[SecretStr] = None
    TIKTOK_CLIENT_KEY: Optional[str] = None
    TIKTOK_CLIENT_SECRET: Optional[SecretStr] = None
    TIKTOK_POST_MODE: str = "inbox"  # inbox | direct (direct = mock-only until audited)

    # Worker
    WORKER_INTERVAL_SECONDS: int = 60
    PUBLISH_MAX_ATTEMPTS: int = 3
    PLATFORM_TIMEOUT_SECONDS: float = 60.0

    # Planner
    AUTO_PLAN_ENABLED: bool = False
    PLANNER_RUN_HOUR: int = 6
    PLANNER_LEAD_MINUTES: int = 30
    PLANNER_MAX_TOPUP_PER_DAY: int = 3

    # Notifications (credentials live only in env - never logged/returned)
    NOTIFY_PROVIDER: str = "none"  # none | telegram | ntfy | pushover
    TELEGRAM_BOT_TOKEN: Optional[SecretStr] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    NTFY_SERVER: str = "https://ntfy.sh"
    NTFY_TOPIC: Optional[SecretStr] = None
    PUSHOVER_APP_TOKEN: Optional[SecretStr] = None
    PUSHOVER_USER_KEY: Optional[SecretStr] = None

    # Dropbox sync disk guard
    MIN_FREE_DISK_GB: float = 2.0

    @field_validator("PUBLISHING_MODE")
    @classmethod    def validate_publishing_mode(cls, v: str) -> str:
        if v not in ("off", "reminders", "dry_run", "live"):
            raise ValueError("PUBLISHING_MODE must be 'off', 'reminders', 'dry_run' or 'live'")
        return v

    @field_validator("TIKTOK_POST_MODE")
    @classmethod    def validate_tiktok_post_mode(cls, v: str) -> str:
        if v not in ("inbox", "direct"):
            raise ValueError("TIKTOK_POST_MODE must be 'inbox' or 'direct'")
        return v

    @field_validator("NOTIFY_PROVIDER")
    @classmethod    def validate_notify_provider(cls, v: str) -> str:
        if v not in ("none", "telegram", "ntfy", "pushover"):
            raise ValueError("NOTIFY_PROVIDER must be 'none', 'telegram', 'ntfy' or 'pushover'")
        return v

    @field_validator("PUBLIC_BASE_URL")
    @classmethod    def validate_public_base_url(cls, v: Optional[str]) -> Optional[str]:
        if v in (None, ""):
            return None
        v = str(v).rstrip("/")
        if not v.startswith("https://"):
            raise ValueError("PUBLIC_BASE_URL must start with https://")
        return v

    @field_validator("PLANNER_RUN_HOUR", "PLANNER_LEAD_MINUTES")
    @classmethod    def validate_non_negative_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be >= 0")
        return v

    @field_validator(
        "TOKEN_ENCRYPTION_KEY", "MEDIA_URL_SECRET", "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET",
        "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "NTFY_TOPIC", "PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY", mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, v):
        # `KEY=` (copied from .env.example, or blanked by tests) means "not configured"
        return None if isinstance(v, str) and v.strip() == "" else v

    @field_validator("MEDIA_SYNC_DIR", mode="before")
    @classmethod
    def _coerce_empty_sync_dir(cls, v):
        # Treat an explicitly-empty value as unset so the sync endpoint can
        # report MEDIA_SYNC_DIR not configured.
        if v is None:
            return None
        s = str(v).strip()
        if s == "" or s == ".":
            return None
        return Path(s)

    # Timezone for display (IANA zone name)
    APP_TIMEZONE: str = "UTC"

    # Optional Basic Auth for dashboard
    DASHBOARD_USERNAME: Optional[str] = None
    DASHBOARD_PASSWORD: Optional[SecretStr] = None

    # Rate limiting
    GENERATION_RATE_LIMIT_PER_MINUTE: int = 10
    UPLOAD_RATE_LIMIT_PER_MINUTE: int = 20

    @field_validator("APP_ENV")
    @classmethod
    def validate_env(cls, v: str) -> str:
        if v not in ("development", "production"):
            raise ValueError("APP_ENV must be 'development' or 'production'")
        return v

    @field_validator("LLM_PROVIDER")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        if v not in ("auto", "anthropic", "mistral"):
            raise ValueError("LLM_PROVIDER must be 'auto', 'anthropic', or 'mistral'")
        return v

    @field_validator("CLAUDE_EFFORT")
    @classmethod
    def validate_effort(cls, v: str) -> str:
        if v not in ("low", "medium", "high"):
            raise ValueError("CLAUDE_EFFORT must be 'low', 'medium', or 'high'")
        return v

    @field_validator("APP_TIMEZONE")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        # Validate it's a valid IANA timezone
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(v)
        except Exception as e:
            raise ValueError(f"Invalid APP_TIMEZONE: {v}. Must be a valid IANA timezone.") from e
        return v

    @property
    def claude_configured(self) -> bool:
        """Check if Anthropic API key is configured."""
        return self.ANTHROPIC_API_KEY is not None and self.ANTHROPIC_API_KEY.get_secret_value() != ""

    @property
    def mistral_configured(self) -> bool:
        return self.MISTRAL_API_KEY is not None and self.MISTRAL_API_KEY.get_secret_value() != ""

    @property
    def basic_auth_enabled(self) -> bool:
        """Check if basic auth is enabled (both username and password set)."""
        return (
            self.DASHBOARD_USERNAME is not None
            and self.DASHBOARD_USERNAME != ""
            and self.DASHBOARD_PASSWORD is not None
            and self.DASHBOARD_PASSWORD.get_secret_value() != ""
        )

    @property
    def notify_configured(self) -> bool:
        """True when the chosen provider has its credentials."""
        if self.NOTIFY_PROVIDER == "telegram":
            return self.TELEGRAM_BOT_TOKEN is not None and bool(self.TELEGRAM_CHAT_ID)
        if self.NOTIFY_PROVIDER == "ntfy":
            return self.NTFY_TOPIC is not None
        if self.NOTIFY_PROVIDER == "pushover":
            return self.PUSHOVER_APP_TOKEN is not None and self.PUSHOVER_USER_KEY is not None
        return False

    @property
    def live_publishing_ready(self) -> bool:
        """Prerequisites for PUBLISHING_MODE=live (worker refuses without them, PLAN §2)."""
        return (
            self.PUBLIC_BASE_URL is not None
            and self.TOKEN_ENCRYPTION_KEY is not None
            and self.MEDIA_URL_SECRET is not None
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear the settings cache (useful for testing)."""
    get_settings.cache_clear()


def publishing_mode() -> str:
    """The one publishing switch (PLAN_PHASE2 rule 12).

    Every code path that could post checks the mode through this helper.
    """
    return get_settings().PUBLISHING_MODE