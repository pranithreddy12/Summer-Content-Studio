"""API dependencies."""

from functools import lru_cache

from app.config import get_settings
from app.services.llm_client import LLMClient

_platform_http = None


_llm = {"key": None, "client": None}


def get_claude_client() -> LLMClient:
    """Module-level LLM router (Anthropic and/or Mistral) built from settings; rebuilt when the provider, model
    or effort changes on the Settings page (the worker notices within seconds).

    Tests override this via app.dependency_overrides[get_claude_client].
    """
    s = get_settings()
    key = (s.LLM_PROVIDER, s.CLAUDE_MODEL, s.CLAUDE_EFFORT, s.MISTRAL_MODEL)
    if _llm["key"] != key or _llm["client"] is None:
        _llm.update(key=key, client=LLMClient(s))
    return _llm["client"]


def reset_claude_client_cache() -> None:
    """Forget the LLM router (for tests)."""
    _llm.update(key=None, client=None)


get_claude_client.cache_clear = reset_claude_client_cache  # the old lru_cache API


def get_platform_http():
    """The one httpx client for platform calls (rule 2); tests override this dependency."""
    global _platform_http
    if _platform_http is None:
        from app.platforms import http_client

        _platform_http = http_client(get_settings())
    return _platform_http
