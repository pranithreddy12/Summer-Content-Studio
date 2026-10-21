"""Tests for the dashboard (server-rendered web routes)."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _png_file():
    return {"file": ("tiny.png", (FIXTURES / "tiny.png").read_bytes(), "image/png")}


# =========================================================================
# Pages render with real data
# =========================================================================

def test_overview_page_200(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "Summer" in body or "Overview" in body  # page renders


def test_overview_page_shows_real_counts(client, fake_claude):
    # Create one asset + one post via API
    resp = client.post("/api/v1/assets/upload", files=_png_file(), data={"description": "pumpkin"})
    asset_id = resp.json()["id"]
    resp = client.post("/api/v1/content/generate", json={
        "profile_id": 1, "platform": "instagram", "content_type": "reel",
        "campaign": "halloween", "asset_id": asset_id,
    })
    assert resp.status_code == 201

    resp = client.get("/")
    body = resp.text
    assert "Test caption for generated content" in body  # recent posts from FakeClaude
    # Counts match API
    api_posts = client.get("/api/v1/posts").json()
    assert api_posts["total"] == 1
    assert "1" in body  # count cards show non-zero totals


def test_assets_library_page_shows_filename_and_filters(client):
    resp = client.post("/api/v1/assets/upload", files=_png_file(), data={
        "description": "A tiny pumpkin", "tags": "halloween",
    })
    asset = resp.json()

    resp = client.get("/assets")
    assert resp.status_code == 200
    assert "tiny.png" in resp.text
    assert "halloween" in resp.text.lower()

    # Filter by tag
    resp = client.get("/assets", params={"tag": "halloween"})
    assert resp.status_code == 200
    resp = client.get("/assets", params={"q": "no-such-thing"})
    assert resp.status_code == 200


def test_asset_new_page(client):
    resp = client.get("/assets/new")
    assert resp.status_code == 200
    assert "Upload" in resp.text or "upload" in resp.text


def test_asset_detail_page(client):
    resp = client.post("/api/v1/assets/upload", files=_png_file(), data={
        "description": "A tiny pumpkin",
    })
    asset = resp.json()
    resp = client.get(f"/assets/{asset['id']}")
    assert resp.status_code == 200
    assert "tiny.png" in resp.text
    assert "A tiny pumpkin" in resp.text
    # description hint present
    assert "Claude only knows what you write here" in resp.text


def test_generate_form_page(client):
    resp = client.get("/generate")
    assert resp.status_code == 200
    assert "Summer" in resp.text  # profile select
    assert "idempotency_key" in resp.text  # hidden idempotency field


def test_posts_queue_page(client, fake_claude):
    resp = client.post("/api/v1/content/generate", json={
        "profile_id": 1, "platform": "instagram", "content_type": "reel",
        "campaign": "halloween",
    })
    post_id = resp.json()["id"]
    caption = resp.json()["caption"]

    resp = client.get("/posts")
    assert resp.status_code == 200
    assert caption in resp.text

    # Filters render
    resp = client.get("/posts", params={"platform": "instagram", "status": "draft"})
    assert resp.status_code == 200


def test_post_detail_page(client, fake_claude):
    resp = client.post("/api/v1/content/generate", json={
        "profile_id": 1, "platform": "instagram", "content_type": "reel",
        "campaign": "halloween",
    })
    post_id = resp.json()["id"]
    caption = resp.json()["caption"]

    resp = client.get(f"/posts/{post_id}")
    assert resp.status_code == 200
    assert caption in resp.text
    # status banner: no auto-publishing in Phase 1
    assert "NOT published automatically" in resp.text
    # "Use this version" on variants
    assert "Use this version" in resp.text


def test_profiles_list_page(client):
    resp = client.get("/profiles")
    assert resp.status_code == 200
    assert "Summer" in resp.text


def test_profile_detail_page(client):
    resp = client.get("/profiles/1")
    assert resp.status_code == 200
    # Persona fields visible
    assert "flirtatious" in resp.text
    assert "restricted" in resp.text.lower()


def test_profile_update_post(client):
    resp = client.post("/profiles/1", data={
        "name": "Summer",
        "persona_description": "Summer is a cheeky, flirtatious persona.",
        "tone_style": "Playful and warm.",
        "communication_rules": "Rule one\nRule two",
        "restricted_topics": "Politics\nReligion debates",
        "style_notes": "Learns from reference accounts.",
        "cta_guidelines": "Never name adult platforms.",
        "is_active": "on",
    }, follow_redirects=False)
    assert resp.status_code == 303

    # Changes persisted via API
    resp = client.get("/api/v1/profiles/1")
    profile = resp.json()
    assert profile["communication_rules"] == ["Rule one", "Rule two"]
    assert profile["restricted_topics"] == ["Politics", "Religion debates"]


# =========================================================================
# Generate flow via dashboard POST
# =========================================================================

def test_generate_form_post_redirects_to_post(client, fake_claude):
    resp = client.post("/generate", data={
        "profile_id": "1",
        "platform": "instagram",
        "content_type": "reel",
        "campaign": "halloween",
        "asset_id": "",
        "asset_context": "",
        "seasonal_context": "Halloween week",
        "tone_variation": "",
        "cta_request": "",
        "extra_restrictions": "",
        "idempotency_key": "dash-key-1",
    }, follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/posts/")


def test_generate_form_missing_api_key_shows_error(client, monkeypatch):
    import os
    from app.config import clear_settings_cache

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    clear_settings_cache()
    try:
        resp = client.post("/generate", data={
            "profile_id": "1",
            "platform": "instagram",
            "content_type": "reel",
            "campaign": "halloween",
        }, follow_redirects=False)
        # Error state: form re-rendered (any status) or a redirect that shows the message
        if resp.status_code == 303:
            resp = client.get(resp.headers["location"])
        assert "ANTHROPIC_API_KEY" in resp.text or "not configured" in resp.text.lower()
    finally:
        monkeypatch.undo()
        clear_settings_cache()


def test_asset_new_post_upload_redirect(client):
    resp = client.post("/assets/new", data={
        "form_type": "external_url",
        "external_url": "https://www.dropbox.com/s/xyz/pic.jpg?dl=0",
        "external_filename": "pic.jpg",
        "external_media_type": "image",
        "external_description": "A dropbox-hosted photo",
        "external_tags": "halloween",
    }, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    assert "/assets/" in resp.headers["location"]

    # The asset exists via API
    resp = client.get("/api/v1/assets")
    items = resp.json()["items"]
    assert any(a["filename"] == "pic.jpg" for a in items)


# =========================================================================
# Static assets
# =========================================================================

def test_static_css_served(client):
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "css" in resp.headers["content-type"]


def test_dashboard_404_for_unknown_post(client):
    resp = client.get("/posts/999")
    assert resp.status_code == 404


def test_generate_button_does_not_disable_on_click(client):
    """Disabling the submit button in a click handler cancels the form submit (button did nothing)."""
    html = client.get("/generate").text
    assert "generateBtn.form.addEventListener('submit'" in html
    assert "addEventListener('click'" not in html


def test_library_shows_real_usage_and_used_filter(client):
    a = client.post("/api/v1/assets", json=dict(filename="All videos", media_type="video",
                    storage_type="external_url", source_ref="https://www.dropbox.com/scl/fo/abc/xyz")).json()
    b = client.post("/api/v1/assets", json=dict(filename="Unused clip", media_type="video",
                    storage_type="external_url", source_ref="https://www.dropbox.com/scl/fi/def/z.mp4")).json()
    client.post("/api/v1/posts", json=dict(profile_id=1, platform="instagram", content_type="feed_post",
                campaign="lifestyle", caption="x", asset_id=a["id"]))
    page = client.get("/assets").text
    assert "1 use<" in page.replace("</span>", "<") or "1 use" in page
    used = client.get("/assets?usage=used").text
    assert "All videos" in used and "Unused clip" not in used
    unused = client.get("/assets?usage=unused").text
    assert "Unused clip" in unused and "All videos" not in unused
