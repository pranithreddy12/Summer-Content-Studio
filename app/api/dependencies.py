"""API dependencies."""

from functools import lru_cache

from app.config import get_settings
from app.services.llm_client import LLMClient

_platform_http = None


@lru_cache(maxsize=1)
def get_claude_client() -> LLMClient:
    """Module-level singleton LLM router (Anthropic and/or Mistral) built from settings.

    Tests override this via app.dependency_overrides[get_claude_client].
    """
    return LLMClient(get_settings())


def reset_claude_client_cache() -> None:
    """Clear the ClaudeClient cache (for tests)."""
    get_claude_client.cache_clear()


def get_platform_http():
    """The one httpx client for platform calls (rule 2); tests override this dependency."""
    global _platform_http
    if _platform_http is None:
        from app.platforms import http_client

        _platform_http = http_client(get_settings())
    return _platform_http
