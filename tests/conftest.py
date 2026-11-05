"""Test configuration and fixtures."""

import json
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config import clear_settings_cache, get_settings
from app.db import Base, get_engine, make_engine, get_session_factory
from app.main import create_app
from app.seed import seed_all


# Credentials blanked for every test run (env beats .env in pydantic-settings)
ISOLATED_KEYS = [
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "NTFY_TOPIC", "PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY",
    "PUBLIC_BASE_URL", "TOKEN_ENCRYPTION_KEY", "MEDIA_URL_SECRET",
    "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET", "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET",
    "FANVUE_CLIENT_ID", "FANVUE_CLIENT_SECRET",
]


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Generator:
    """Configure settings with test environment variables."""
    tmp_dir = tmp_path_factory.mktemp("test_env")

    import os
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_dir}/test.db"
    os.environ["MEDIA_UPLOAD_DIR"] = str(tmp_dir / "media")
    os.environ["MEDIA_SYNC_DIR"] = str(tmp_dir / "sync")
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-SECRET123"
    # Never let a developer's real Mistral key (or provider override) leak into tests
    os.environ["MISTRAL_API_KEY"] = ""
    os.environ["LLM_PROVIDER"] = "auto"
    os.environ["APP_ENV"] = "development"
    os.environ["APP_TIMEZONE"] = "UTC"
    os.environ["DASHBOARD_USERNAME"] = ""
    os.environ["DASHBOARD_PASSWORD"] = ""
    # Phase 2: tests never post or push for real, whatever the developer's .env says
    os.environ["PUBLISHING_MODE"] = "off"
    os.environ["NOTIFY_PROVIDER"] = "none"
    os.environ["MIN_FREE_DISK_GB"] = "0"   # tests must not depend on the free space of the machine running them
    for secret in ISOLATED_KEYS:
        os.environ[secret] = ""

    clear_settings_cache()

    # Create directories
    (tmp_dir / "media").mkdir(parents=True, exist_ok=True)
    (tmp_dir / "sync").mkdir(parents=True, exist_ok=True)

    yield get_settings()

    clear_settings_cache()
    for key in ["DATABASE_URL", "MEDIA_UPLOAD_DIR", "MEDIA_SYNC_DIR", "ANTHROPIC_API_KEY", "MISTRAL_API_KEY", "LLM_PROVIDER",
                "PUBLISHING_MODE", "NOTIFY_PROVIDER", "MIN_FREE_DISK_GB", *ISOLATED_KEYS]:
        os.environ.pop(key, None)


@pytest.fixture
def app(settings):
    """Create test app with fresh database."""
    # Reset the in-memory rate limiter between tests
    from app.security import _rate_limit_store
    _rate_limit_store.clear()

    # Create engine bound to test database (NullPool: tests open many short-lived
    # sessions; a shared pool would exhaust with open-but-uncommitted sessions)
    from sqlalchemy.pool import NullPool

    engine = make_engine(settings.DATABASE_URL, poolclass=NullPool)
    Base.metadata.create_all(engine)

    # Build app
    test_app = create_app()

    # Override engine/session for tests
    import app.db as db_module
    old_engine = getattr(db_module, "_engine", None)
    old_session = getattr(db_module, "_SessionLocal", None)
    db_module._engine = engine
    db_module._SessionLocal = None  # Will be recreated by get_session_factory

    # Track every Session made through get_session_factory() and close them all
    # at teardown (tests create many sessions; leaked ones exhaust the pool).
    real_sessionmaker = db_module.get_session_factory()
    opened = []

    class _TrackingSessionMaker:
        def __call__(self, *args, **kwargs):
            s = real_sessionmaker(*args, **kwargs)
            opened.append(s)
            return s

    db_module._SessionLocal = _TrackingSessionMaker()

    # Seed database
    from app.db import session_scope
    with session_scope() as session:
        seed_all(session)

    yield test_app

    # Cleanup
    for s in opened:
        try:
            s.close()
        except Exception:
            pass
    db_module._SessionLocal = old_session
    db_module._engine = old_engine
    engine.dispose()
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(app) -> TestClient:
    """Create test client."""
    return TestClient(app)


# =========================================================================
# Fake Claude
# =========================================================================

class FakeClaude:
    """Fake Claude client for testing without real API calls."""

    def __init__(self):
        self.result = None
        self.error = None
        self.calls = 0
        self.settings = None

    @property
    def is_configured(self):
        return True

    def generate(self, system: str, user: str, images=None):
        self.calls += 1
        self.last_system, self.last_user, self.last_images = system, user, images or []
        if self.error:
            raise self.error
        if self.result is None:
            # Default valid result
            from app.schemas import GeneratedContent
            self.result = type("ClaudeResult", (), {
                "content": GeneratedContent(
                    content_format="test format",
                    hook="Test hook",
                    caption="Test caption for generated content",
                    hashtags=["#test", "#content", "#generated"],
                    call_to_action="Follow for more",
                    content_notes="Remember AI label",
                ),
                "model": "claude-opus-5-5",
                "input_tokens": 100,
                "output_tokens": 200,
                "request_id": "req_test_1",
            })()
        return self.result


@pytest.fixture
def fake_claude(app) -> FakeClaude:
    """Install FakeClaude via dependency override."""
    from app.api.dependencies import get_claude_client

    fake = FakeClaude()
    fake.settings = get_settings()
    app.dependency_overrides[get_claude_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_claude_client, None)


# =========================================================================
# Sample fixtures
# =========================================================================

@pytest.fixture
def halloween_asset(client) -> dict:
    """Upload a tiny PNG tagged 'halloween' with a description."""
    # Create a minimal PNG (1x1 pixel)
    import base64
    tiny_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )

    files = {"file": ("test.png", tiny_png, "image/png")}
    data = {
        "description": "A spooky Halloween pumpkin on a dark background",
        "tags": "halloween, spooky",
    }

    response = client.post("/api/v1/assets/upload", files=files, data=data)
    assert response.status_code == 201, f"Upload failed: {response.text}"
    return response.json()

# =========================================================================
# Phase 2 helpers
# =========================================================================

@pytest.fixture
def env():
    """Set env vars for one test (and clear the settings cache); restored afterwards.
    Request it BEFORE `client`/`app` when the value must be seen by create_app()."""
    import os

    from app.config import clear_settings_cache

    saved = {}

    def setter(**kw):
        for k, v in kw.items():
            saved.setdefault(k, os.environ.get(k))
            os.environ[k] = v
        clear_settings_cache()

    yield setter
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    clear_settings_cache()


@pytest.fixture
def phase2(env):
    """Everything a connected, publishing setup needs (fake values), mode still `off`."""
    from cryptography.fernet import Fernet

    env(TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), MEDIA_URL_SECRET="media-secret-for-tests",
        PUBLIC_BASE_URL="https://studio.example.com", INSTAGRAM_APP_ID="igapp",
        INSTAGRAM_APP_SECRET="ig-secret", TIKTOK_CLIENT_KEY="ttkey", TIKTOK_CLIENT_SECRET="tt-secret")
    return env
