"""Claude API client - wraps the Anthropic SDK with error mapping."""

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

import anthropic

from app.config import Settings
from app.schemas import GeneratedContent

logger = logging.getLogger("app.services.claude_client")


class GenerationError(Exception):
    """Generation error with category and HTTP status."""

    def __init__(self, category: str, message: str, http_status: int):
        self.category = category
        self.message = message
        self.http_status = http_status
        super().__init__(message)


@dataclass
class ClaudeResult:
    """Result from Claude generation."""
    content: GeneratedContent
    model: str
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    request_id: Optional[str]


# JSON schema for structured output
GENERATED_CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "content_format": {"type": "string"},
        "hook": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "call_to_action": {"type": "string"},
        "content_notes": {"type": "string"},
    },
    "required": ["content_format", "hook", "caption", "hashtags", "call_to_action", "content_notes"],
    "additionalProperties": False,
}


class ClaudeClient:
    """Wraps Anthropic SDK with error mapping and validation."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: Optional[anthropic.Anthropic] = None

        if settings.claude_configured:
            self.client = anthropic.Anthropic(
                api_key=settings.ANTHROPIC_API_KEY.get_secret_value(),
                timeout=settings.CLAUDE_TIMEOUT_SECONDS,
                max_retries=settings.CLAUDE_MAX_RETRIES,
            )

    @property
    def is_configured(self) -> bool:
        return self.client is not None

    def generate(self, system: str, user: str) -> ClaudeResult:
        """
        Call Claude with structured output (JSON schema).

        Raises GenerationError with appropriate category on failure.
        """
        if not self.client:
            raise GenerationError(
                "not_configured",
                "ANTHROPIC_API_KEY is not set. See README → Configure Claude.",
                503,
            )

        start_time = time.time()

        try:
            response = self.client.messages.create(
                model=self.settings.CLAUDE_MODEL,
                max_tokens=self.settings.CLAUDE_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": self.settings.CLAUDE_EFFORT,
                    "format": {"type": "json_schema", "schema": GENERATED_CONTENT_SCHEMA},
                },
            )
        except anthropic.AuthenticationError as e:
            raise GenerationError(
                "auth_error",
                "Anthropic rejected the API key.",
                502,
            )
        except anthropic.PermissionDeniedError as e:
            raise GenerationError(
                "permission_denied",
                "API key lacks permission / credits.",
                502,
            )
        except anthropic.NotFoundError as e:
            raise GenerationError(
                "model_not_found",
                f"Model '{self.settings.CLAUDE_MODEL}' not found — check CLAUDE_MODEL.",
                502,
            )
        except anthropic.RateLimitError as e:
            raise GenerationError(
                "rate_limited",
                "Anthropic rate limit reached, try again shortly.",
                429,
            )
        except anthropic.BadRequestError as e:
            # Log server-side but don't include message in response (may contain key)
            logger.error("Anthropic BadRequestError: %s", getattr(e, "message", str(e)))
            raise GenerationError(
                "bad_request",
                "Anthropic rejected the request.",
                502,
            )
        except anthropic.APITimeoutError as e:
            raise GenerationError(
                "timeout",
                "Claude request timed out. Try again.",
                504,
            )
        except anthropic.APIConnectionError as e:
            raise GenerationError(
                "connection_error",
                "Could not connect to Anthropic API.",
                503,
            )
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise GenerationError(
                    "upstream_error",
                    "Anthropic API is experiencing issues.",
                    503,
                )
            raise GenerationError(
                "bad_request",
                "Anthropic rejected the request.",
                502,
            )

        duration_ms = int((time.time() - start_time) * 1000)

        # Handle stop_reason
        if response.stop_reason == "refusal":
            raise GenerationError(
                "refusal",
                "Claude declined this request. Adjust the description/context and try again.",
                422,
            )
        elif response.stop_reason == "max_tokens":
            raise GenerationError(
                "truncated",
                "Claude response was truncated. Try again or increase CLAUDE_MAX_TOKENS.",
                502,
            )

        # Find first text block (thinking blocks may come first)
        text_block = None
        for block in response.content:
            if block.type == "text":
                text_block = block
                break

        if text_block is None:
            raise GenerationError(
                "invalid_output",
                "Claude returned no text content.",
                502,
            )

        # Parse JSON
        try:
            content = GeneratedContent.model_validate_json(text_block.text)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("Failed to parse Claude output as JSON: %s", type(e).__name__)
            raise GenerationError(
                "invalid_output",
                "Claude returned invalid JSON. Try regenerating.",
                502,
            )

        # Extract usage
        input_tokens = getattr(response.usage, "input_tokens", None)
        output_tokens = getattr(response.usage, "output_tokens", None)
        request_id = getattr(response, "_request_id", None)

        return ClaudeResult(
            content=content,
            model=response.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=request_id,
        )