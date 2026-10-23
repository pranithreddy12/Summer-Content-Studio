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


# =========================================================================
# Phase 2 (B2): planner + calendar pages, both_platforms pair
# =========================================================================

def _mp4_kb() -> bytes:
    """A >1 KB MP4-prefixed blob (passes magic-byte check; is a usable video)."""
    base = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00\x00\x00\x08free"
    return base + b"\x00" * 2048


def test_planner_page_renders(client):
    from app.db import get_session_factory
    from app.models import PostingRule

    db = get_session_factory()()
    db.add(PostingRule(model_profile_id=1, platform="instagram", is_active=False))
    db.commit()
    resp = client.get("/planner")
    assert resp.status_code == 200
    assert "Plan next 7 days" in resp.text
    assert "instagram" in resp.text


def test_planner_page_week_grid_with_data(client, fake_claude):
    from tests.test_planner import make_approved
    make_approved(caption="W", i=1)
    resp = client.get("/planner")
    assert resp.status_code == 200


def test_plan_next_7_days_button_flow(client, fake_claude):
    from app.db import get_session_factory
    from app.models import PostingRule

    db = get_session_factory()()
    db.add(PostingRule(model_profile_id=1, platform="instagram", posts_per_week=10,
                       min_per_day=1, max_per_day=2, window_start="08:00", window_end="21:00",
                       min_gap_minutes=180, timezone="Europe/London", is_active=True))
    db.commit()

    from tests.test_planner import make_approved
    for i in range(10):
        make_approved(caption="B", i=i)

    resp = client.post("/planner/plan", data={"platform": "instagram", "profile_id": "1"},
                       follow_redirects=False)
    assert resp.status_code == 303
    page = client.get(resp.headers["location"])
    assert "slots filled" in page.text
    # running it twice: nothing new scheduled
    resp = client.post("/planner/plan", data={"platform": "instagram", "profile_id": "1"},
                       follow_redirects=False)
    page = client.get(resp.headers["location"])
    assert "0 slots filled" in page.text


def test_calendar_page_renders_and_links(client, fake_claude):
    from tests.test_planner import make_approved
    pid = make_approved(caption="C", i=1)
    resp = client.get("/calendar")
    assert resp.status_code == 200
    resp = client.get(f"/posts/{pid}")  # calendar slot links to post detail
    assert resp.status_code == 200


def test_generate_random_both_platforms_creates_pair(client, fake_claude):
    """Videos produce an Instagram + TikTok pair sharing a group_key (PLAN §9a)."""
    files = {"file": ("clip.mp4", _mp4_kb(), "video/mp4")}
    upload = client.post("/api/v1/assets/upload", files=files, data={"description": "video"})
    assert upload.status_code == 201, upload.text

    resp = client.post("/generate/random", data={
        "platform": "instagram",
        "campaign": "auto",
        "count": "1",
        "media_type": "video",
        "both_platforms": "true",
        "batch_key": "pair-test-1",
    }, follow_redirects=False)
    assert resp.status_code == 303, resp.headers.get("location")

    posts = client.get("/api/v1/posts").json()["items"]
    pair = [p for p in posts if p["platform"] in ("instagram", "tiktok")]
    assert len(pair) == 2
    platforms = sorted(p["platform"] for p in pair)
    assert platforms == ["instagram", "tiktok"]
    keys = {p.get("group_key") for p in pair}
    assert len(keys) == 1 and next(iter(keys))  # same, non-empty group_key
    assert all(p["status"] == "draft" for p in pair)  # drafts only, never approved


# ---- Phase 2 B5: accounts page + publish panel ------------------------------

def test_accounts_page_lists_platforms_and_names_missing_env_only(client):
    page = client.get("/accounts")
    assert page.status_code == 200
    assert "Instagram" in page.text and "TikTok" in page.text
    assert "INSTAGRAM_APP_ID" in page.text and "TOKEN_ENCRYPTION_KEY" in page.text   # names, never values
    assert "/oauth/instagram/start" not in page.text                                    # cannot connect yet


def test_accounts_page_offers_connect_when_configured(phase2, client):
    page = client.get("/accounts").text
    assert 'href="/oauth/instagram/start?profile_id=1"' in page and 'href="/oauth/tiktok/start?profile_id=1"' in page


def test_accounts_notify_test_without_channel_is_a_flash_error(client):
    r = client.post("/accounts/notify-test", follow_redirects=False)
    assert r.status_code == 303 and "kind=error" in r.headers["location"]


def test_post_detail_shows_publish_panel_per_mode(phase2, client):
    from datetime import datetime, timezone
    from tests.test_publish_live import make_post

    pid = make_post(client)
    assert 'id="publish-panel"' not in client.get(f"/posts/{pid}").text            # mode off: Phase 1 / manual only
    phase2(PUBLISHING_MODE="dry_run")
    page = client.get(f"/posts/{pid}").text
    assert 'id="publish-panel"' in page and "Dry run now" in page and "account_not_connected" not in page
    phase2(PUBLISHING_MODE="live")
    page = client.get(f"/posts/{pid}").text
    assert "Publish now" in page and "account_not_connected" in page and "disabled" in page   # blocked until connected


def test_dashboard_publish_in_dry_run_flashes_and_records_attempt(phase2, client, monkeypatch):
    from datetime import datetime, timezone
    from tests.test_publish_live import NOON, make_post
    import app.services.publishing as p

    phase2(PUBLISHING_MODE="dry_run")
    pid = make_post(client)
    monkeypatch.setattr(p, "check_publishable", lambda *a, **k: None)
    r = client.post(f"/posts/{pid}/publish", follow_redirects=False)
    assert r.status_code == 303 and "Dry+run+recorded" in r.headers["location"].replace("%20", "+")
    assert "would publish" in client.get(f"/posts/{pid}").text


def test_dashboard_publish_when_off_is_a_flash_error(client):
    from tests.test_publish_live import make_post

    pid = make_post(client)
    r = client.post(f"/posts/{pid}/publish", follow_redirects=False)
    assert r.status_code == 303 and "kind=error" in r.headers["location"]


def test_dashboard_retry_only_for_failed(client):
    from tests.test_publish_live import make_post

    pid = make_post(client)
    r = client.post(f"/posts/{pid}/retry", follow_redirects=False)
    assert r.status_code == 303 and "kind=error" in r.headers["location"]
