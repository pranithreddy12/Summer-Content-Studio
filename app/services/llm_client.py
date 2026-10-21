"""Provider router: picks Anthropic or Mistral per LLM_PROVIDER, with fallback in auto mode."""

import logging
from typing import Optional

from app.config import Settings
from app.services.claude_client import ClaudeClient, ClaudeResult, GenerationError
from app.services.mistral_client import MistralClient

logger = logging.getLogger("app.services.llm_client")

# Provider-side failures worth retrying on the other provider. Refusals, bad output and
# bad requests are not (the other model would likely behave the same and cost more).
FALLBACK_CATEGORIES = {
    "auth_error", "permission_denied", "model_not_found", "rate_limited",
    "timeout", "connection_error", "upstream_error",
}


class LLMClient:
    """Same interface as ClaudeClient (generate / is_configured / settings)."""

    def __init__(self, settings: Settings, anthropic: Optional[ClaudeClient] = None,
                 mistral: Optional[MistralClient] = None):
        self.settings = settings
        self.anthropic = anthropic or ClaudeClient(settings)
        self.mistral = mistral or MistralClient(settings)

    @property
    def is_configured(self) -> bool:
        return self.anthropic.is_configured or self.mistral.is_configured

    def _order(self) -> list:
        mode = self.settings.LLM_PROVIDER
        if mode == "anthropic":
            return [self.anthropic]
        if mode == "mistral":
            return [self.mistral]
        return [c for c in (self.anthropic, self.mistral) if c.is_configured]

    def generate(self, system: str, user: str, images=None) -> ClaudeResult:
        order = self._order()
        if not order or not order[0].is_configured:
            raise GenerationError(
                "not_configured",
                "No LLM provider configured. Set ANTHROPIC_API_KEY or MISTRAL_API_KEY. See README.",
                503,
            )
        first = order[0]
        try:
            return first.generate(system, user, images=images)
        except GenerationError as e:
            e.provider = e.provider or getattr(first, "provider", "anthropic")
            if len(order) > 1 and e.category in FALLBACK_CATEGORIES:
                logger.warning("provider %s failed (%s); falling back to %s",
                               e.provider, e.category, order[1].provider)
                return order[1].generate(system, user, images=images)
            raise
