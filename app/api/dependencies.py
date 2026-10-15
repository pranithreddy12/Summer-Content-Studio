"""API dependencies."""

from functools import lru_cache

from app.config import get_settings
from app.services.claude_client import ClaudeClient


@lru_cache(maxsize=1)
def get_claude_client() -> ClaudeClient:
    """Module-level singleton ClaudeClient built from settings.

    Tests override this via app.dependency_overrides[get_claude_client].
    """
    return ClaudeClient(get_settings())


def reset_claude_client_cache() -> None:
    """Clear the ClaudeClient cache (for tests)."""
    get_claude_client.cache_clear()