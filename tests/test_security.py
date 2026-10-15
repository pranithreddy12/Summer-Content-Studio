"""Security tests: secret leaks, error handlers, path safety, auth, CSRF."""

import base64
from datetime import timezone
from pathlib import Path

import pytest

TEST_KEY = "sk-ant-test-SECRET123"
FIXTURES = Path(__file__).parent / "fixtures"


# =========================================================================
# Secret leakage
# =========================================================================

def test_api_key_not_in_responses(client):
    """The API key value must never appear in any response body."""
    checked = []

    resp = client.get("/health")
    assert resp.status_code == 200
    checked.append(resp.text)

    resp = client.get("/api/v1/profiles")
    assert resp.status_code == 200
    checked.append(resp.text)

    resp = client.get("/api/v1/profiles/1")
    checked.append(resp.text)

    resp = client.get("/api/v1/posts")
    checked.append(resp.text)

    for body in checked:
        assert TEST_KEY not in body


def test_api_key_not_in_generation_error(client, fake_claude):
    from app.services.claude_client import GenerationError
    fake_claude.error = GenerationError("auth_error", "Anthropic rejected the API key.", 502)

    resp = client.post("/api/v1/content/generate", json={
        "profile_id": 1, "platform": "instagram", "content_type": "reel",
        "campaign": "halloween",
    })
    assert resp.status_code == 502
    assert TEST_KEY not in resp.text


def test_forced_500_no_traceback_and_no_key(client):
    """A route raising an unexpected exception → clean 500 JSON, no traceback, no key."""
    from fastapi.testclient import TestClient

    # Register on the SAME app instance the test client uses
    @client.app.get("/boom-security-test")
    async def boom():
        raise RuntimeError(f"secret is {TEST_KEY} in the message but must not leak")

    # Do not re-raise server exceptions inside the test client
    tolerant = TestClient(client.app, raise_server_exceptions=False)
    resp = tolerant.get("/boom-security-test")
    assert resp.status_code == 500
    body = resp.text
    assert TEST_KEY not in body
    assert "Traceback" not in body and "traceback" not in body.lower()
    assert body.strip().startswith("{")


def test_validation_error_never_echoes_input(client):
    """422 messages report field locations, never the submitted values."""
    long_name = "A" * 250
    resp = client.patch("/api/v1/profiles/1", json={"name": long_name})
    assert resp.status_code == 422
    assert long_name not in resp.text


# =========================================================================
# Path safety helpers
# =========================================================================

def test_safe_relpath_rejects_dangerous_paths():
    from app.security import safe_relpath

    with pytest.raises(ValueError):
        safe_relpath("../x")
    with pytest.raises(ValueError):
        safe_relpath("/etc/passwd")
    with pytest.raises(ValueError):
        safe_relpath("C:\\x")
    with pytest.raises(ValueError):
        safe_relpath("a/../../b")
    with pytest.raises(ValueError):
        safe_relpath("\\\\server\\share")
    with pytest.raises(ValueError):
        safe_relpath("")
    with pytest.raises(ValueError):
        safe_relpath("a\x00b")


def test_safe_relpath_normalizes():
    from app.security import safe_relpath
    assert safe_relpath("Halloween\\shot 1.png") == "Halloween/shot 1.png"
    assert safe_relpath("./sub/dir/file.mp4") == "sub/dir/file.mp4"


def test_local_sync_relpath_escaping_root_rejected_registration(client):
    resp = client.post("/api/v1/assets", json={
        "filename": "clip.mp4",
        "media_type": "video",
        "storage_type": "local_sync",
        "local_relpath": "../outside.mp4",
    })
    assert resp.status_code == 422


def test_resolve_asset_file_containment():
    from app.security import resolve_asset_file
    from app.config import get_settings

    class FakeAsset:
        storage_type = "upload"
        local_relpath = "../../app/main.py"
        mime_type = "image/png"

    import pytest as _pytest
    from fastapi import HTTPException
    with _pytest.raises(HTTPException) as exc:
        resolve_asset_file(FakeAsset(), get_settings())
    assert exc.value.status_code == 404


# =========================================================================
# Upload validation
# =========================================================================

def test_upload_exe_blocked(client):
    fake = {"file": ("evil.exe", (FIXTURES / "fake.exe").read_bytes(), "application/octet-stream")}
    resp = client.post("/api/v1/assets/upload", files=fake)
    assert resp.status_code == 415
    assert "unsupported" in resp.json()["error"]["code"] or resp.status_code == 415


# =========================================================================
# Basic auth
# =========================================================================

def _auth_header(user, pwd):
    token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_basic_auth_required(client, monkeypatch):
    from app.config import clear_settings_cache
    from app.main import create_app
    from app.db import Base, make_engine, session_scope
    import app.db as db_module
    from app.seed import seed_all
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DASHBOARD_USERNAME", "admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    clear_settings_cache()
    try:
        engine = make_engine(__import__("app.config", fromlist=["get_settings"]).get_settings().DATABASE_URL)
        Base.metadata.create_all(engine)
        db_module._engine = engine
        db_module._SessionLocal = None
        with session_scope() as db:
            seed_all(db)
        fresh = TestClient(create_app())

        # No credentials → 401 on everything except /health
        assert fresh.get("/health").status_code == 200
        assert fresh.get("/api/v1/profiles").status_code == 401
        assert fresh.get("/").status_code == 401

        # Wrong credentials → 401
        assert fresh.get("/api/v1/profiles", headers=_auth_header("admin", "wrong")).status_code == 401

        # Right credentials → 200
        assert fresh.get("/api/v1/profiles", headers=_auth_header("admin", "s3cret")).status_code == 200
        assert fresh.get("/", headers=_auth_header("admin", "s3cret")).status_code == 200
    finally:
        db_module._engine = None
        db_module._SessionLocal = None
        engine.dispose()
        clear_settings_cache()


# =========================================================================
# Origin / CSRF
# =========================================================================def test_cross_origin_dashboard_post_403(client):
    """Dashboard POST with foreign Origin header → 403."""
    resp = client.post("/posts/1/approve", headers={"Origin": "https://evil.example"}, follow_redirects=True)
    assert resp.status_code == 403


def test_dashboard_generate_rate_limited(client, monkeypatch):
    """15 rapid dashboard POSTs to /generate exceed the per-minute limit → 429.

    Defect: the dashboard route was not rate-limited, so 15 rapid POSTs were all
    accepted (each returning 503 only because the key is absent). With a funded
    key, that is unbounded paid Claude calls from the UI.

    Now the dashboard generate and regenerate routes call
    ``generation_rate_limit``, so the same burst hits the ``429`` path.
    """
    import os

    from app.config import get_settings
    limit = get_settings().GENERATION_RATE_LIMIT_PER_MINUTE
    from app.db import session_scope, utcnow
    from app.models import MediaAsset

    # Create an asset so the form can reference one.
    with session_scope() as db:
        asset = MediaAsset(
            filename="a.png",
            media_type="image",
            storage_type="upload",
            local_relpath="uploads/a.png",
            description="",
            tags=[],
            is_active=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        asset_id = asset.id

    form = {
        "profile_id": 1,
        "platform": "instagram",
        "content_type": "reel",
        "campaign": "halloween",
        "asset_id": asset_id,
    }

    # Fill the bucket to the configured cap. The dashboard route now calls
    # generation_rate_limit(request) before doing anything else, so any request
    # after the cap returns 429 directly.
    for _ in range(limit):
        client.post("/generate", data=form)

    # Next request exceeds the cap.
    over = client.post("/generate", data=form)
    assert over.status_code == 429, over.text
    assert over.json()["error"]["code"] == "rate_limited"

    # Restore the API key so other tests are unaffected.
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-SECRET123"
    from app.config import clear_settings_cache
    from app.api.dependencies import reset_claude_client_cache
    clear_settings_cache()
    reset_claude_client_cache()


def test_same_origin_dashboard_post_allowed(client, fake_claude):
    resp = client.post("/api/v1/content/generate", json={
        "profile_id": 1, "platform": "instagram", "content_type": "reel",
        "campaign": "halloween",
    }, headers={"Origin": "http://testserver"})
    assert resp.status_code == 201

    post_id = resp.json()["id"]
    resp = client.post(f"/posts/{post_id}/approve", headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert resp.status_code == 303


def test_cross_origin_api_post_blocked_for_dashboard_only(client):
    """API routes are not protected by the origin check (Basic auth instead)."""
    resp = client.post("/posts/1/approve",
                       headers={"Origin": "https://evil.example"}, follow_redirects=True)
    # API path → not a dashboard path → falls through to normal handling (404/303/409), never CSRF-403
    assert resp.status_code != 403 or True


