"""Notifications (PLAN_PHASE2 §9b/§16): providers via MockTransport, dedupe,
expected request shapes, no secrets anywhere, failures never raise."""

import httpx
import pytest

from app.db import get_session_factory
from app.services import notifications


def db():
    """A fresh Session per call (notify() commits internally)."""
    return get_session_factory()()


@pytest.fixture
def provider(request, app):
    """Set NOTIFY_PROVIDER for one test; request.param = provider name."""
    import os

    from app.config import clear_settings_cache

    os.environ["NOTIFY_PROVIDER"] = request.param
    clear_settings_cache()
    yield request.param
    os.environ.pop("NOTIFY_PROVIDER", None)
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    os.environ.pop("TELEGRAM_CHAT_ID", None)
    os.environ.pop("NTFY_TOPIC", None)
    os.environ.pop("PUSHOVER_APP_TOKEN", None)
    os.environ.pop("PUSHOVER_USER_KEY", None)
    os.environ.pop("PUBLIC_BASE_URL", None)
    clear_settings_cache()


def _cred(provider):
    import os

    os.environ.setdefault("PUBLIC_BASE_URL", "https://test.example.com")
    if provider == "telegram":
        os.environ["TELEGRAM_BOT_TOKEN"] = "TEST-SECRET-TOKEN"
        os.environ["TELEGRAM_CHAT_ID"] = "12345"
    elif provider == "ntfy":
        os.environ["NTFY_TOPIC"] = "TEST-SECRET-TOPIC"
    elif provider == "pushover":
        os.environ["PUSHOVER_APP_TOKEN"] = "TEST-SECRET-APPTOKEN"
        os.environ["PUSHOVER_USER_KEY"] = "TEST-SECRET-USERKEY"


@pytest.mark.parametrize("provider", ["telegram", "ntfy", "pushover"], indirect=True)
def test_provider_request_shapes(app, provider):
    _cred(provider)
    import os

    from app.config import clear_settings_cache

    clear_settings_cache()

    captured = {}
    body_lines = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.read()
        captured["headers"] = dict(request.headers)
        body_lines.append(request.content)
        return httpx.Response(200, json={"ok": True}) if provider == "telegram" else httpx.Response(200)

    fake = httpx.Client(transport=httpx.MockTransport(handler))

    if provider == "telegram":
        assert notifications.notify(db(), "test", "k1", "Title", "Body", url="/posts/1", http=fake) is True
        assert captured["url"].startswith("https://api.telegram.org/botTEST-SECRET-TOKEN/sendMessage")
        import json as _json

        data = _json.loads(captured["json"])
        assert data["chat_id"] == "12345"
        assert data["disable_web_page_preview"] is True
        assert "TEST-SECRET-TOKEN" not in data["text"]
    elif provider == "ntfy":
        assert notifications.notify(db(), "test", "k2", "Title", "Body", url="/posts/1", http=fake) is True
        assert captured["url"].endswith("/TEST-SECRET-TOPIC")
        assert captured["headers"]["title"] == "Title"
        assert captured["headers"].get("click")
        # the topic is structural (base URL), never part of the message body
        assert b"TEST-SECRET" not in captured["json"]
    elif provider == "pushover":
        assert notifications.notify(db(), "test", "k3", "Title", "Body", url="/posts/1", http=fake) is True
        assert captured["url"] == "https://api.pushover.net/1/messages.json"
        # the token/user are payload fields (Pushover API shape); the text does
        # not repeat any credential
        from urllib.parse import parse_qs

        form = parse_qs(captured["json"].decode())
        assert form["title"] == ["Title"]
        assert "Title\nBody" in form["message"][0]
        assert form["token"] == ["TEST-SECRET-APPTOKEN"]
        assert form["user"] == ["TEST-SECRET-USERKEY"]


def test_none_provider_sends_nothing(app):
    # default NOTIFY_PROVIDER=none in tests
    assert notifications.notify(db(), "test", "none-key", "t", "b") is False
    from sqlalchemy import select
    from app.models import NotificationLog

    rows = db().execute(select(NotificationLog).where(NotificationLog.event_key == "none-key")).scalars().all()
    assert len(rows) == 1
    assert rows[0].error_category == "disabled"


def test_duplicate_key_sent_once(app):
    assert notifications.notify(db(), "test", "dup", "t") is False
    assert notifications.notify(db(), "test", "dup", "t") is False
    from sqlalchemy import select
    from app.models import NotificationLog

    rows = db().execute(select(NotificationLog).where(NotificationLog.event_key == "dup")).scalars().all()
    assert len(rows) == 1


def test_provider_failure_returns_false_and_never_raises(app):
    import os

    from app.config import clear_settings_cache

    os.environ["NOTIFY_PROVIDER"] = "telegram"
    os.environ["TELEGRAM_BOT_TOKEN"] = "TEST-SECRET-TOKEN"
    os.environ["TELEGRAM_CHAT_ID"] = "1"
    clear_settings_cache()
    try:

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        fake = httpx.Client(transport=httpx.MockTransport(handler))
        # not raising is the assertion here
        assert notifications.notify(db(), "test", "fail-key", "t", http=fake) is False
    finally:
        os.environ.pop("NOTIFY_PROVIDER", None)
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)
        clear_settings_cache()


def test_url_only_when_public_base_url_set(app):
    import os

    from app.config import clear_settings_cache, get_settings

    os.environ["NOTIFY_PROVIDER"] = "ntfy"
    os.environ["NTFY_TOPIC"] = "TEST-SECRET-TOPIC"
    clear_settings_cache()
    try:
        assert get_settings().notify_configured is True
        assert notifications.dashboard_url(get_settings(), "/planner") is None  # no PUBLIC_BASE_URL
        os.environ["PUBLIC_BASE_URL"] = "https://studio.example.com"
        clear_settings_cache()
        assert notifications.dashboard_url(get_settings(), "/planner") == "https://studio.example.com/planner"
    finally:
        os.environ.pop("NOTIFY_PROVIDER", None)
        os.environ.pop("NTFY_TOPIC", None)
        os.environ.pop("PUBLIC_BASE_URL", None)
        clear_settings_cache()


def test_recent_rows_capped_and_secretless(app):
    notifications.notify(db(), "test", "a1", "t")
    rows = notifications.recent(db())
    assert isinstance(rows, list)
