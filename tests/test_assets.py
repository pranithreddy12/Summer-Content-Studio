"""Tests for assets service/API - register, upload, sync, filters, archive, file serving."""

from pathlib import Path

import pytest

from app.config import clear_settings_cache

FIXTURES = Path(__file__).parent / "fixtures"


def _png_bytes() -> bytes:
    return (FIXTURES / "tiny.png").read_bytes()


def _png_file():
    return {"file": ("tiny.png", _png_bytes(), "image/png")}


# =========================================================================
# Registration (external_url / local_sync)
# =========================================================================

def test_register_external_url(client):
    resp = client.post("/api/v1/assets", json={
        "filename": "dropbox photo.jpg",
        "media_type": "image",
        "storage_type": "external_url",
        "source_ref": "https://www.dropbox.com/s/abc123/photo.jpg?dl=0",
        "description": "A housewarming party photo",
        "tags": ["lifestyle"],
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["storage_type"] == "external_url"
    assert data["preview_url"] is None  # external_url has no preview
    assert data["source_ref"].startswith("https://")
    assert data["tags"][0]["slug"] == "lifestyle"


def test_register_external_url_requires_https(client):
    resp = client.post("/api/v1/assets", json={
        "filename": "x.jpg",
        "media_type": "image",
        "storage_type": "external_url",
        "source_ref": "http://not-secure.example/x.jpg",
    })
    assert resp.status_code == 422


def test_register_local_sync_valid_relpath(client):
    resp = client.post("/api/v1/assets", json={
        "filename": "clip.mp4",
        "media_type": "video",
        "storage_type": "local_sync",
        "local_relpath": "Halloween/clip.mp4",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["local_relpath"] == "Halloween/clip.mp4"


def test_register_local_sync_bad_relpath(client):
    resp = client.post("/api/v1/assets", json={
        "filename": "clip.mp4",
        "media_type": "video",
        "storage_type": "local_sync",
        "local_relpath": "../escape.mp4",
    })
    assert resp.status_code == 422


def test_register_local_sync_duplicate_relpath(client):
    payload = {
        "filename": "clip.mp4",
        "media_type": "video",
        "storage_type": "local_sync",
        "local_relpath": "Halloween/clip.mp4",
    }
    resp = client.post("/api/v1/assets", json=payload)
    assert resp.status_code == 201
    resp = client.post("/api/v1/assets", json=payload)
    assert resp.status_code == 409


def test_register_upload_storage_rejected(client):
    resp = client.post("/api/v1/assets", json={
        "filename": "x.png",
        "media_type": "image",
        "storage_type": "upload",
    })
    assert resp.status_code == 422


# =========================================================================
# Upload
# =========================================================================

def test_upload_png_and_file_serving(client):
    resp = client.post("/api/v1/assets/upload", files=_png_file(), data={
        "description": "A tiny test pumpkin",
        "tags": "halloween, spooky",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    asset_id = data["id"]
    assert data["media_type"] == "image"
    assert data["storage_type"] == "upload"
    assert data["sha256"]
    assert data["size_bytes"] == len(_png_bytes())
    assert {t["slug"] for t in data["tags"]} == {"halloween", "spooky"}
    assert data["preview_url"] == f"/api/v1/assets/{asset_id}/file"

    # File endpoint returns the original bytes
    resp = client.get(f"/api/v1/assets/{asset_id}/file")
    assert resp.status_code == 200
    assert resp.content == _png_bytes()
    assert resp.headers["content-type"].startswith("image/png")


def test_upload_exe_rejected_415(client):
    fake = {"file": ("evil.exe", (FIXTURES / "fake.exe").read_bytes(), "application/octet-stream")}
    resp = client.post("/api/v1/assets/upload", files=fake)
    assert resp.status_code == 415


def test_upload_png_extension_non_png_bytes_415(client):
    fake = {"file": ("not_png.png", b"GIF8" + b"\x00" * 16, "image/png")}
    resp = client.post("/api/v1/assets/upload", files=fake)
    assert resp.status_code == 415


def test_upload_oversize_413(client, monkeypatch):
    monkeypatch.setenv("MAX_IMAGE_MB", "0")
    clear_settings_cache()
    try:
        resp = client.post("/api/v1/assets/upload", files=_png_file())
        assert resp.status_code == 413
    finally:
        monkeypatch.undo()
        clear_settings_cache()


def test_upload_duplicate_409(client):
    resp1 = client.post("/api/v1/assets/upload", files=_png_file())
    assert resp1.status_code == 201
    resp2 = client.post("/api/v1/assets/upload", files=_png_file())
    assert resp2.status_code == 409


def test_upload_missing_filename_422(client):
    resp = client.post("/api/v1/assets/upload", files={"file": ("", _png_bytes(), "image/png")})
    assert resp.status_code in (415, 422)


def test_upload_rejects_unsupported_extension(client):
    fake = {"file": ("notes.txt", b"hello world text content, not an image", "text/plain")}
    resp = client.post("/api/v1/assets/upload", files=fake)
    assert resp.status_code == 415


# =========================================================================
# PATCH / archive
# =========================================================================

def test_patch_description_and_tags(client):
    resp = client.post("/api/v1/assets/upload", files=_png_file(), data={"description": "before"})
    asset_id = resp.json()["id"]
    resp = client.patch(f"/api/v1/assets/{asset_id}", json={
        "description": "A glowing jack-o-lantern",
        "tags": ["halloween"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["description"] == "A glowing jack-o-lantern"
    assert data["tags"][0]["slug"] == "halloween"
    assert data["preview_url"] == f"/api/v1/assets/{asset_id}/file"
    assert data["usage_count"] == 0


def test_patch_unknown_field_422(client):
    resp = client.post("/api/v1/assets/upload", files=_png_file())
    asset_id = resp.json()["id"]
    resp = client.patch(f"/api/v1/assets/{asset_id}", json={"status": "published"})
    assert resp.status_code == 422


def test_archive_hides_from_default_list(client):
    resp = client.post("/api/v1/assets/upload", files=_png_file())
    asset_id = resp.json()["id"]

    # Archive
    resp = client.delete(f"/api/v1/assets/{asset_id}")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Default list hides it
    resp = client.get("/api/v1/assets")
    assert all(a["id"] != asset_id for a in resp.json()["items"])

    # include_inactive shows it
    resp = client.get("/api/v1/assets", params={"include_inactive": "true"})
    assert any(a["id"] == asset_id for a in resp.json()["items"])


def test_get_unknown_asset_404(client):
    resp = client.get("/api/v1/assets/999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_file_serving_unknown_asset_404(client):
    resp = client.get("/api/v1/assets/999/file")
    assert resp.status_code == 404


# =========================================================================
# Filters
# =========================================================================

@pytest.fixture
def two_assets(client):
    """One halloween png, one lifestyle gif (different bytes: dedupe by sha256)."""
    resp = client.post("/api/v1/assets/upload", files=_png_file(), data={
        "description": "pumpkin party", "tags": "halloween",
    })
    a1 = resp.json()["id"]
    gif = {"file": ("tiny.gif", (FIXTURES / "tiny.gif").read_bytes(), "image/gif")}
    resp = client.post("/api/v1/assets/upload", files=gif, data={
        "description": "beach day", "tags": "lifestyle",
    })
    a2 = resp.json()["id"]
    return a1, a2


def test_filter_by_tag(client, two_assets):
    a1, a2 = two_assets
    resp = client.get("/api/v1/assets", params={"tag": ["halloween"]})
    ids = [a["id"] for a in resp.json()["items"]]
    assert a1 in ids and a2 not in ids


def test_filter_by_q(client, two_assets):
    a1, a2 = two_assets
    resp = client.get("/api/v1/assets", params={"q": "beach"})
    ids = [a["id"] for a in resp.json()["items"]]
    assert a2 in ids and a1 not in ids


def test_filter_by_media_type(client, two_assets):
    resp = client.get("/api/v1/assets", params={"media_type": "video"})
    assert resp.json()["total"] == 0
    resp = client.get("/api/v1/assets", params={"media_type": "image"})
    assert resp.json()["total"] == 2


def test_filter_unused_needs_posts_relations(client, two_assets, fake_claude):
    """unused filter: regenerate via generate endpoint to link a post to asset a1."""
    a1, a2 = two_assets
    resp = client.post("/api/v1/content/generate", json={
        "profile_id": 1, "platform": "instagram", "content_type": "reel",
        "campaign": "halloween", "asset_id": a1,
    })
    assert resp.status_code == 201, resp.text
    resp = client.get("/api/v1/assets", params={"unused": "true"})
    ids = [a["id"] for a in resp.json()["items"]]
    assert a1 not in ids
    assert a2 in ids


def test_list_limit_and_offset(client, two_assets):
    resp = client.get("/api/v1/assets", params={"limit": 1, "offset": 1})
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


# =========================================================================
# Sync (local MEDIA_SYNC_DIR)
# =========================================================================

def test_sync_registers_files_and_auto_tags(client, tmp_path, monkeypatch):
    sync_dir = tmp_path / "sync2"
    (sync_dir / "Halloween").mkdir(parents=True)
    (sync_dir / "Halloween" / "shot1.png").write_bytes(_png_bytes())
    (sync_dir / "Halloween" / "notes.txt").write_bytes(b"not media, skipped")
    (sync_dir / ".hidden").write_bytes(_png_bytes())  # hidden files skipped
    (sync_dir / "empty.png").write_bytes(b"")  # zero-byte placeholder skipped

    monkeypatch.setenv("MEDIA_SYNC_DIR", str(sync_dir))
    clear_settings_cache()
    try:
        resp = client.post("/api/v1/assets/sync")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["added"] == 1
        assert body["skipped"] == 3

        resp = client.get("/api/v1/assets")
        items = resp.json()["items"]
        assert len(items) == 1
        asset = items[0]
        assert asset["local_relpath"] == "Halloween/shot1.png"
        assert asset["storage_type"] == "local_sync"
        assert {t["slug"] for t in asset["tags"]} == {"halloween"}

        # Second sync: everything already registered
        resp = client.post("/api/v1/assets/sync")
        assert resp.json() == {"added": 0, "skipped": 4}
    finally:
        monkeypatch.undo()
        clear_settings_cache()


def test_sync_not_configured_400(client, monkeypatch):
    monkeypatch.delenv("MEDIA_SYNC_DIR", raising=False)
    clear_settings_cache()
    try:
        resp = client.post("/api/v1/assets/sync")
        assert resp.status_code == 400
    finally:
        monkeypatch.undo()
        clear_settings_cache()


# =========================================================================
# Usage count is derived, not stored
# =========================================================================

def test_usage_count_derived_from_posts(client, fake_claude):
    resp = client.post("/api/v1/assets/upload", files=_png_file(), data={"description": "for a post"})
    a1 = resp.json()["id"]
    resp = client.post("/api/v1/content/generate", json={
        "profile_id": 1, "platform": "instagram", "content_type": "reel",
        "campaign": "halloween", "asset_id": a1,
    })
    assert resp.status_code == 201
    resp = client.get(f"/api/v1/assets/{a1}")
    assert resp.json()["usage_count"] == 1
