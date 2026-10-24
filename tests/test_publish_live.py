"""publish_post in dry_run and live modes + retry/failure handling + scheduler dispatch
(PLAN_PHASE2 §8/§9). Platform calls are stubbed or MockTransport; nothing is real."""
import base64
import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.db import get_session_factory
from app.models import ContentPost, NotificationLog, PlatformAccount, PublishAttempt
from app.platforms import PlatformError
from app.services import AppError, accounts
from app.services import publishing as pub

NOON = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)   # GMT: 12:00 local, inside 08:00-21:00
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
JPG = base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2Q==")


_n = itertools.count(1)


def db():
    return get_session_factory()()


class NoHttp(httpx.Client):
    """Fails the test if anything tries to leave the process."""

    def __init__(self):
        super().__init__(transport=httpx.MockTransport(self._boom))
        self.sent = 0

    def _boom(self, request):
        self.sent += 1
        raise AssertionError(f"unexpected HTTP call: {request.url}")


def stub_publisher(monkeypatch, fn=None, calls=None, check=None):
    """Replace the platform module inside the publishing service."""
    def publish(http, token, uid, post, media_url, media_path, on_ref=None, caption=None):
        if calls is not None:
            calls.append(SimpleNamespace(token=token, uid=uid, media_url=media_url, media_path=media_path, caption=caption))
        if fn:
            return fn(on_ref)
        if on_ref:
            on_ref("REF-1")
        from app.platforms import PublishResult
        return PublishResult("EXT-1", "https://www.instagram.com/p/abc/", NOON, "REF-1")
    monkeypatch.setattr(pub, "publisher", lambda platform: SimpleNamespace(publish=publish, check_status=check or (lambda *a: None)))


def make_account(platform="instagram"):
    d = db()
    d.add(PlatformAccount(model_profile_id=1, platform=platform, external_user_id="uid1", username="summer",
                          access_token_enc=accounts.encrypt("TOKEN-SECRET-A"), scopes=[], status="connected",
                          token_expires_at=NOON + timedelta(days=30)))
    d.commit()


def make_post(client, platform="instagram", content_type="reel", video=True, status="scheduled", planned=NOON,
              hashtags=("halloween", "spooky")):
    data, mime, name = (MP4, "video/mp4", "c.mp4") if video else (JPG, "image/jpeg", "c.jpg")
    a = client.post("/api/v1/assets/upload", files={"file": (name, data + str(next(_n)).encode(), mime)},   # unique bytes: duplicates are rejected
                    data={"description": "d"})
    assert a.status_code == 201, a.text
    r = client.post("/api/v1/posts", json={
        "profile_id": 1, "platform": platform, "content_type": content_type, "campaign": "lifestyle",
        "caption": "Hello world", "hashtags": list(hashtags), "asset_id": a.json()["id"]})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    client.post(f"/api/v1/posts/{pid}/approve")
    d = db()
    post = d.get(ContentPost, pid)
    post.status, post.planned_publish_at = status, planned
    d.commit()
    return pid


def post_of(pid):
    return db().get(ContentPost, pid)


@pytest.fixture
def live(phase2, client, monkeypatch):
    phase2(PUBLISHING_MODE="live")
    make_account()
    return client


# ---- modes -----------------------------------------------------------------

def test_off_and_reminders_modes_refuse(phase2, client):
    pid = make_post(client)
    with pytest.raises(AppError) as e:
        pub.publish_post(db(), pid, now=NOON)
    assert (e.value.code, e.value.status) == ("publishing_disabled", 409)
    phase2(PUBLISHING_MODE="reminders")
    with pytest.raises(AppError) as e:
        pub.publish_post(db(), pid, now=NOON)
    assert e.value.code == "publishing_reminders_only"
    assert post_of(pid).status == "scheduled" and db().query(PublishAttempt).count() == 0


def test_dry_run_sends_nothing_records_one_attempt_and_changes_no_state(phase2, client):
    phase2(PUBLISHING_MODE="dry_run")
    pid = make_post(client)                       # note: NO account connected - dry run does not need one
    http = NoHttp()
    for _ in range(3):                            # idempotent: repeated loops add nothing
        out = pub.publish_post(db(), pid, trigger="schedule", now=NOON, http=http)
    assert http.sent == 0
    assert out.status == "scheduled" and out.publish_attempt_count == 0 and out.published_at is None
    (attempt,) = db().query(PublishAttempt).all()
    assert attempt.trigger == "dry_run" and attempt.success is None
    assert "would publish reel to instagram" in attempt.error_message and "c.mp4" in attempt.error_message


def test_dry_run_still_enforces_preflight_and_window(phase2, client):
    phase2(PUBLISHING_MODE="dry_run")
    draft = make_post(client, status="draft")
    with pytest.raises(AppError) as e:
        pub.publish_post(db(), draft, now=NOON)
    assert e.value.code == "invalid_transition"
    pid = make_post(client)
    with pytest.raises(AppError) as e:
        pub.publish_post(db(), pid, now=datetime(2026, 11, 2, 22, 0, tzinfo=timezone.utc))
    assert e.value.code == "outside_posting_window"
    assert db().query(PublishAttempt).count() == 0


# ---- live: success ---------------------------------------------------------

def test_live_success_records_everything(live, monkeypatch):
    calls = []
    stub_publisher(monkeypatch, calls=calls)
    pid = make_post(live)
    out = pub.publish_post(db(), pid, trigger="manual", now=NOON, http=NoHttp())
    assert (out.status, out.published_via, out.external_post_id) == ("published", "api", "EXT-1")
    assert out.external_url == "https://www.instagram.com/p/abc/" and out.published_at == NOON
    assert out.claimed_at is None and out.next_attempt_at is None and out.publish_attempt_count == 1
    (att,) = db().query(PublishAttempt).all()
    assert (att.success, att.trigger, att.attempt_no, att.platform_ref) == (True, "manual", 1, "REF-1")
    c = calls[0]
    assert c.token == "TOKEN-SECRET-A" and c.uid == "uid1" and c.caption == "Hello world\n\n#halloween #spooky"
    assert c.media_url.startswith("https://studio.example.com/m/") and c.media_path.is_file()


def test_live_end_to_end_instagram_reel_through_the_real_module(live, monkeypatch):
    from app.platforms import instagram
    monkeypatch.setattr(instagram, "_sleep", lambda s: None)
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        p = req.url.path
        if p.endswith("/content_publishing_limit"):
            return httpx.Response(200, json={"data": [{"quota_usage": 1, "config": {"quota_total": 100}}]})
        if p.endswith("/uid1/media"):
            body = req.content.decode()
            assert "video_url=https%3A%2F%2Fstudio.example.com%2Fm%2F" in body and "media_type=REELS" in body
            return httpx.Response(200, json={"id": "C9"})
        if p.endswith("/C9"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if p.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "M9"})
        return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/zzz/",
                                         "timestamp": "2026-11-02T12:00:05+0000"})
    pid = make_post(live)
    out = pub.publish_post(db(), pid, now=NOON, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert (out.status, out.external_post_id, out.external_url) == ("published", "M9", "https://www.instagram.com/reel/zzz/")
    # the container id was stored as soon as it existed
    assert db().query(PublishAttempt).one().platform_ref == "C9"


def test_tiktok_inbox_success_is_sent_to_drafts_not_published(phase2, client, monkeypatch, env):
    phase2(PUBLISHING_MODE="live")
    make_account("tiktok")
    stub_publisher(monkeypatch)
    pid = make_post(client, "tiktok", "video")
    out = pub.publish_post(db(), pid, now=NOON, http=NoHttp())
    assert out.status == "sent_to_drafts" and out.published_at is None and out.published_via is None
    assert out.external_post_id == "EXT-1"
    assert db().query(NotificationLog).filter_by(event_key=f"tiktok:{pid}").count() == 1
    from app.services.posts import mark_posted
    assert mark_posted(db(), pid).status == "published"


# ---- live: pre-flight ------------------------------------------------------

def test_preflight_failures_leave_the_post_untouched(phase2, client, monkeypatch):
    phase2(PUBLISHING_MODE="live")
    stub_publisher(monkeypatch)
    pid = make_post(client)
    with pytest.raises(AppError) as e:                               # no connected account
        pub.publish_post(db(), pid, now=NOON)
    assert e.value.code == "account_not_connected"
    make_account()
    draft = make_post(client, status="draft")
    with pytest.raises(AppError) as e:
        pub.publish_post(db(), draft, now=NOON)
    assert e.value.code == "invalid_transition"
    assert post_of(pid).status == "scheduled" and db().query(PublishAttempt).count() == 0


def test_manual_publish_outside_window_needs_override(live, monkeypatch):
    stub_publisher(monkeypatch)
    pid = make_post(live)
    late = datetime(2026, 11, 2, 22, 0, tzinfo=timezone.utc)
    with pytest.raises(AppError) as e:
        pub.publish_post(db(), pid, now=late)
    assert e.value.code == "outside_posting_window" and post_of(pid).status == "scheduled"
    assert pub.publish_post(db(), pid, now=late, override_window=True).status == "published"


def test_lost_claim_race_is_already_publishing(live, monkeypatch):
    stub_publisher(monkeypatch)
    pid = make_post(live)
    monkeypatch.setattr(pub, "check_publishable", lambda *a, **k: None)   # both callers passed pre-flight ...
    d = db()
    d.get(ContentPost, pid).status = "publishing"                          # ... and the other one claimed first
    d.commit()
    with pytest.raises(AppError) as e:
        pub.publish_post(db(), pid, now=NOON)
    assert (e.value.code, e.value.status) == ("already_publishing", 409)
    assert db().query(PublishAttempt).count() == 0


def test_a_publishing_post_cannot_be_edited_or_approved(live):
    pid = make_post(live)
    d = db()
    d.get(ContentPost, pid).status = "publishing"
    d.commit()
    assert live.patch(f"/api/v1/posts/{pid}", json={"caption": "x"}).status_code == 409
    assert live.post(f"/api/v1/posts/{pid}/approve").status_code == 409
    assert live.post(f"/api/v1/posts/{pid}/archive").status_code == 409


# ---- live: failures --------------------------------------------------------

def failing(code, transient=True, message="boom", status=None):
    def fn(on_ref):
        if on_ref:
            on_ref("REF-F")
        raise PlatformError(code, message, transient, status)
    return fn


def test_transient_failures_back_off_inside_the_window_then_fail_with_a_push(live, monkeypatch):
    stub_publisher(monkeypatch, failing("upstream_error", True, "oops", 503))
    pid = make_post(live)
    t = NOON
    out = pub.publish_post(db(), pid, now=t)
    assert (out.status, out.publish_attempt_count, out.next_attempt_at) == ("scheduled", 1, t + timedelta(minutes=5))
    assert out.last_publish_error == "upstream_error: oops"
    t = out.next_attempt_at
    out = pub.publish_post(db(), pid, trigger="retry", now=t)
    assert (out.status, out.publish_attempt_count, out.next_attempt_at) == ("scheduled", 2, t + timedelta(minutes=15))
    out = pub.publish_post(db(), pid, trigger="retry", now=out.next_attempt_at)
    assert out.status == "failed" and out.publish_attempt_count == 3 and out.next_attempt_at is None
    attempts = db().query(PublishAttempt).order_by(PublishAttempt.id).all()
    assert [(a.attempt_no, a.success, a.error_code, a.http_status, a.platform_ref) for a in attempts] == \
        [(1, False, "upstream_error", 503, "REF-F"), (2, False, "upstream_error", 503, "REF-F"),
         (3, False, "upstream_error", 503, "REF-F")]
    assert db().query(NotificationLog).filter_by(event="publish_failed").count() == 1
    assert db().query(NotificationLog).filter_by(event_key=f"failed:{pid}:3").count() == 1


def test_retry_after_evening_failure_moves_to_next_window_start(live, monkeypatch):
    stub_publisher(monkeypatch, failing("timeout", True))
    pid = make_post(live)
    out = pub.publish_post(db(), pid, now=datetime(2026, 11, 2, 20, 58, tzinfo=timezone.utc))
    assert out.status == "scheduled"
    assert out.next_attempt_at == datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)   # 08:00 next morning, not 21:03


def test_permanent_failure_fails_immediately(live, monkeypatch):
    stub_publisher(monkeypatch, failing("media_rejected", False, "bad video"))
    pid = make_post(live)
    out = pub.publish_post(db(), pid, now=NOON)
    assert out.status == "failed" and out.publish_attempt_count == 1 and out.next_attempt_at is None
    assert out.last_publish_error == "media_rejected: bad video"


def test_token_expired_marks_account_and_asks_to_reconnect(live, monkeypatch):
    stub_publisher(monkeypatch, failing("token_expired", False, "Error validating access token", 401))
    pid = make_post(live)
    out = pub.publish_post(db(), pid, now=NOON)
    assert out.status == "failed" and "Reconnect the instagram account" in out.last_publish_error
    acct = db().query(PlatformAccount).one()
    assert acct.status == "expired"
    assert db().query(NotificationLog).filter_by(event="account_attention").count() == 1
    with pytest.raises(AppError):                                  # an expired account blocks the next publish
        pub.publish_post(db(), pid, now=NOON)


def test_unexpected_exception_is_a_generic_transient_error(live, monkeypatch):
    def crash(on_ref):
        raise RuntimeError("secret internals TOKEN-SECRET-A")
    stub_publisher(monkeypatch, crash)
    pid = make_post(live)
    out = pub.publish_post(db(), pid, now=NOON)
    assert out.status == "scheduled" and out.last_publish_error == "internal_error: Unexpected error while publishing."
    assert "TOKEN-SECRET-A" not in (db().query(PublishAttempt).one().error_message or "")


def test_platform_messages_are_sanitized_before_storage(live, monkeypatch):
    stub_publisher(monkeypatch, failing("bad_request", False, "GET ?access_token=LEAKY123 failed for TOKEN-SECRET-A"))
    pid = make_post(live)
    out = pub.publish_post(db(), pid, now=NOON)
    stored = out.last_publish_error + (db().query(PublishAttempt).one().error_message or "")
    assert "LEAKY123" not in stored and "TOKEN-SECRET-A" not in stored


def test_vanished_media_file_fails_visibly_instead_of_skipping_forever(live, monkeypatch):
    calls = []
    stub_publisher(monkeypatch, calls=calls)
    pid = make_post(live)
    from app.config import get_settings
    for f in get_settings().MEDIA_UPLOAD_DIR.iterdir():
        f.unlink()
    out = pub.publish_post(db(), pid, now=NOON)
    assert out.status == "failed" and out.last_publish_error.startswith("media_not_publishable")
    assert calls == []                                              # the platform was never called
    assert db().query(NotificationLog).filter_by(event="publish_failed").count() == 1


# ---- retry action ----------------------------------------------------------

def test_retry_returns_failed_post_to_the_schedule(live, monkeypatch):
    stub_publisher(monkeypatch, failing("media_rejected", False))
    pid = make_post(live)
    pub.publish_post(db(), pid, now=NOON)
    out = pub.retry_post(db(), pid, now=NOON + timedelta(hours=1))
    assert (out.status, out.publish_attempt_count, out.last_publish_error, out.next_attempt_at) == ("scheduled", 0, None, None)
    assert out.planned_publish_at == NOON + timedelta(hours=1)    # was in the past -> due now, worker respects the window
    with pytest.raises(AppError) as e:
        pub.retry_post(db(), pid)
    assert e.value.code == "invalid_transition"                    # only failed posts can be retried


def test_editing_a_failed_post_makes_it_a_draft(live, monkeypatch):
    stub_publisher(monkeypatch, failing("media_rejected", False))
    pid = make_post(live)
    pub.publish_post(db(), pid, now=NOON)
    assert live.patch(f"/api/v1/posts/{pid}", json={"caption": "fixed"}).json()["status"] == "draft"


# ---- API -------------------------------------------------------------------

def test_publish_retry_attempts_api(phase2, client, app, monkeypatch):
    from app.api.dependencies import get_platform_http
    phase2(PUBLISHING_MODE="dry_run")
    app.dependency_overrides[get_platform_http] = lambda: NoHttp()
    try:
        pid = make_post(client, planned=datetime.now(timezone.utc))
        # dry run through the API only works inside the window; patch the clock-dependent rule
        import app.services.publishing as p
        monkeypatch.setattr(p, "check_publishable", lambda *a, **k: None)
        r = client.post(f"/api/v1/posts/{pid}/publish", json={"override_window": True})
        assert r.status_code == 200 and r.json()["status"] == "scheduled"
        att = client.get(f"/api/v1/posts/{pid}/attempts").json()
        assert len(att) == 1 and att[0]["trigger"] == "dry_run"
        assert client.get("/api/v1/posts/999/attempts").status_code == 404
        assert client.post(f"/api/v1/posts/{pid}/retry").status_code == 409   # not failed
        assert client.post("/api/v1/posts/999/publish").status_code == 404
    finally:
        app.dependency_overrides.pop(get_platform_http, None)


def test_publish_endpoint_refuses_when_off(client):
    pid = make_post(client)
    r = client.post(f"/api/v1/posts/{pid}/publish")
    assert r.status_code == 409 and r.json()["error"]["code"] == "publishing_disabled"


# ---- scheduler dispatch ----------------------------------------------------

def run(now, http=None):
    from app.services.scheduler import run_once
    return run_once(db(), now, http=http)


def test_scheduler_live_publishes_due_posts_only(live, monkeypatch):
    stub_publisher(monkeypatch)
    due = make_post(live)
    future = make_post(live, planned=NOON + timedelta(hours=3))
    retry_later = make_post(live)
    d = db()
    d.get(ContentPost, retry_later).next_attempt_at = NOON + timedelta(minutes=30)
    d.commit()
    r = run(NOON, NoHttp())
    assert (r["due"], r["published"], r["failed"]) == (1, 1, 0)
    assert post_of(due).status == "published"
    assert post_of(future).status == "scheduled" and post_of(retry_later).status == "scheduled"
    r2 = run(NOON + timedelta(hours=3, minutes=31), NoHttp())      # backlog spacing: one post per min_gap
    assert post_of(retry_later).status == "published" and r2["published"] == 1


def test_scheduler_never_publishes_outside_the_window(live, monkeypatch):
    stub_publisher(monkeypatch)
    pid = make_post(live, planned=datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc))
    assert run(datetime(2026, 11, 2, 21, 30, tzinfo=timezone.utc))["due"] == 0       # 21:30 local
    assert run(datetime(2026, 11, 3, 7, 59, tzinfo=timezone.utc))["due"] == 0
    assert post_of(pid).status == "scheduled"
    assert run(datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc))["published"] == 1   # first minute of the window


def test_scheduler_one_bad_post_does_not_stop_the_rest(live, monkeypatch):
    stub_publisher(monkeypatch)
    no_account = make_post(live, "tiktok", "video", planned=NOON - timedelta(minutes=5))   # no tiktok account
    good = make_post(live, planned=NOON)
    r = run(NOON)
    assert (r["due"], r["failed"], r["published"]) == (2, 1, 1)
    # the unpublishable post is failed visibly (not skipped forever) while the good one still went out
    assert post_of(no_account).status == "failed" and post_of(good).status == "published"


def test_scheduler_failures_are_counted_and_recorded(live, monkeypatch):
    stub_publisher(monkeypatch, failing("media_rejected", False))
    pid = make_post(live)
    r = run(NOON)
    assert (r["failed"], r["published"]) == (1, 0) and post_of(pid).status == "failed"


def test_scheduler_dry_run_counts_without_http_or_state_change(phase2, client):
    phase2(PUBLISHING_MODE="dry_run")
    pid = make_post(client)
    http = NoHttp()
    assert run(NOON, http)["dry_runs"] == 1
    again = run(NOON + timedelta(minutes=1), http)
    assert (again["due"], again["dry_runs"]) == (1, 0)                       # still due, but no new attempt row
    assert db().query(PublishAttempt).count() == 1 and http.sent == 0
    assert post_of(pid).status == "scheduled"
