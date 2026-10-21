"""Tests for Claude client error mapping and parsing."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from app.config import get_settings
from app.schemas import GeneratedContent
from app.services.claude_client import ClaudeClient, GenerationError, GENERATED_CONTENT_SCHEMA


def _make_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _make_settings():
    return get_settings()


def _mock_response(json_str: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=json_str)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        _request_id="req_1",
        model="claude-opus-5-5",
    )


VALID_JSON = json.dumps({
    "content_format": "instagram reel caption",
    "hook": "Test hook",
    "caption": "Test caption",
    "hashtags": ["tag1", "tag2"],
    "call_to_action": "Follow",
    "content_notes": "Notes",
})


def _get_client_with_mock():
    client = ClaudeClient(_make_settings())
    assert client.is_configured, "Settings should have API key in tests"
    mock = MagicMock()
    client.client = mock
    return client, mock


class TestGenerateValid:
    def test_valid_json_parsed(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.return_value = _mock_response(VALID_JSON)

        result = client.generate("system prompt", "user prompt")

        assert isinstance(result.content, GeneratedContent)
        assert result.content.hook == "Test hook"
        assert result.content.hashtags == ["tag1", "tag2"]
        assert result.input_tokens == 10
        assert result.output_tokens == 20
        assert result.request_id == "req_1"
        assert result.model == "claude-opus-5-5"

    def test_thinking_block_before_text_handled(self):
        client, mock = _get_client_with_mock()
        resp = SimpleNamespace(
            stop_reason="end_turn",
            content=[
                SimpleNamespace(type="thinking", thinking="thinking..."),
                SimpleNamespace(type="text", text=VALID_JSON),
            ],
            usage=SimpleNamespace(input_tokens=5, output_tokens=15),
            _request_id="req_2",
            model="m",
        )
        mock.messages.create.return_value = resp

        result = client.generate("sys", "usr")
        assert result.content.caption == "Test caption"

    def test_schema_sent_to_api(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.return_value = _mock_response(VALID_JSON)

        client.generate("sys", "usr")
        call_kwargs = mock.messages.create.call_args[1]
        assert call_kwargs["output_config"]["format"]["schema"] == GENERATED_CONTENT_SCHEMA
        assert call_kwargs["output_config"]["effort"] in ("low", "medium", "high")


class TestInvalidOutput:
    def test_malformed_json(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.return_value = _mock_response("{not valid json")

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "invalid_output"
        assert exc.value.http_status == 502

    def test_missing_field(self):
        client, mock = _get_client_with_mock()
        incomplete = json.dumps({"hook": "h", "caption": "c"})
        mock.messages.create.return_value = _mock_response(incomplete)

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "invalid_output"

    def test_no_text_block(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.return_value = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="thinking", thinking="...")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            _request_id="r",
            model="m",
        )

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "invalid_output"


class TestStopReason:
    def test_refusal(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.return_value = _mock_response(VALID_JSON, stop_reason="refusal")

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "refusal"
        assert exc.value.http_status == 422

    def test_max_tokens(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.return_value = _mock_response(VALID_JSON, stop_reason="max_tokens")

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "truncated"
        assert exc.value.http_status == 502


class TestAPIErrors:
    def test_rate_limit(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.side_effect = anthropic.RateLimitError(
            "rate", response=httpx.Response(429, request=_make_request()), body=None
        )

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "rate_limited"
        assert exc.value.http_status == 429

    def test_api_connection_error(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.side_effect = anthropic.APIConnectionError(request=_make_request())

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "connection_error"
        assert exc.value.http_status == 503

    def test_api_timeout(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.side_effect = anthropic.APITimeoutError(request=_make_request())

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "timeout"
        assert exc.value.http_status == 504

    def test_authentication_error_no_key_in_message(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.side_effect = anthropic.AuthenticationError(
            "bad key", response=httpx.Response(401, request=_make_request()), body=None
        )

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "auth_error"
        assert "sk-ant" not in exc.value.message
        assert "SECRET" not in exc.value.message

    def test_permission_denied(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.side_effect = anthropic.PermissionDeniedError(
            "denied", response=httpx.Response(403, request=_make_request()), body=None
        )

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "permission_denied"

    def test_not_found(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.side_effect = anthropic.NotFoundError(
            "not found", response=httpx.Response(404, request=_make_request()), body=None
        )

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "model_not_found"

    def test_bad_request(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.side_effect = anthropic.BadRequestError(
            "bad request", response=httpx.Response(400, request=_make_request()), body=None
        )

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "bad_request"
        assert exc.value.http_status == 502

    def test_server_error_5xx(self):
        client, mock = _get_client_with_mock()
        mock.messages.create.side_effect = anthropic.APIStatusError(
            "server error", response=httpx.Response(500, request=_make_request()), body=None
        )

        with pytest.raises(GenerationError) as exc:
            client.generate("sys", "usr")
        assert exc.value.category == "upstream_error"
        assert exc.value.http_status == 503


class TestNotConfigured:
    def test_missing_api_key_raises_503(self):
        import os
        from app.config import clear_settings_cache

        os.environ["ANTHROPIC_API_KEY"] = ""
        clear_settings_cache()

        try:
            settings = get_settings()
            client = ClaudeClient(settings)
            assert not client.is_configured

            with pytest.raises(GenerationError) as exc:
                client.generate("sys", "usr")
            assert exc.value.category == "not_configured"
            assert exc.value.http_status == 503
        finally:
            os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-SECRET123"
            clear_settings_cache()