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

    clear_settings_cache()

    # Create directories
    (tmp_dir / "media").mkdir(parents=True, exist_ok=True)
    (tmp_dir / "sync").mkdir(parents=True, exist_ok=True)

    yield get_settings()

    clear_settings_cache()
    for key in ["DATABASE_URL", "MEDIA_UPLOAD_DIR", "MEDIA_SYNC_DIR", "ANTHROPIC_API_KEY", "MISTRAL_API_KEY", "LLM_PROVIDER"]:
        os.environ.pop(key, None)


@pytest.fixture
def app(settings):
    """Create test app with fresh database."""
    # Reset the in-memory rate limiter between tests
    from app.security import _rate_limit_store
    _rate_limit_store.clear()

    # Create engine bound to test database
    engine = make_engine(settings.DATABASE_URL)
    Base.metadata.create_all(engine)

    # Build app
    test_app = create_app()

    # Override engine/session for tests
    import app.db as db_module
    old_engine = getattr(db_module, "_engine", None)
    old_session = getattr(db_module, "_SessionLocal", None)
    db_module._engine = engine
    db_module._SessionLocal = None  # Will be recreated by get_session_factory

    # Seed database
    from app.db import session_scope
    with session_scope() as session:
        seed_all(session)

    yield test_app

    # Cleanup
    db_module._engine = old_engine
    db_module._SessionLocal = old_session
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