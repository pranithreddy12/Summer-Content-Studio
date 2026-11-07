"""Regression tests for the 2026-10-24 audit (docs/AUDIT_2026-10-24.md). One section per finding."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.models import ContentPost, ModelProfile
from tests.test_publish_live import db, live, make_post, post_of  # noqa: F401 (live = fixture)


def future(days=10, hour=12):
    d = datetime.now(timezone.utc) + timedelta(days=days)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0)


def form_of(pid, **over):
    """What the dashboard form posts back untouched."""
    p = post_of(pid)
    from app.web.routes import local_time
    lt = local_time(p.planned_publish_at)
    data = {"hook": p.hook or "", "caption": p.caption, "hashtags": " ".join(p.hashtags or []),
            "call_to_action": p.call_to_action or "", "content_notes": p.content_notes or "",
            "campaign": p.campaign, "tags": ", ".join(t.name for t in p.tags if not t.is_category),
            "asset_id": str(p.media_asset_id or ""), "planned_publish_at": lt.strftime("%Y-%m-%dT%H:%M") if lt else ""}
    data.update(over)
    return data


# ---- H1: persona edit must keep the rules as lists -----------------------------------

def test_h1_profile_edit_keeps_rules_as_lists(client):
    r = client.post("/profiles/1", data={"name": "Summer", "persona_description": "p", "tone_style": "t",
                                         "communication_rules": "Be kind\nNo spam\n", "restricted_topics": "Politics\nReligion",
                                         "style_notes": "", "cta_guidelines": "", "is_active": "true"}, follow_redirects=False)
    assert r.status_code == 303
    prof = db().get(ModelProfile, 1)
    assert prof.communication_rules == ["Be kind", "No spam"]
    assert prof.restricted_topics == ["Politics", "Religion"]


# ---- H2: Save without edits keeps approval and time --------------------------------

def test_h2_plain_save_keeps_scheduled_status_and_time(client):
    pid = make_post(client, status="scheduled", planned=future())
    before = post_of(pid)
    r = client.post(f"/posts/{pid}", data=form_of(pid), follow_redirects=False)
    assert r.status_code == 303
    after = post_of(pid)
    assert after.status == "scheduled" and after.approved_at is not None
    assert after.planned_publish_at == before.planned_publish_at


def test_h2_summer_time_roundtrips_through_the_form(client, env):
    env(APP_TIMEZONE="Europe/London")
    when = datetime(2099, 7, 1, 11, 0, tzinfo=timezone.utc)               # 12:00 BST
    pid = make_post(client, status="scheduled", planned=when)
    page = client.get(f"/posts/{pid}").text
    assert 'value="2099-07-01T12:00"' in page                              # shown in London time, not UTC
    client.post(f"/posts/{pid}", data=form_of(pid), follow_redirects=False)
    assert post_of(pid).planned_publish_at == when                         # not shifted by a Save
    client.post(f"/posts/{pid}", data=form_of(pid, planned_publish_at="2099-07-02T13:30"), follow_redirects=False)
    assert post_of(pid).planned_publish_at == datetime(2099, 7, 2, 12, 30, tzinfo=timezone.utc)


def test_h2_editing_the_caption_still_resets_approval_and_bad_time_is_reported(client):
    pid = make_post(client, status="scheduled", planned=future())
    client.post(f"/posts/{pid}", data=form_of(pid, caption="Changed"), follow_redirects=False)
    p = post_of(pid)
    assert p.status == "draft" and p.approved_at is None
    t = post_of(pid).planned_publish_at
    r = client.post(f"/posts/{pid}", data=form_of(pid, planned_publish_at="garbage"), follow_redirects=False)
    assert "not+a+valid" in r.headers["location"] or "not%20a%20valid" in r.headers["location"]
    assert post_of(pid).planned_publish_at == t


def test_h2_setting_only_a_time_on_an_approved_post_schedules_it(client):
    pid = make_post(client, status="approved", planned=None)
    when = future(3)
    from app.web.routes import local_time
    client.post(f"/posts/{pid}", data=form_of(pid, planned_publish_at=local_time(when).strftime("%Y-%m-%dT%H:%M")), follow_redirects=False)
    p = post_of(pid)
    assert p.status == "scheduled" and p.approved_at is not None


# ---- M15: provider errors on the dashboard flash instead of 500 -----------------------

def test_m15_dashboard_provider_error_is_a_flash_not_a_500(app, client):
    from app.api.dependencies import get_claude_client
    from app.services.claude_client import GenerationError

    class Down:
        settings = SimpleNamespace(CLAUDE_MODEL="m")

        def generate(self, *a, **k):
            raise GenerationError("auth_error", "Anthropic rejected the API key.", 502, "anthropic", "m")
    pid = make_post(client, status="draft", planned=None)
    app.dependency_overrides[get_claude_client] = lambda: Down()
    try:
        tc = TestClient(app, raise_server_exceptions=False)
        r = tc.post(f"/posts/{pid}/regenerate", data={}, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_claude_client, None)
    assert r.status_code == 303 and "rejected" in r.headers["location"]


# ---- H3: nothing that calls an LLM / hashes files may block the event loop ----------

def test_h3_slow_handlers_are_sync_defs():
    import inspect
    from app.api import assets as api_assets, content as api_content
    from app.web import routes
    for fn in (routes.generate_content, routes.post_regenerate, routes.assets_sync,
               api_content.generate_content, api_content.regenerate_content, api_assets.sync_assets_endpoint):
        assert not inspect.iscoroutinefunction(fn), f"{fn.__module__}.{fn.__name__} would freeze the app"


# ---- M1: dashboard Sync button -------------------------------------------------------

def test_m1_dashboard_sync_route_is_reachable(client):
    r = client.post("/assets/sync", follow_redirects=False)
    assert r.status_code == 303 and "Sync+complete" in r.headers["location"].replace("%20", "+")


# ---- H4: a retry must first ask the platform whether the lost attempt went out ----------

def test_h4_retry_does_not_republish_when_the_platform_already_has_it(live, monkeypatch):
    import app.services.publishing as pub
    from app.platforms import PlatformError, PublishResult
    from tests.test_publish_live import NOON, stub_publisher, failing
    calls = []
    stub_publisher(monkeypatch, failing("timeout", True, "read timed out"), calls=calls)
    pid = make_post(live)
    out = pub.publish_post(db(), pid, now=NOON)
    assert out.status == "scheduled" and len(calls) == 1                        # first attempt: response lost, ref saved
    stub_publisher(monkeypatch, calls=calls, check=lambda *a: PublishResult("EXT-9", "https://ig/p/9", NOON, "REF-F"))
    out = pub.publish_post(db(), pid, trigger="retry", now=NOON + timedelta(minutes=5))
    assert out.status == "published" and out.external_post_id == "EXT-9"
    assert len(calls) == 1                                                     # no second publish call


def test_h4_retry_publishes_when_the_platform_has_nothing(live, monkeypatch):
    import app.services.publishing as pub
    from tests.test_publish_live import NOON, stub_publisher, failing
    calls = []
    stub_publisher(monkeypatch, failing("timeout", True, "read timed out"), calls=calls)
    pid = make_post(live)
    pub.publish_post(db(), pid, now=NOON)
    stub_publisher(monkeypatch, calls=calls)                                   # check_status -> None
    out = pub.publish_post(db(), pid, trigger="retry", now=NOON + timedelta(minutes=5))
    assert out.status == "published" and len(calls) == 2


# ---- H5: CSRF applies to /api too, and "Origin: null" is cross-origin ---------------------

def test_h5_cross_origin_api_post_is_rejected_and_changes_nothing(client):
    pid = make_post(client, status="draft", planned=None)
    for origin in ("https://evil.example", "null"):
        r = client.post(f"/api/v1/posts/{pid}/approve", headers={"Origin": origin})
        assert r.status_code == 403, origin
    assert post_of(pid).status == "draft"
    assert client.post(f"/api/v1/posts/{pid}/approve", headers={"Origin": "http://testserver"}).status_code == 200
    r = client.post("/assets/sync", headers={"Origin": "null"}, follow_redirects=False)
    assert r.status_code == 403
    assert client.get("/api/v1/posts", headers={"Origin": "https://evil.example"}).status_code == 200     # reads stay open


# ---- H6: init-db must not stamp a tracked database whose upgrade failed -------------------

def test_h6_init_db_fails_instead_of_stamping_a_tracked_database(app, monkeypatch):
    from click.testing import CliRunner
    from sqlalchemy import text
    import app.cli as cli_mod
    from app.db import get_engine
    with get_engine().begin() as c:
        c.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
    stamped = []
    monkeypatch.setattr(cli_mod.alembic_command, "upgrade", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(cli_mod.alembic_command, "stamp", lambda *a, **k: stamped.append(a))
    r = CliRunner().invoke(cli_mod.cli, ["init-db"])
    assert r.exit_code == 1 and "Migration failed" in r.output and not stamped


# ---- M2: a backlog is released one post per min_gap, not all at once ---------------------

def test_m2_overdue_backlog_is_not_burst_posted(live, monkeypatch):
    import app.services.publishing as pub
    import app.services.scheduler as sch
    from tests.test_publish_live import NOON, NoHttp
    from tests.test_scheduler import stub_platform
    stub_platform(monkeypatch)
    ids = [make_post(live, planned=NOON - timedelta(days=d)) for d in (3, 2, 1)]
    out1 = sch.run_once(db(), NOON, http=NoHttp())
    assert out1["published"] == 1
    assert sch.run_once(db(), NOON + timedelta(minutes=1), http=NoHttp())["published"] == 0
    assert sch.run_once(db(), NOON + timedelta(hours=3, minutes=5), http=NoHttp())["published"] == 1
    assert sorted(post_of(i).status for i in ids) == ["published", "published", "scheduled"]


# ---- M3: the planner does not schedule posts that pre-flight will always refuse ------------

def test_m3_planner_skips_unpostable_media_when_publishing_is_automatic(live):
    from app.models import PostingRule
    from app.services.planner import plan_week
    from tests.test_publish_live import NOON, make_account
    make_account("tiktok")
    d = db()
    rule = PostingRule(model_profile_id=1, platform="tiktok", posts_per_week=7, min_per_day=1, max_per_day=1, window_start="08:00",
                       window_end="21:00", min_gap_minutes=180, timezone="Europe/London", is_active=True)
    d.add(rule)
    d.commit()
    image = make_post(live, platform="tiktok", content_type="photo_post", video=False, status="approved", planned=None)
    video = make_post(live, platform="tiktok", content_type="video", video=True, status="approved", planned=None)
    plan_week(db(), db().get(PostingRule, rule.id), NOON)
    assert post_of(image).status == "approved" and post_of(image).planned_publish_at is None
    assert post_of(video).status == "scheduled"


# ---- M4: "Use this version" never touches a post that is being / has been sent ----------

def test_m4_apply_variant_refuses_live_posts_and_returns_failed_to_draft(live):
    from app.models import ContentVariant
    pid = make_post(live, status="draft", planned=None)
    d = db()
    d.add(ContentVariant(post_id=pid, caption="Variant", hashtags=[], hook="", call_to_action="", content_notes="", content_format="x"))
    d.commit()
    vid = d.query(ContentVariant).filter_by(post_id=pid).first().id
    for status in ("publishing", "sent_to_drafts"):
        d = db()
        d.get(ContentPost, pid).status = status
        d.commit()
        assert live.post(f"/api/v1/posts/{pid}/variants/{vid}/apply").status_code == 409
        assert post_of(pid).caption == "Hello world"
    d = db()
    p = d.get(ContentPost, pid)
    p.status, p.approved_at = "failed", None
    d.commit()
    assert live.post(f"/api/v1/posts/{pid}/variants/{vid}/apply").status_code == 200
    assert post_of(pid).status == "draft" and post_of(pid).caption == "Variant"


# ---- M5: several tag filters = asset has ALL of them ---------------------------------------

def test_m5_multi_tag_filter(client):
    a = client.post("/api/v1/assets/upload", files={"file": ("a.jpg", b"\xff\xd8\xff" + b"m5a" * 20, "image/jpeg")},
                    data={"description": "d", "tags": "halloween,pool"})
    assert a.status_code == 201, a.text
    client.post("/api/v1/assets/upload", files={"file": ("b.jpg", b"\xff\xd8\xff" + b"m5b" * 20, "image/jpeg")},
                data={"description": "d", "tags": "halloween"})
    both = client.get("/api/v1/assets", params=[("tag", "halloween"), ("tag", "pool")]).json()
    assert both["total"] == 1 and both["items"][0]["id"] == a.json()["id"]
    assert client.get("/api/v1/assets", params=[("tag", "halloween")]).json()["total"] == 2


# ---- M12 / M13 -------------------------------------------------------------------------------

def test_m12_delete_survives_an_unremovable_file(client, monkeypatch):
    from pathlib import Path
    from app.services.assets import delete_asset_permanently
    a = client.post("/api/v1/assets/upload", files={"file": ("a.jpg", b"\xff\xd8\xff" + b"m12" * 20, "image/jpeg")}, data={"description": "d"})
    real = Path.unlink

    def deny(self, *args, **kw):
        if self.suffix == ".jpg":
            raise PermissionError("locked")
        return real(self, *args, **kw)
    monkeypatch.setattr(Path, "unlink", deny)
    out = delete_asset_permanently(db(), a.json()["id"])
    assert out["file_removed"] is False


def test_m13_cancelled_upload_leaves_no_temp_file(app):
    import asyncio
    from fastapi import UploadFile
    from app.config import get_settings
    from app.services.assets import upload_asset

    class Dying:
        filename = "x.jpg"
        n = 0

        async def read(self, size):
            self.n += 1
            if self.n == 1:
                return b"\xff\xd8\xff" + b"x" * 100
            raise asyncio.CancelledError()
    folder = get_settings().MEDIA_UPLOAD_DIR
    before = set(folder.glob("tmp-*"))
    try:
        asyncio.run(upload_asset(db(), Dying()))
    except BaseException:
        pass
    assert set(folder.glob("tmp-*")) == before


# ---- M6: uploads and generation have separate rate-limit buckets -----------------------------

def test_m6_uploads_do_not_use_up_the_generation_budget(app, client):
    from app.api.dependencies import get_claude_client
    from app.security import _rate_limit_store
    from app.services.claude_client import GenerationError

    class Down:                                           # never reach (or cache) a real provider client
        settings = SimpleNamespace(CLAUDE_MODEL="m")

        def generate(self, *a, **k):
            raise GenerationError("rate_limited", "x", 429, "fake", "m")
    app.dependency_overrides[get_claude_client] = lambda: Down()
    _rate_limit_store.clear()
    for i in range(12):
        client.post("/api/v1/assets/upload", files={"file": (f"u{i}.jpg", b"\xff\xd8\xff" + bytes([i]) * 40, "image/jpeg")}, data={"description": "d"})
    try:
        r = client.post("/api/v1/content/generate-random", json={"platform": "instagram", "campaign": "auto", "count": 1})
    finally:
        app.dependency_overrides.pop(get_claude_client, None)
    assert r.status_code != 429 or "Rate limit exceeded" not in r.text


# ---- M7: dashboard validation problems are flashes, not 500s ---------------------------------

def test_m7_invalid_generate_combo_and_empty_caption_are_flashes(app, client):
    from app.api.dependencies import get_claude_client
    app.dependency_overrides[get_claude_client] = lambda: SimpleNamespace(settings=SimpleNamespace(CLAUDE_MODEL="m"))
    tc = TestClient(app, raise_server_exceptions=False)
    r = tc.post("/generate", data={"profile_id": 1, "platform": "tiktok", "content_type": "feed_post", "campaign": "lifestyle"}, follow_redirects=False)
    app.dependency_overrides.pop(get_claude_client, None)
    assert r.status_code == 303 and "/generate" in r.headers["location"]
    pid = make_post(client, status="draft", planned=None)
    r = tc.post(f"/posts/{pid}", data=form_of(pid, caption=""), follow_redirects=False)
    assert r.status_code == 303 and "kind=error" in r.headers["location"]


# ---- M8: caption + hashtags are checked together ---------------------------------------------

def test_m8_caption_plus_hashtags_over_the_limit_fails_preflight(live):
    import app.services.publishing as pub
    from app.services import AppError
    import pytest as _pytest
    pid = make_post(live, hashtags=tuple(f"tag{i:02d}" for i in range(20)))
    d = db()
    d.get(ContentPost, pid).caption = "x" * 2150            # fine alone, 2,300+ with the hashtags
    d.commit()
    with _pytest.raises(AppError) as e:
        pub.check_publishable(db(), post_of(pid), override_window=True)
    assert e.value.code == "caption_too_long"


# ---- M9 / M10 / M11 -----------------------------------------------------------------------------

def test_m9_engagement_polls_at_most_every_30_minutes():
    from app.services import engagement
    assert engagement.SYNC_EVERY >= timedelta(minutes=30)


def test_m10_tiktok_token_is_not_refreshed_every_half_hour(phase2, client):
    import app.services.scheduler as sch
    from app.models import PlatformAccount
    from app.services import accounts
    from tests.test_publish_live import NOON
    d = db()
    d.add(PlatformAccount(model_profile_id=1, platform="tiktok", external_user_id="t1", username="s", access_token_enc=accounts.encrypt("T"),
                          scopes=[], status="connected", token_expires_at=NOON + timedelta(hours=20)))
    d.commit()
    out = sch.refresh_due_tokens(db(), NOON, None)                 # 20 h left of a 24 h token: not due
    assert out == {"refreshed": 0, "failed": 0}


def test_m11_heartbeat_is_stamped_when_the_iteration_ends(phase2, client, monkeypatch):
    import app.services.scheduler as sch
    from tests.test_publish_live import NOON, NoHttp
    phase2(PUBLISHING_MODE="dry_run")
    import itertools
    import time
    ticks = itertools.count(0, 400)                      # every read of the monotonic clock = 400 s later
    monkeypatch.setattr(time, "monotonic", lambda: float(next(ticks)))
    sch.run_once(db(), NOON, http=NoHttp())
    last = db().query(sch.WorkerHeartbeat).one().last_seen
    assert last.replace(tzinfo=timezone.utc) >= NOON + timedelta(minutes=5)      # not the iteration's start time


# ---- M14: security headers; live mode is production-grade even if APP_ENV was forgotten ----------

def test_m14_security_headers(client):
    r = client.get("/health")
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"


def test_m14_live_mode_refuses_to_start_without_a_password(env):
    import pytest
    from app.main import create_app
    env(PUBLISHING_MODE="live", APP_ENV="development", DASHBOARD_USERNAME="", DASHBOARD_PASSWORD="")
    with pytest.raises(RuntimeError, match="PUBLISHING_MODE=live"):
        create_app()


# ---- M16 / M17 / M18 ---------------------------------------------------------------------------------

def test_m16_blank_idempotency_key_is_no_key(app, client):
    tc = TestClient(app, raise_server_exceptions=False)
    body = {"profile_id": 1, "platform": "instagram", "content_type": "reel", "campaign": "lifestyle", "caption": "a", "idempotency_key": " "}
    assert [tc.post("/api/v1/posts", json=body).status_code for _ in range(3)] == [201, 201, 201]


def test_m17_planner_always_keeps_min_gap():
    import random
    from datetime import date
    from app.services.planner import make_slots
    rule = SimpleNamespace(posts_per_week=21, min_per_day=3, max_per_day=3, window_start="11:30", window_end="19:15", min_gap_minutes=180,
                           timezone="Asia/Kolkata", enforce_window=True, model_profile_id=1)
    for seed in range(40):
        slots = make_slots(rule, date(2099, 8, 10), datetime(2099, 8, 1, tzinfo=timezone.utc), random.Random(seed))
        by_day = {}
        for s in slots:
            by_day.setdefault(s.local_day, []).append(s.start_utc)
        assert len(slots) == 21
        for times in by_day.values():
            times.sort()
            assert all((b - a) >= timedelta(minutes=180) for a, b in zip(times, times[1:])), (seed, times)


def test_m18_nulls_and_bad_ids_are_422_not_500(app, client):
    tc = TestClient(app, raise_server_exceptions=False)
    pid = make_post(client, status="draft", planned=None)
    cases = [
        ("patch", "/api/v1/profiles/1", {"restricted_topics": None}),
        ("patch", "/api/v1/profiles/1", {"restricted_topics": True}),
        ("patch", "/api/v1/profiles/1", {"is_active": None}),
        ("patch", f"/api/v1/posts/{pid}", {"caption": None}),
        ("patch", f"/api/v1/posts/{pid}", {"asset_id": 2 ** 70}),
        ("patch", "/api/v1/assets/1", {"model_profile_id": 0}),
        ("post", "/api/v1/posts", {"profile_id": 2 ** 70, "platform": "instagram", "content_type": "reel", "campaign": "lifestyle", "caption": "a"}),
        ("post", "/api/v1/planner/plan", {"profile_id": 2 ** 70, "platform": "instagram"}),
    ]
    for method, url, body in cases:
        assert getattr(tc, method)(url, json=body).status_code == 422, (url, body)
    rules = tc.get("/api/v1/planner/rules").json()
    if rules:
        assert tc.put(f"/api/v1/planner/rules/{rules[0]['id']}", json={"posts_per_week": None}).status_code == 422
    assert tc.patch(f"/api/v1/posts/{pid}", json={"planned_publish_at": None}).status_code == 200      # clearing stays allowed


def test_m18_draft_reply_provider_error_is_a_clean_error(app, client):
    from app.api.dependencies import get_claude_client
    from app.models import Comment
    from app.services.claude_client import GenerationError
    from tests.test_publish_live import NOON

    class Down:
        settings = SimpleNamespace(CLAUDE_MODEL="m")

        def generate(self, *a, **k):
            raise GenerationError("rate_limited", "slow down", 429, "fake", "m")
    pid = make_post(client, status="published", planned=None)
    d = db()
    d.add(Comment(post_id=pid, platform="instagram", external_id="cz", author_username="fan", text="hi", reply_status="none", created_at=NOON))
    d.commit()
    cid = d.query(Comment).filter_by(external_id="cz").one().id
    app.dependency_overrides[get_claude_client] = lambda: Down()
    try:
        r = TestClient(app, raise_server_exceptions=False).post(f"/api/v1/comments/{cid}/draft-reply")
    finally:
        app.dependency_overrides.pop(get_claude_client, None)
    assert r.status_code == 429


# ---- Low items closed in the Phase 2 re-audit (2026-11-06) ---------------------------

def test_l1_no_username_inside_inline_js():
    from pathlib import Path

    src = Path("app/web/templates/engagement.html").read_text(encoding="utf-8")
    import re

    assert "author_username" in src and not re.search(r'onclick="[^"]*author_username', src)


def test_l3_worker_interval_and_planner_hour_are_bounded():
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    for bad in ({"WORKER_INTERVAL_SECONDS": 0}, {"PLANNER_RUN_HOUR": 25}, {"PLANNER_RUN_HOUR": 24}):
        with pytest.raises(ValidationError):
            Settings(**bad)
    assert Settings(PLANNER_RUN_HOUR=23, WORKER_INTERVAL_SECONDS=1).PLANNER_RUN_HOUR == 23


def test_l4_bot_token_redacted_and_own_client_closed(client, monkeypatch):
    import httpx

    from app import platforms
    from app.logging_setup import redact
    from app.services import notifications

    token = "123456789:" + "Fake_" * 7   # built at runtime: keeps the delivery-zip key scan clean
    assert token not in redact(f"POST https://api.telegram.org/bot{token}/sendMessage failed")

    made = []

    def factory(*a, **k):
        made.append(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
        return made[-1]

    monkeypatch.setattr(platforms, "http_client", factory)
    monkeypatch.setattr(notifications, "get_settings", lambda: SimpleNamespace(
        NOTIFY_PROVIDER="ntfy", notify_configured=True, NTFY_SERVER="https://ntfy.example",
        NTFY_TOPIC=SimpleNamespace(get_secret_value=lambda: "t"), PUBLIC_BASE_URL=None), raising=False)
    notifications.notify(db(), "test", "l4-close-check", "t", "b")
    assert made and made[0].is_closed


def test_l10_profile_form_refuses_blank_name_and_huge_text(client):
    base = {"name": "Summer", "persona_description": "p", "tone_style": "t", "communication_rules": "",
            "restricted_topics": "", "style_notes": "", "cta_guidelines": "", "is_active": "true"}
    before = db().get(ModelProfile, 1).name
    r = client.post("/profiles/1", data={**base, "name": "   "}, follow_redirects=False)
    assert r.status_code == 303 and "kind=error" in r.headers["location"]
    r = client.post("/profiles/1", data={**base, "persona_description": "x" * 4001}, follow_redirects=False)
    assert "kind=error" in r.headers["location"]
    s = db()
    assert s.get(ModelProfile, 1).name == before and len(s.get(ModelProfile, 1).persona_description) <= 4000
    assert client.patch("/api/v1/profiles/1", json={"style_notes": "x" * 4001}).status_code == 422


def test_l11_tiktok_topup_asks_for_video(client, monkeypatch):
    import random
    from datetime import date

    from app.models import PostingRule
    from app.services import random_draft
    from app.services.planner import plan_week

    s = db()
    rule = PostingRule(model_profile_id=1, platform="tiktok", posts_per_week=7, min_per_day=1, max_per_day=1,
                       window_start="08:00", window_end="21:00", min_gap_minutes=180, timezone="UTC",
                       is_active=True, auto_topup=True)
    s.add(rule)
    s.commit()
    asked = []
    monkeypatch.setattr(random_draft, "generate_random_drafts",
                        lambda db_, req, client_: asked.append(req) or {"posts": []})
    plan_week(s, rule, datetime(2031, 3, 1, 20, 0, tzinfo=timezone.utc), rng=random.Random(1),
              start_day=date(2031, 3, 3), client=object())
    assert asked and all(r.media_type.value == "video" for r in asked)


def test_huge_query_number_is_422_not_500(client):
    """Overall audit 2026-11-07: 44 fuzz hits, one cause - an int above SQLite's 64-bit range."""
    big = str(2 ** 70)
    for url, q in (("/api/v1/posts", "asset_id"), ("/api/v1/assets", "offset"), ("/api/v1/comments", "post_id"),
                   ("/posts", "page"), ("/assets", "page"), ("/generate", "asset_id")):
        r = client.get(url, params={q: big})
        assert r.status_code == 422, (url, r.status_code)
