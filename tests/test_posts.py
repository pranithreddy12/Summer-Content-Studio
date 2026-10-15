"""Tests for posts service/API - CRUD, transitions, filters, variants, persistence."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _aware(days: float = 1.0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


def _paste(days: float = 1.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _create_post(client, **overrides):
    payload = {
        "profile_id": 1,
        "platform": "instagram",
        "content_type": "reel",
        "campaign": "halloween",
        "hook": "Trick or treat?",
        "caption": "Spooky season is here. #pumpkinvibes",
        "hashtags": ["halloween", "pumpkinvibes"],
        "call_to_action": "Link in bio",
        "content_notes": "Remember AI label",
    }
    payload.update(overrides)
    return client.post("/api/v1/posts", json=payload)


def _create_via_generate(client, fake_claude=None, **overrides):
    payload = {
        "profile_id": 1, "platform": "instagram", "content_type": "reel",
        "campaign": "halloween",
    }
    payload.update(overrides)
    return client.post("/api/v1/content/generate", json=payload)


# =========================================================================
# Manual create / get
# =========================================================================

def test_manual_create_and_get(client):
    resp = _create_post(client)
    assert resp.status_code == 201, resp.text
    post = resp.json()
    assert post["status"] == "draft"
    assert post["source"] == "manual"
    assert post["caption"].startswith("Spooky season")
    assert {t["slug"] for t in post["tags"]} == {"halloween"}

    resp = client.get(f"/api/v1/posts/{post['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == post["id"]


def test_manual_create_unknown_campaign_404(client):
    resp = _create_post(client, campaign="nope")
    assert resp.status_code == 404


def test_manual_create_naive_datetime_422(client):
    resp = _create_post(client, planned_publish_at="2030-01-01T10:00:00")
    assert resp.status_code == 422


def test_manual_create_idempotent(client):
    payload = {
        "profile_id": 1, "platform": "instagram", "content_type": "reel",
        "campaign": "halloween", "caption": "Once only",
        "idempotency_key": "manual-key-1",
    }
    r1 = client.post("/api/v1/posts", json=payload)
    r2 = client.post("/api/v1/posts", json=payload)
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"]
    # Only one post exists
    resp = client.get("/api/v1/posts")
    assert resp.json()["total"] == 1


# =========================================================================
# Restart persistence (criterion 2)
# =========================================================================

def test_post_persists_after_restart(client, settings, tmp_path_factory):
    resp = _create_post(client)
    post_id = resp.json()["id"]

    # Simulate a restart: dispose engines, rebuild app + engine on the same DB file
    import os
    from app.main import create_app as _create_app
    from app.db import Base, make_engine, get_session_factory
    import app.db as db_module

    db_url = settings.DATABASE_URL

    # Dispose every engine that may hold the SQLite file
    try:
        get_session_factory().kw["bind"].dispose()
    except Exception:
        pass
    db_module._engine = None
    db_module._SessionLocal = None

    engine = None
    try:
        # Rebind to the same DB file and rebuild the app from scratch
        engine = make_engine(db_url)
        Base.metadata.create_all(engine)  # creates missing tables on the same file
        db_module._engine = engine
        db_module._SessionLocal = None

        from fastapi.testclient import TestClient
        fresh_client = TestClient(_create_app())

        resp = fresh_client.get(f"/api/v1/posts/{post_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["caption"].startswith("Spooky season")
    finally:
        db_module._engine = None
        db_module._SessionLocal = None
        if engine is not None:
            engine.dispose()


# =========================================================================
# PATCH / editing
# =========================================================================

def test_patch_caption(client):
    resp = _create_post(client)
    post_id = resp.json()["id"]

    resp = client.patch(f"/api/v1/posts/{post_id}", json={"caption": "Edited caption"})
    assert resp.status_code == 200
    assert resp.json()["caption"] == "Edited caption"


def test_patch_status_field_rejected_422(client):
    """No endpoint accepts `status` in a body - published can never be set."""
    resp = _create_post(client)
    post_id = resp.json()["id"]
    resp = client.patch(f"/api/v1/posts/{post_id}", json={"status": "published"})
    assert resp.status_code == 422
    resp = client.get(f"/api/v1/posts/{post_id}")
    assert resp.json()["status"] == "draft"


def test_patch_naive_datetime_422(client):
    """PATCH with naive planned_publish_at → 422 (not 500).

    POST already rejects naive datetimes (test_manual_create_naive_datetime_422),
    but the same validator was not reached on PATCH because the schema accepted the
    bare ISO string. Now PATCH parses then validates, and returns a 422 with the
    same ``validation_error`` code.
    """
    resp = _create_post(client)
    post_id = resp.json()["id"]
    resp = client.patch(f"/api/v1/posts/{post_id}", json={"planned_publish_at": "2030-01-01T10:00:00"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_patch_past_planned_time_422(client):
    resp = _create_post(client)
    post_id = resp.json()["id"]
    resp = client.patch(f"/api/v1/posts/{post_id}", json={"planned_publish_at": _paste()})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_edit_approved_post_returns_to_draft(client):
    resp = _create_post(client)
    post_id = resp.json()["id"]
    client.post(f"/api/v1/posts/{post_id}/approve")
    assert client.get(f"/api/v1/posts/{post_id}").json()["status"] == "approved"

    resp = client.patch(f"/api/v1/posts/{post_id}", json={"caption": "Changed!"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"
    assert resp.json()["approved_at"] is None


def test_archive_and_restore(client):
    resp = _create_post(client)
    post_id = resp.json()["id"]

    resp = client.post(f"/api/v1/posts/{post_id}/archive")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"
    assert resp.json()["archived_at"] is not None

    resp = client.post(f"/api/v1/posts/{post_id}/restore")
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"
    assert resp.json()["archived_at"] is None


# =========================================================================
# Approve / planned-publish transitions
# =========================================================================

def test_approve_draft(client):
    resp = _create_post(client)
    post_id = resp.json()["id"]
    resp = client.post(f"/api/v1/posts/{post_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert resp.json()["approved_at"] is not None


def test_approve_with_planned_time_goes_scheduled(client):
    resp = _create_post(client, planned_publish_at=_aware().isoformat())
    post_id = resp.json()["id"]
    assert resp.json()["status"] == "draft"
    resp = client.post(f"/api/v1/posts/{post_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "scheduled"


def test_set_time_on_approved_goes_scheduled(client):
    resp = _create_post(client)
    post_id = resp.json()["id"]
    client.post(f"/api/v1/posts/{post_id}/approve")

    resp = client.patch(f"/api/v1/posts/{post_id}", json={"planned_publish_at": _aware().isoformat()})
    assert resp.status_code == 200
    assert resp.json()["status"] == "scheduled"


def test_clear_time_on_scheduled_returns_approved(client):
    resp = _create_post(client)
    post_id = resp.json()["id"]
    client.post(f"/api/v1/posts/{post_id}/approve")
    client.patch(f"/api/v1/posts/{post_id}", json={"planned_publish_at": _aware().isoformat()})
    assert client.get(f"/api/v1/posts/{post_id}").json()["status"] == "scheduled"

    resp = client.patch(f"/api/v1/posts/{post_id}", json={"planned_publish_at": None})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_set_time_on_draft_stays_draft(client):
    resp = _create_post(client)
    post_id = resp.json()["id"]
    resp = client.patch(f"/api/v1/posts/{post_id}", json={"planned_publish_at": _aware().isoformat()})
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"


def test_approve_archived_409(client):
    resp = _create_post(client)
    post_id = resp.json()["id"]
    client.post(f"/api/v1/posts/{post_id}/archive")
    resp = client.post(f"/api/v1/posts/{post_id}/approve")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_transition"


def test_patch_archived_409(client):
    resp = _create_post(client)
    post_id = resp.json()["id"]
    client.post(f"/api/v1/posts/{post_id}/archive")
    resp = client.patch(f"/api/v1/posts/{post_id}", json={"caption": "no"})
    assert resp.status_code == 409


def test_set_planned_past_time_on_approved_also_422(client):
    resp = _create_post(client)
    post_id = resp.json()["id"]
    client.post(f"/api/v1/posts/{post_id}/approve")
    resp = client.patch(f"/api/v1/posts/{post_id}", json={"planned_publish_at": _paste()})
    assert resp.status_code == 422


# =========================================================================
# Filters & listing
# =========================================================================

def test_filter_platform(client):
    _create_post(client)
    _create_post(client, platform="tiktok", content_type="video")

    resp = client.get("/api/v1/posts", params={"platform": "tiktok"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["platform"] == "tiktok"


def test_filter_status(client):
    r = _create_post(client)
    post_id = r.json()["id"]
    _create_post(client)
    client.post(f"/api/v1/posts/{post_id}/approve")

    resp = client.get("/api/v1/posts", params={"status": ["approved"]})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["status"] == "approved"


def test_archived_excluded_by_default(client):
    r = _create_post(client)
    post_id = r.json()["id"]
    _create_post(client)
    client.post(f"/api/v1/posts/{post_id}/archive")

    resp = client.get("/api/v1/posts")
    assert resp.json()["total"] == 1
    resp = client.get("/api/v1/posts", params={"status": ["archived"]})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["status"] == "archived"


def test_filter_planned_date_range(client):
    _create_post(client, planned_publish_at=_aware(days=2).isoformat())
    _create_post(client)  # no planned time

    resp = client.get("/api/v1/posts", params={
        "planned_from": _aware(days=1).isoformat(),
        "planned_to": _aware(days=3).isoformat(),
    })
    assert resp.json()["total"] == 1


def test_filter_campaign(client):
    _create_post(client)
    resp = client.get("/api/v1/posts", params={"campaign": "lifestyle"})
    assert resp.json()["total"] == 0


# =========================================================================
# Variants
# =========================================================================

def test_list_variants(client, fake_claude):
    resp = _create_via_generate(client, fake_claude)
    post_id = resp.json()["id"]

    # Regenerate → second variant
    resp = client.post(f"/api/v1/content/{post_id}/regenerate", json={})
    assert resp.status_code == 201

    resp = client.get(f"/api/v1/posts/{post_id}/variants")
    assert resp.status_code == 200
    variants = resp.json()
    assert len(variants) == 2
    assert variants[0]["created_at"] >= variants[1]["created_at"]  # newest first


def test_apply_variant_copies_fields(client, fake_claude):
    resp = _create_via_generate(client, fake_claude)
    post_id = resp.json()["id"]
    original_caption = resp.json()["caption"]

    # New distinct variant
    from app.schemas import GeneratedContent
    fake_claude.result.content = GeneratedContent(
        content_format="v2", hook="New hook", caption="A clearly different caption",
        hashtags=["fresh"], call_to_action="", content_notes="",
    )
    resp = client.post(f"/api/v1/content/{post_id}/regenerate", json={})
    variant_id = resp.json()["id"]

    resp = client.post(f"/api/v1/posts/{post_id}/variants/{variant_id}/apply")
    assert resp.status_code == 200
    post = resp.json()
    assert post["caption"] == "A clearly different caption"
    assert post["hashtags"] == ["fresh"]
    assert post["caption"] != original_caption


def test_apply_variant_unknown_404(client, fake_claude):
    resp = _create_via_generate(client, fake_claude)
    post_id = resp.json()["id"]
    resp = client.post(f"/api/v1/posts/{post_id}/variants/999/apply")
    assert resp.status_code == 404


# =========================================================================
# `published` is unreachable in Phase 1
# =========================================================================

def test_no_endpoint_yields_published(client, fake_claude):
    """Exercise the full API surface: no post ever leaves with status published."""
    resp = _create_post(client)
    post_id = resp.json()["id"]

    # All transition endpoints
    client.post(f"/api/v1/posts/{post_id}/approve")
    client.patch(f"/api/v1/posts/{post_id}", json={"planned_publish_at": _aware().isoformat()})
    client.post(f"/api/v1/posts/{post_id}/archive")
    client.post(f"/api/v1/posts/{post_id}/restore")

    resp = client.get(f"/api/v1/posts/{post_id}")
    assert resp.json()["status"] != "published"

    resp = client.get("/api/v1/posts")
    for p in resp.json()["items"]:
        assert p["status"] != "published"

    # Status transitions only exist via action endpoints; PATCH rejects status
    resp = client.patch(f"/api/v1/posts/{post_id}", json={"status": "published"})
    assert resp.status_code == 422
