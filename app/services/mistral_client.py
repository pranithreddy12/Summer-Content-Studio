"""Mistral client - same interface as ClaudeClient, plain REST via httpx (no extra SDK)."""

import base64
import logging
from typing import Optional

import httpx

from app.config import Settings
from app.schemas import GeneratedContent
from app.services.claude_client import ClaudeResult, GenerationError

logger = logging.getLogger("app.services.mistral_client")

MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"

_JSON_INSTRUCTION = (
    "\n\nRespond with a single JSON object and nothing else, with exactly these keys: "
    '"content_format" (string), "hook" (string), "caption" (string), '
    '"hashtags" (array of strings), "call_to_action" (string), "content_notes" (string).'
)

_STATUS_MAP = {
    401: ("auth_error", "Mistral rejected the API key.", 502),
    403: ("permission_denied", "Mistral denied the request: key lacks permission/credits, or the model is not in your tier (try MISTRAL_MODEL=ministral-8b-latest).", 502),
    404: ("model_not_found", "Mistral model not found - check MISTRAL_MODEL.", 502),
    429: ("rate_limited", "Mistral rate limit reached, try again shortly.", 429),
}


class MistralClient:
    provider = "mistral"

    def __init__(self, settings: Settings, transport: Optional[httpx.BaseTransport] = None):
        self.settings = settings
        self._transport = transport  # tests inject httpx.MockTransport

    @property
    def is_configured(self) -> bool:
        return self.settings.mistral_configured

    @property
    def model(self) -> str:
        return self.settings.MISTRAL_MODEL

    def generate(self, system: str, user: str, images=None) -> ClaudeResult:
        if not self.is_configured:
            raise GenerationError("not_configured", "MISTRAL_API_KEY is not set.", 503, "mistral", self.model)

        user_content = user
        if images:
            user_content = [
                *({"type": "image_url",
                   "image_url": f"data:{i.media_type};base64,{base64.b64encode(i.data).decode()}"} for i in images),
                {"type": "text", "text": user},
            ]
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system + _JSON_INSTRUCTION},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": self.settings.MISTRAL_MAX_TOKENS,
            "temperature": 0.8,
        }
        headers = {"Authorization": f"Bearer {self.settings.MISTRAL_API_KEY.get_secret_value()}"}

        def fail(category: str, message: str, status: int) -> GenerationError:
            return GenerationError(category, message, status, "mistral", self.model)

        # No retry loop on purpose: a retry is a second paid call; the user can Regenerate.
        try:
            with httpx.Client(timeout=self.settings.CLAUDE_TIMEOUT_SECONDS, transport=self._transport) as http:
                resp = http.post(MISTRAL_URL, json=payload, headers=headers)
        except httpx.TimeoutException:
            raise fail("timeout", "Mistral request timed out. Try again.", 504)
        except httpx.TransportError:
            raise fail("connection_error", "Could not connect to Mistral API.", 503)

        if resp.status_code in _STATUS_MAP:
            raise fail(*_STATUS_MAP[resp.status_code])
        if resp.status_code >= 500:
            raise fail("upstream_error", "Mistral API is experiencing issues.", 503)
        if resp.status_code >= 400:
            logger.error("Mistral rejected request: status=%s", resp.status_code)
            raise fail("bad_request", "Mistral rejected the request.", 502)

        try:
            data = resp.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise fail("invalid_output", "Mistral returned an unexpected response.", 502)

        if choice.get("finish_reason") == "length":
            raise fail("truncated", "Mistral response was truncated. Try again or raise MISTRAL_MAX_TOKENS.", 502)

        try:
            content = GeneratedContent.model_validate_json(text)
        except ValueError as e:
            logger.error("Failed to parse Mistral output: %s", type(e).__name__)
            raise fail("invalid_output", "Mistral returned invalid JSON. Try regenerating.", 502)

        usage = data.get("usage") or {}
        return ClaudeResult(
            content=content,
            model=data.get("model") or self.model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            request_id=resp.headers.get("x-request-id"),
            provider="mistral",
        )
