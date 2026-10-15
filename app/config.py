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

    # Media
    MEDIA_UPLOAD_DIR: Path = Path("./data/media")
    MEDIA_SYNC_DIR: Optional[Path] = None
    MAX_IMAGE_MB: int = 25
    MAX_VIDEO_MB: int = 500

    # Timezone for display (IANA zone name)
    APP_TIMEZONE: str = "UTC"

    # Optional Basic Auth for dashboard
    DASHBOARD_USERNAME: Optional[str] = None
    DASHBOARD_PASSWORD: Optional[SecretStr] = None

    # Rate limiting
    GENERATION_RATE_LIMIT_PER_MINUTE: int = 10

    @field_validator("APP_ENV")
    @classmethod
    def validate_env(cls, v: str) -> str:
        if v not in ("development", "production"):
            raise ValueError("APP_ENV must be 'development' or 'production'")
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
    def basic_auth_enabled(self) -> bool:
        """Check if basic auth is enabled (both username and password set)."""
        return (
            self.DASHBOARD_USERNAME is not None
            and self.DASHBOARD_USERNAME != ""
            and self.DASHBOARD_PASSWORD is not None
            and self.DASHBOARD_PASSWORD.get_secret_value() != ""
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear the settings cache (useful for testing)."""
    get_settings.cache_clear()