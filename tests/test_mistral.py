"""Mistral client + provider router tests (no network: httpx.MockTransport / fakes)."""

import json

import httpx
import pytest

from app.config import Settings
from app.schemas import GeneratedContent
from app.services.claude_client import ClaudeResult, GenerationError
from app.services.llm_client import LLMClient
from app.services.mistral_client import MistralClient

KEY = "mistral-test-SECRET456"
GOOD = {
    "content_format": "instagram reel caption", "hook": "Boo!", "caption": "Trick or treat?",
    "hashtags": ["#halloween"], "call_to_action": "", "content_notes": "AI label",
}


def settings(**kw) -> Settings:
    base = dict(_env_file=None, ANTHROPIC_API_KEY=None, MISTRAL_API_KEY=KEY)
    base.update(kw)
    return Settings(**base)


def mistral(handler, **kw) -> MistralClient:
    return MistralClient(settings(**kw), transport=httpx.MockTransport(handler))


def ok(body=None, finish="stop"):
    payload = {"model": "mistral-large-latest", "usage": {"prompt_tokens": 5, "completion_tokens": 7},
               "choices": [{"finish_reason": finish, "message": {"content": json.dumps(body or GOOD)}}]}
    return lambda req: httpx.Response(200, json=payload)


def test_valid_output_parsed_with_usage():
    r = mistral(ok()).generate("sys", "user")
    assert r.content.caption == "Trick or treat?" and r.provider == "mistral"
    assert (r.input_tokens, r.output_tokens) == (5, 7)


def test_request_uses_key_and_json_mode():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return ok()(req)

    mistral(handler).generate("sys", "user")
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert "hashtags" in seen["body"]["messages"][0]["content"]


@pytest.mark.parametrize("status,category", [
    (401, "auth_error"), (403, "permission_denied"), (404, "model_not_found"),
    (429, "rate_limited"), (400, "bad_request"), (503, "upstream_error"),
])
def test_http_errors_mapped_without_leaking_key(status, category):
    with pytest.raises(GenerationError) as exc:
        mistral(lambda req: httpx.Response(status, json={"message": f"bad {KEY}"})).generate("s", "u")
    assert exc.value.category == category and KEY not in exc.value.message
    assert exc.value.provider == "mistral"


def test_timeout_and_connection_errors():
    def boom(exc):
        def h(req):
            raise exc
        return h
    with pytest.raises(GenerationError) as e1:
        mistral(boom(httpx.ReadTimeout("t"))).generate("s", "u")
    assert e1.value.category == "timeout"
    with pytest.raises(GenerationError) as e2:
        mistral(boom(httpx.ConnectError("c"))).generate("s", "u")
    assert e2.value.category == "connection_error"


def test_malformed_and_truncated_output():
    bad = {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]}
    with pytest.raises(GenerationError) as e1:
        mistral(lambda req: httpx.Response(200, json=bad)).generate("s", "u")
    assert e1.value.category == "invalid_output"
    with pytest.raises(GenerationError) as e2:
        mistral(ok(finish="length")).generate("s", "u")
    assert e2.value.category == "truncated"
    with pytest.raises(GenerationError) as e3:
        mistral(ok({"caption": "missing fields"})).generate("s", "u")
    assert e3.value.category == "invalid_output"


def test_not_configured():
    with pytest.raises(GenerationError) as exc:
        MistralClient(settings(MISTRAL_API_KEY=None)).generate("s", "u")
    assert exc.value.category == "not_configured"


# ---- router ----------------------------------------------------------------

class Stub:
    def __init__(self, provider, configured=True, error=None):
        self.provider, self.is_configured, self.error, self.calls = provider, configured, error, 0

    def generate(self, system, user, images=None):
        self.calls += 1
        if self.error:
            raise self.error
        return ClaudeResult(content=GeneratedContent(**GOOD), model="m", input_tokens=1,
                            output_tokens=1, request_id=None, provider=self.provider)


def router(a, m, **kw):
    return LLMClient(settings(**kw), anthropic=a, mistral=m)


def test_auto_prefers_anthropic():
    a, m = Stub("anthropic"), Stub("mistral")
    assert router(a, m).generate("s", "u").provider == "anthropic" and m.calls == 0


def test_auto_without_anthropic_key_uses_mistral():
    a, m = Stub("anthropic", configured=False), Stub("mistral")
    assert router(a, m).generate("s", "u").provider == "mistral" and a.calls == 0


def test_auto_falls_back_on_provider_failure():
    a = Stub("anthropic", error=GenerationError("rate_limited", "x", 429))
    m = Stub("mistral")
    assert router(a, m).generate("s", "u").provider == "mistral"
    assert a.calls == 1 and m.calls == 1


def test_no_fallback_on_refusal_or_invalid_output():
    for cat in ("refusal", "invalid_output", "bad_request"):
        a = Stub("anthropic", error=GenerationError(cat, "x", 422))
        m = Stub("mistral")
        with pytest.raises(GenerationError) as exc:
            router(a, m).generate("s", "u")
        assert exc.value.category == cat and exc.value.provider == "anthropic" and m.calls == 0


def test_forced_provider_never_falls_back():
    a = Stub("anthropic", error=GenerationError("timeout", "x", 504))
    m = Stub("mistral")
    with pytest.raises(GenerationError):
        router(a, m, LLM_PROVIDER="anthropic").generate("s", "u")
    assert m.calls == 0
    assert router(Stub("anthropic"), m, LLM_PROVIDER="mistral").generate("s", "u").provider == "mistral"


def test_nothing_configured():
    with pytest.raises(GenerationError) as exc:
        router(Stub("anthropic", False), Stub("mistral", False)).generate("s", "u")
    assert exc.value.category == "not_configured"
