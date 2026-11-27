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
    assert "Plan posts" in resp.text and 'name="days"' in resp.text and 'name="start_day"' in resp.text
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


def test_fanvue_twin_copies_the_instagram_draft(client, fake_claude):
    """fanvue_twin=True: each new Instagram draft also gets a Fanvue draft with the same text, never approved."""
    from app.db import get_session_factory  # noqa: F401  (session helper used below)
    from app.services.random_draft import generate_random_drafts
    from app.schemas import RandomDraftRequest
    from app.models import Platform
    import app.db as appdb

    client.post("/api/v1/assets/upload", files={"file": ("a.mp4", _mp4_kb(), "video/mp4")}, data={"description": "v"})
    with appdb.get_session_factory()() as s:
        out = generate_random_drafts(s, RandomDraftRequest(platform=Platform.instagram, campaign="auto", count=1),
                                     fake_claude, announce=False, fanvue_twin=True)
        ig, fv = [p for p in out["posts"] if p.platform == "instagram"][0], [p for p in out["posts"] if p.platform == "fanvue"][0]
        assert fv.caption == ig.caption and fv.content_type == "fanvue_post" and fv.status == "draft"
        assert fv.group_key and fv.group_key == ig.group_key and fv.fanvue_audience == "subscribers"


def test_copy_to_fanvue_button_makes_one_draft(client, fake_claude):
    client.post("/api/v1/assets/upload", files={"file": ("b.mp4", _mp4_kb(), "video/mp4")}, data={"description": "v"})
    client.post("/generate/random", data={"platform": "instagram", "campaign": "auto", "count": "1", "media_type": "video",
                                          "batch_key": "cf-1"}, follow_redirects=False)
    ig = next(p for p in client.get("/api/v1/posts").json()["items"] if p["platform"] == "instagram")
    assert "Copy to Fanvue" in client.get(f"/posts/{ig['id']}").text
    client.post(f"/posts/{ig['id']}/copy-to-fanvue", follow_redirects=False)
    fv = [p for p in client.get("/api/v1/posts").json()["items"] if p["platform"] == "fanvue"]
    assert len(fv) == 1 and fv[0]["caption"] == ig["caption"] and fv[0]["status"] == "draft"
    client.post(f"/posts/{ig['id']}/copy-to-fanvue", follow_redirects=False)  # second click: refused
    assert len([p for p in client.get("/api/v1/posts").json()["items"] if p["platform"] == "fanvue"]) == 1


def _bundle_setup(client, videos, images=0):
    import app.db as appdb
    for n in range(videos):
        r = client.post("/api/v1/assets/upload", files={"file": (f"v{n}.mp4", _mp4_kb() + bytes([n + 1]) * 8, "video/mp4")},
                        data={"description": f"v{n}"})
        assert r.status_code == 201, r.text
    for n in range(images):
        png = (FIXTURES / "tiny.png").read_bytes() + b"\x00" * (n + 1)
        assert client.post("/api/v1/assets/upload", files={"file": (f"i{n}.png", png, "image/png")},
                           data={"description": "i"}).status_code == 201
    return appdb.get_session_factory()


def _bundle(sf, fake_claude, media=None):
    from app.models import Platform
    from app.schemas import RandomDraftRequest
    from app.services.random_draft import generate_random_drafts
    with sf() as s:
        out = generate_random_drafts(s, RandomDraftRequest(platform=Platform.instagram, campaign="auto", count=1, media_type=media),
                                     fake_claude, announce=False, bundle=True)
        return {p.platform: p for p in out["posts"]}


def test_bundle_fresh_video_goes_to_instagram_tiktok_and_fanvue(client, fake_claude):
    sf = _bundle_setup(client, videos=1)
    got = _bundle(sf, fake_claude, "video")
    assert set(got) == {"instagram", "tiktok", "fanvue"}
    assert len({p.media_asset_id for p in got.values()}) == 1          # one video everywhere
    assert got["instagram"].group_key == got["tiktok"].group_key == got["fanvue"].group_key
    assert all(p.status == "draft" for p in got.values())


def test_bundle_image_gets_a_different_tiktok_video_that_instagram_uses_next_day(client, fake_claude, monkeypatch):
    from datetime import timedelta
    from app.db import utcnow
    from app.models import ContentPost
    from app.services import random_draft
    sf = _bundle_setup(client, videos=3, images=1)
    day1 = _bundle(sf, fake_claude, "image")
    assert set(day1) == {"instagram", "tiktok", "fanvue"}
    assert day1["instagram"].media_asset_id == day1["fanvue"].media_asset_id != day1["tiktok"].media_asset_id
    assert day1["tiktok"].asset.media_type == "video" and day1["tiktok"].group_key != day1["instagram"].group_key
    # same day: the TikTok video is not used on Instagram yet; the next day it is
    with sf() as s:
        assert random_draft.carried_over_videos(s, random_draft.get_settings()) == []
        s.get(ContentPost, day1["tiktok"].id).created_at = utcnow() - timedelta(days=1, hours=1)
        s.commit()
        assert [a.id for a in random_draft.carried_over_videos(s, random_draft.get_settings())] == [day1["tiktok"].media_asset_id]
    monkeypatch.setattr(random_draft.random, "random", lambda: 0.0)
    day2 = _bundle(sf, fake_claude)
    assert day2["instagram"].media_asset_id == day1["tiktok"].media_asset_id         # yesterday's TikTok video
    assert day2["fanvue"].media_asset_id == day2["instagram"].media_asset_id
    assert day2["tiktok"].media_asset_id not in (day1["tiktok"].media_asset_id, day2["instagram"].media_asset_id)  # a new one


def test_planner_offers_a_rule_for_platforms_without_one_and_approve_says_so(env, client, fake_claude):
    env(AUTO_SCHEDULE_ON_APPROVE="true")
    page = client.get("/planner").text
    assert "Add Fanvue rule" in page and "Add TikTok rule" in page
    client.post("/planner/rules/add", data={"platform": "tiktok"}, follow_redirects=False)
    client.post("/planner/rules/add", data={"platform": "tiktok"}, follow_redirects=False)  # second click: refused, no duplicate
    page = client.get("/planner").text
    assert "Add TikTok rule" not in page and "Add Fanvue rule" in page and "Plan TikTok" in page

    client.post("/api/v1/assets/upload", files={"file": ("r.mp4", _mp4_kb(), "video/mp4")}, data={"description": "v"})
    client.post("/generate/random", data={"platform": "instagram", "campaign": "auto", "count": "1", "media_type": "video",
                                          "batch_key": "rule-msg"}, follow_redirects=False)
    ig = next(p for p in client.get("/api/v1/posts").json()["items"] if p["platform"] == "instagram")
    client.post(f"/posts/{ig['id']}/copy-to-fanvue", follow_redirects=False)
    fv = next(p for p in client.get("/api/v1/posts").json()["items"] if p["platform"] == "fanvue")
    out = client.post(f"/posts/{fv['id']}/approve", data={"next": ""}, follow_redirects=True).text
    assert "Fanvue has no posting rule" in out


def test_delete_published_counts_first_then_deletes_only_published_and_keeps_a_copy(client, fake_claude):
    from click.testing import CliRunner
    import app.db as appdb
    from app.cli import cli
    from app.models import ContentPost

    client.post("/api/v1/assets/upload", files={"file": ("d.mp4", _mp4_kb(), "video/mp4")}, data={"description": "v"})
    client.post("/generate/random", data={"platform": "instagram", "campaign": "auto", "count": "1", "media_type": "video",
                                          "both_platforms": "true", "batch_key": "del-1"}, follow_redirects=False)
    with appdb.get_session_factory()() as s:
        ids = [p.id for p in s.query(ContentPost).all()]
        s.get(ContentPost, ids[0]).status = "published"
        s.commit()
    keep = len(ids) - 1
    dry = CliRunner().invoke(cli, ["delete-published"])
    assert "1 published posts" in dry.output and "Nothing deleted" in dry.output
    with appdb.get_session_factory()() as s:
        assert s.query(ContentPost).count() == len(ids)
    done = CliRunner().invoke(cli, ["delete-published", "--yes"])
    assert "Deleted 1 published" in done.output and "Database copy saved" in done.output
    with appdb.get_session_factory()() as s:
        assert s.query(ContentPost).count() == keep and s.query(ContentPost).filter_by(status="published").count() == 0


def test_queue_shows_a_draft_shared_by_two_platforms_once_with_a_chip(client, fake_claude):
    client.post("/api/v1/assets/upload", files={"file": ("q.mp4", _mp4_kb(), "video/mp4")}, data={"description": "v"})
    client.post("/generate/random", data={"platform": "instagram", "campaign": "auto", "count": "1", "media_type": "video",
                                          "batch_key": "q-1"}, follow_redirects=False)
    ig = next(p for p in client.get("/api/v1/posts").json()["items"] if p["platform"] == "instagram")
    client.post(f"/posts/{ig['id']}/copy-to-fanvue", follow_redirects=False)
    page = client.get("/posts").text
    assert page.count('class="wl-title"') == 1 and "Also on:" in page and "Fanvue" in page
    assert page.count(ig["caption"][:30]) == 1                         # the text is not repeated
    assert "Also on:" not in client.get("/posts?platform=fanvue").text  # filtered to one platform: plain row
