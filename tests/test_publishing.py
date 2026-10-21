"""Publishing service B1 slice (PLAN_PHASE2 §8): pre-flight check_publishable,
window enforcement and mark_posted. The full publish pipeline (B5) extends this
file - mode/dry-run/live cases live here too.
"""

import base64
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import AppError
from app.services.publishing import check_publishable, render_caption
from app.services.posts import mark_posted, approve_post
from app.models import PostingRule, PostStatus

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def aware(*args):
    return datetime(*args, tzinfo=timezone.utc)


def default_rule():
    return PostingRule(model_profile_id=1, platform="instagram", posts_per_week=10,
                       min_per_day=1, max_per_day=2, window_start="08:00", window_end="21:00",
                       min_gap_minutes=180, timezone="Europe/London", is_active=True)


def db_session():
    from app.db import get_session_factory

    return get_session_factory()()


def _approved_post(client, **overrides):
    """Create + approve an instagram feed post with an uploaded PNG asset."""
    assets = client.post(
        "/api/v1/assets/upload",
        files={"file": ("a.png", TINY_PNG, "image/png")},
        data={"description": "d"},
    ).json()
    body = {
        "profile_id": 1,
        "platform": "instagram",
        "content_type": "feed_post",
        "campaign": "lifestyle",
        "caption": "Caption",
        "hashtags": ["summer"],
        "asset_id": assets["id"],
    }
    body.update(overrides)
    resp = client.post("/api/v1/posts", json=body)
    assert resp.status_code == 201, resp.text
    post_id = resp.json()["id"]
    approve_post(db_session(), post_id)
    return post_id


@pytest.fixture
def active_rule(app):
    db = db_session()
    rule = default_rule()
    db.add(rule)
    db.commit()
    return rule


def _post(post_id):
    from app.services.posts import get_post

    return get_post(db_session(), post_id)


# --- mark_posted ---

def test_mark_posted_from_scheduled(app, client, approved_post_fixture, active_rule):
    db = db_session()
    post = _post(approved_post_fixture)
    post.status = PostStatus.scheduled.value
    db.commit()

    result = mark_posted(db_session(), approved_post_fixture, external_url="https://instagram.com/p/xyz/")
    assert result.status == PostStatus.published.value
    assert result.published_via == "manual"
    assert result.published_at is not None
    assert result.external_url == "https://instagram.com/p/xyz/"


def test_mark_posted_from_approved(app, client, approved_post_fixture, active_rule):
    result = mark_posted(db_session(), approved_post_fixture)
    assert result.status == PostStatus.published.value
    assert result.published_via == "manual"
    assert result.published_at is not None


def test_mark_posted_rejects_draft(app, client, active_rule):
    from app.schemas import PostCreate
    from app.models import Platform, ContentType
    from app.services.posts import create_post, get_post

    db = db_session()
    created = create_post(db, PostCreate(
        profile_id=1, platform=Platform.instagram, content_type=ContentType.feed_post,
        campaign="lifestyle", caption="caption"))
    with pytest.raises(AppError) as err:
        mark_posted(db, created.id)
    assert err.value.status == 409
    assert get_post(db, created.id).status == PostStatus.draft.value


def test_mark_posted_rejects_non_https_url(app, client, approved_post_fixture, active_rule):
    with pytest.raises(AppError) as err:
        mark_posted(db_session(), approved_post_fixture, external_url="http://insecure.example/p/1")
    assert err.value.status == 422


# --- check_publishable / preflight ---

def test_preflight_pass(app, client, approved_post_fixture, active_rule):
    # 12:00 BST on 21 Oct 2026 = 11:00 UTC, inside 08:00-21:00
    check_publishable(db_session(), _post(approved_post_fixture), now=aware(2026, 10, 21, 11, 0))


def test_draft_cannot_publish(app, client, active_rule):
    from app.schemas import PostCreate
    from app.models import Platform, ContentType
    from app.services.posts import create_post

    created = create_post(db_session(), PostCreate(
        profile_id=1, platform=Platform.instagram, content_type=ContentType.feed_post,
        campaign="lifestyle", caption="caption"))
    with pytest.raises(AppError) as err:
        check_publishable(db_session(), _post(created.id), now=aware(2026, 10, 21, 11, 0))
    assert err.value.status == 409


def test_outside_window_boundary(app, client, approved_post_fixture, active_rule):
    # 26 Oct 2026 is GMT (UTC+0): window = 08:00-21:00 UTC, end inclusive
    post = _post(approved_post_fixture)
    with pytest.raises(AppError) as err:
        check_publishable(db_session(), post, now=aware(2026, 10, 26, 21, 0, 1))
    assert err.value.code == "outside_posting_window"
    check_publishable(db_session(), post, now=aware(2026, 10, 26, 21, 0, 0))


def test_external_url_asset_not_publishable(app, client, active_rule):
    from app.schemas import AssetCreate, PostCreate
    from app.models import Platform, ContentType
    from app.services.assets import register_asset
    from app.services.posts import create_post

    db = db_session()
    asset = register_asset(db, AssetCreate(
        filename="link.png", media_type="image", storage_type="external_url",
        source_ref="https://www.dropbox.com/s/x/img.png", description="ext"))
    created = create_post(db, PostCreate(
        profile_id=1, platform=Platform.instagram, content_type=ContentType.feed_post,
        campaign="lifestyle", caption="caption", asset_id=asset.id))
    post = _post(created.id)
    post.status = PostStatus.approved.value
    db.commit()
    with pytest.raises(AppError) as err:
        check_publishable(db_session(), post, now=aware(2026, 10, 21, 11, 0))
    assert err.value.code == "media_not_publishable"


def test_caption_too_long(app, client, approved_post_fixture, active_rule):
    db = db_session()
    post = _post(approved_post_fixture)
    post.caption = "x" * 2201
    db.commit()
    with pytest.raises(AppError) as err:
        check_publishable(db_session(), post, now=aware(2026, 10, 21, 11, 0))
    assert err.value.code == "caption_too_long"


# --- dashboard manual-post panel ---

def test_dashboard_manual_post_panel(app, client, approved_post_fixture, active_rule):
    resp = client.get(f"/posts/{approved_post_fixture}")
    assert resp.status_code == 200
    html = resp.text
    assert "Mark as posted" in html
    assert "Copy caption" in html
    assert "Download media" in html
    assert "Caption" in html  # full caption rendered for copy


def test_dashboard_mark_posted_route(app, client, approved_post_fixture, active_rule):
    resp = client.post(
        f"/posts/{approved_post_fixture}/posted",
        data={"external_url": "https://instagram.com/p/dash/"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    post = _post(approved_post_fixture)
    assert post.status == PostStatus.published.value
    assert post.published_via == "manual"


def test_render_caption():
    assert render_caption(SimpleNamespace(caption="Hello", hashtags=["a", "b"])) == "Hello\n\n#a #b"
    assert render_caption(SimpleNamespace(caption="Hello", hashtags=[])) == "Hello"


@pytest.fixture
def approved_post_fixture(client):
    return _approved_post(client)
