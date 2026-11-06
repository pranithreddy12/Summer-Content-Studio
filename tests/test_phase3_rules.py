"""Phase 3 C1 rules (PLAN_PHASE3 §4-§6): Fanvue config, enums, the Fanvue-only
media rule at every layer, and the Fanvue caption rule. No network anywhere."""

import pytest
from pydantic import ValidationError

from app.config import Settings, clear_settings_cache, get_settings
from app.models import ContentPost, ContentType, MediaAsset, Platform, VALID_COMBOS
from app.services import AppError
from tests.test_planner import db_session


# ---------------------------------------------------------------- config

def test_default_scopes_never_include_write_creator():
    scopes = Settings().FANVUE_SCOPES.split()
    assert "write:creator" not in scopes
    assert {"read:chat", "write:chat", "write:post", "write:media", "read:insights"} <= set(scopes)


def test_write_creator_scope_is_refused():
    with pytest.raises(ValidationError, match="write:creator"):
        Settings(FANVUE_SCOPES="read:self write:creator")


def test_ppv_range_must_be_ordered():
    with pytest.raises(ValidationError, match="FANVUE_PPV_MIN_CENTS"):
        Settings(FANVUE_PPV_MIN_CENTS=6000, FANVUE_PPV_MAX_CENTS=5000)


@pytest.mark.parametrize("field,value", [("FANVUE_REPORT_WEEKDAY", 7), ("FANVUE_REPORT_HOUR", 24),
                                         ("FAN_DATA_RETENTION_DAYS", 0), ("FANVUE_INBOX_POLL_SECONDS", 0)])
def test_bad_fanvue_numbers_are_refused(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_blank_fanvue_credentials_mean_unset(env):
    env(FANVUE_CLIENT_ID="", FANVUE_CLIENT_SECRET="")
    s = get_settings()
    assert s.FANVUE_CLIENT_ID is None and s.FANVUE_CLIENT_SECRET is None


# ---------------------------------------------------------------- enums

def test_fanvue_platform_and_content_type():
    assert Platform.fanvue.value == "fanvue"
    assert VALID_COMBOS[Platform.fanvue] == {ContentType.fanvue_post}


def test_fanvue_post_rejects_other_content_types(client):
    r = client.post("/api/v1/posts", json={"profile_id": 1, "platform": "fanvue", "content_type": "reel",
                                            "campaign": "lifestyle", "caption": "hi"})
    assert r.status_code == 422


# ---------------------------------------------------------------- helpers

def _mark_fanvue_only(asset_id: int, value: bool = True) -> None:
    db = db_session()
    db.get(MediaAsset, asset_id).fanvue_only = value
    db.commit()
    db.close()


def _post(client, platform, content_type, asset_id=None, caption="caption"):
    body = {"profile_id": 1, "platform": platform, "content_type": content_type,
            "campaign": "lifestyle", "caption": caption, "hashtags": ["spooky", "summer"]}
    if asset_id is not None:
        body["asset_id"] = asset_id
    return client.post("/api/v1/posts", json=body)


# ---------------------------------------------------------------- H7: Fanvue-only media

@pytest.mark.parametrize("platform,content_type", [("instagram", "feed_post"), ("tiktok", "photo_post")])
def test_fanvue_only_asset_refused_on_create(client, halloween_asset, platform, content_type):
    _mark_fanvue_only(halloween_asset["id"])
    r = _post(client, platform, content_type, halloween_asset["id"])
    assert r.status_code == 409
    assert "fanvue_only_media" in r.text


def test_fanvue_only_asset_allowed_on_fanvue(client, halloween_asset):
    _mark_fanvue_only(halloween_asset["id"])
    r = _post(client, "fanvue", "fanvue_post", halloween_asset["id"])
    assert r.status_code == 201, r.text
    assert r.json()["platform"] == "fanvue"


def test_fanvue_only_asset_refused_when_attached_later(client, halloween_asset):
    post = _post(client, "instagram", "feed_post").json()
    _mark_fanvue_only(halloween_asset["id"])
    r = client.patch(f"/api/v1/posts/{post['id']}", json={"asset_id": halloween_asset["id"]})
    assert r.status_code == 409
    assert "fanvue_only_media" in r.text


def test_asset_flagged_after_attach_blocks_approve_and_publish(client, halloween_asset):
    """The flag can be set after the post exists: approve and publish still refuse."""
    from app.services.publishing import (PERSISTENT_PREFLIGHT, asset_is_publishable,
                                         check_publishable, preflight_reasons)

    post = _post(client, "instagram", "feed_post", halloween_asset["id"]).json()
    _mark_fanvue_only(halloween_asset["id"])

    r = client.post(f"/api/v1/posts/{post['id']}/approve")
    assert r.status_code == 409 and "fanvue_only_media" in r.text

    # Publish layer (e.g. a post approved before the flag was set)
    db = db_session()
    p = db.get(ContentPost, post["id"])
    p.status = "approved"
    db.commit()
    assert asset_is_publishable(p) == (False, "fanvue_only_media")
    with pytest.raises(AppError) as e:
        check_publishable(db, p, override_window=True)
    assert e.value.code == "fanvue_only_media"
    assert any(r.startswith("fanvue_only_media") for r in preflight_reasons(db, p))
    assert "fanvue_only_media" in PERSISTENT_PREFLIGHT   # fails visibly instead of retrying forever
    db.close()


def test_unflagged_asset_still_publishable_everywhere(client, halloween_asset):
    from app.services.publishing import asset_is_publishable

    post = _post(client, "fanvue", "fanvue_post", halloween_asset["id"]).json()
    db = db_session()
    assert asset_is_publishable(db.get(ContentPost, post["id"])) == (True, None)
    db.close()


def test_generate_refuses_fanvue_only_asset_for_instagram(client, fake_claude, halloween_asset):
    _mark_fanvue_only(halloween_asset["id"])
    r = client.post("/api/v1/content/generate", json={
        "profile_id": 1, "platform": "instagram", "content_type": "feed_post",
        "campaign": "lifestyle", "asset_id": halloween_asset["id"]})
    assert r.status_code == 409 and "fanvue_only_media" in r.text
    assert fake_claude.calls == 0   # refused before any AI call


def test_random_drafts_never_pick_fanvue_only_assets(client, halloween_asset):
    from app.services.random_draft import eligible_assets

    db = db_session()
    before = [a.id for a in eligible_assets(db, include_used=True)]
    db.close()
    assert halloween_asset["id"] in before
    _mark_fanvue_only(halloween_asset["id"])
    db = db_session()
    assert halloween_asset["id"] not in [a.id for a in eligible_assets(db, include_used=True)]
    db.close()


# ---------------------------------------------------------------- captions + generation

def test_fanvue_caption_has_no_hashtags():
    from app.services.publishing import render_caption

    fan = ContentPost(platform="fanvue", caption="Hello you", hashtags=["a", "b"])
    ig = ContentPost(platform="instagram", caption="Hello you", hashtags=["a", "b"])
    assert render_caption(fan) == "Hello you"
    assert render_caption(ig) == "Hello you\n\n#a #b"


def test_fanvue_generation_drops_hashtags_and_uses_fanvue_rule(client, fake_claude, halloween_asset):
    r = client.post("/api/v1/content/generate", json={
        "profile_id": 1, "platform": "fanvue", "content_type": "fanvue_post",
        "campaign": "lifestyle", "asset_id": halloween_asset["id"]})
    assert r.status_code == 201, r.text
    assert r.json()["hashtags"] == []
    assert "Fanvue post caption" in fake_claude.last_system + fake_claude.last_user


def test_fanvue_columns_default_to_null_and_false(client, halloween_asset):
    post = _post(client, "fanvue", "fanvue_post", halloween_asset["id"]).json()
    db = db_session()
    p = db.get(ContentPost, post["id"])
    assert p.fanvue_audience is None and p.fanvue_price_cents is None   # null = subscribers, free
    assert db.get(MediaAsset, halloween_asset["id"]).fanvue_only is False
    db.close()
    clear_settings_cache()


def test_real_network_is_blocked_in_tests():
    """The conftest guard behind PLAN_PHASE3 §17 "no live call in tests"."""
    import httpx

    with pytest.raises(RuntimeError, match="real network call"):
        httpx.Client().get("https://api.fanvue.com/v1/users/me")
