"""Phase 2 hardening (PLAN_PHASE2 §11a, §15): production guard, disk guard, media sync safety."""
import os
import shutil
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from app.config import clear_settings_cache, get_settings
from app.models import MediaAsset, NotificationLog
from tests.test_publish_live import db


@pytest.fixture
def prod(env):
    def set_env(**kw):
        env(APP_ENV="production", **kw)
        return get_settings()
    return set_env


# ---- production start-up guard ---------------------------------------------

def test_production_refuses_to_start_without_basic_auth(prod):
    from app.main import create_app
    prod(DASHBOARD_USERNAME="", DASHBOARD_PASSWORD="")
    with pytest.raises(RuntimeError) as e:
        create_app()
    assert "Basic auth is mandatory" in str(e.value) and "DASHBOARD_USERNAME" in str(e.value)


@pytest.mark.parametrize("user,pw", [("lee", ""), ("", "a-long-enough-password")])
def test_production_needs_both_username_and_password(prod, user, pw):
    from app.main import create_app
    prod(DASHBOARD_USERNAME=user, DASHBOARD_PASSWORD=pw)
    with pytest.raises(RuntimeError):
        create_app()


def test_production_rejects_a_short_password_without_echoing_it(prod):
    from app.main import create_app
    prod(DASHBOARD_USERNAME="lee", DASHBOARD_PASSWORD="short-pw")
    with pytest.raises(RuntimeError) as e:
        create_app()
    assert "at least 12" in str(e.value) and "short-pw" not in str(e.value)


def test_production_starts_with_good_credentials_and_hides_docs(prod):
    from fastapi.testclient import TestClient
    from app.main import create_app
    prod(DASHBOARD_USERNAME="lee", DASHBOARD_PASSWORD="a-long-enough-password")
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, auth=("lee", "a-long-enough-password")).status_code == 404, path
    assert client.get("/api/v1/profiles").status_code == 401


def test_development_still_starts_without_credentials_and_keeps_docs(app, client):
    assert client.get("/docs").status_code == 200 and client.get("/openapi.json").status_code == 200


# ---- disk guard + media sync (never deletes, never trusts a full disk) ------

@pytest.fixture
def sync_dir(app):
    d = get_settings().MEDIA_SYNC_DIR
    d.mkdir(parents=True, exist_ok=True)
    for f in d.rglob("*"):
        if f.is_file():
            f.unlink()
    return d


def put(dir_, rel, data=b"\xff\xd8\xff\xe0" + b"\x01" * 600):
    p = dir_ / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_sync_refuses_below_the_free_space_threshold_and_registers_nothing(env, app, client, sync_dir):
    put(sync_dir, "a.jpg")
    env(MIN_FREE_DISK_GB="1000000")                              # no machine has a petabyte free
    r = client.post("/api/v1/assets/sync")
    assert r.status_code == 507 and r.json()["error"]["code"] == "disk_low"
    assert db().query(MediaAsset).count() == 0


def test_low_disk_pushes_once_a_day(env, app, client, sync_dir):
    env(MIN_FREE_DISK_GB="1000000")
    for _ in range(3):
        client.post("/api/v1/assets/sync")
    rows = db().query(NotificationLog).filter_by(event="disk_low").all()
    assert len(rows) == 1 and rows[0].event_key.startswith("disk:")


def test_cli_sync_exits_non_zero_when_the_disk_is_low(env, app, sync_dir):
    from app.cli import cli
    put(sync_dir, "a.jpg")
    env(MIN_FREE_DISK_GB="1000000")
    r = CliRunner().invoke(cli, ["sync-media"])
    assert r.exit_code == 1 and "media sync refused" in r.output and db().query(MediaAsset).count() == 0


def test_sync_registers_new_files_skips_known_ones_and_never_touches_the_source(app, client, sync_dir):
    a, b = put(sync_dir, "Halloween/a.jpg"), put(sync_dir, "b.jpg", b"\xff\xd8\xff\xe0" + b"\x02" * 600)
    assert client.post("/api/v1/assets/sync").json() == {"added": 2, "skipped": 0}
    assert client.post("/api/v1/assets/sync").json()["added"] == 0
    assert a.exists() and b.exists() and a.read_bytes()[:4] == b"\xff\xd8\xff\xe0"          # source untouched


def test_a_file_removed_from_the_source_leaves_the_database_row(app, client, sync_dir):
    f = put(sync_dir, "gone.jpg")
    client.post("/api/v1/assets/sync")
    f.unlink()
    assert client.post("/api/v1/assets/sync").json()["added"] == 0
    assert db().query(MediaAsset).count() == 1 and db().query(MediaAsset).one().is_active is True


def test_placeholder_and_hidden_files_are_ignored(app, client, sync_dir):
    put(sync_dir, "empty.jpg", b"")
    put(sync_dir, ".hidden/x.jpg")
    put(sync_dir, ".dropbox.cache/y.jpg")
    assert client.post("/api/v1/assets/sync").json()["added"] == 0


def test_footer_no_longer_says_phase_1(app, client):
    page = client.get("/").text
    assert "Phase 1" not in page


# ---- the planner never approves ---------------------------------------------

def test_planner_never_approves_and_topup_creates_drafts_only(app, client, halloween_asset, fake_claude):
    import random
    from datetime import datetime, timezone
    from app.models import ContentPost, PostingRule
    from app.services.planner import plan_week
    from tests.test_planner import make_approved

    d = db()
    rule = PostingRule(model_profile_id=1, platform="instagram", posts_per_week=10, min_per_day=1, max_per_day=2,
                       window_start="08:00", window_end="21:00", min_gap_minutes=180, timezone="Europe/London",
                       is_active=True, auto_topup=True)
    d.add(rule)
    d.commit()
    approved_ids = [make_approved(i=i) for i in range(2)]
    draft = client.post("/api/v1/posts", json={"profile_id": 1, "platform": "instagram", "content_type": "feed_post",
                                               "campaign": "lifestyle", "caption": "a draft"}).json()["id"]
    before = {p.id: p.status for p in db().query(ContentPost).all()}
    result = plan_week(db(), db().get(PostingRule, rule.id), datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc),
                       rng=random.Random(1), client=fake_claude)
    after = {p.id: p for p in db().query(ContentPost).all()}
    assert after[draft].status == "draft"                                                 # a draft is never promoted
    assert {i for i, p in after.items() if p.status == "scheduled"} == set(approved_ids)  # only approved posts were scheduled
    created = [p for i, p in after.items() if i not in before]
    assert created and all(p.status == "draft" and p.source == "ai" for p in created)     # top-up posts are DRAFTS
    assert not [p for p in after.values() if p.approved_at and p.id in {c.id for c in created}]
    assert len(result.topup_drafts) == len(created) <= get_settings().PLANNER_MAX_TOPUP_PER_DAY


# ---- no secret anywhere: responses, logs, database, push bodies -------------------

def test_no_secret_value_leaks_through_any_channel(phase2, client, app, monkeypatch):
    """Connect via OAuth, fail a publish with a platform error that contains the token, run the API/dashboard
    surface, then scan responses + production-format logs + a full database dump + push bodies."""
    import io
    import logging
    import sqlite3
    from datetime import timedelta
    from types import SimpleNamespace

    import httpx

    import app.platforms as platforms
    import app.services.publishing as pub
    from app.api.dependencies import get_platform_http
    from app.logging_setup import configure_logging
    from app.models import OAuthState
    from app.platforms import PlatformError
    from tests.test_accounts import ig_http
    from tests.test_publish_live import NOON, make_post

    phase2(PUBLISHING_MODE="live", NOTIFY_PROVIDER="ntfy", NTFY_TOPIC="TOPIC-SECRET-XYZ")
    secrets_ = ["LONG-TOKEN-SECRET", "TOPIC-SECRET-XYZ", "LEAKY123", "ig-secret", "media-secret-for-tests",
                get_settings().TOKEN_ENCRYPTION_KEY.get_secret_value()]
    pushed = []
    monkeypatch.setattr(platforms, "http_client", lambda *a, **k: httpx.Client(
        transport=httpx.MockTransport(lambda r: pushed.append(r.content.decode() + str(r.headers)) or httpx.Response(200))))

    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    configure_logging()
    stream = io.StringIO()
    root.handlers[0].setStream(stream)
    bodies = []
    try:
        app.dependency_overrides[get_platform_http] = lambda: ig_http()
        d = db()
        d.add(OAuthState(state="S" * 40, platform="instagram", model_profile_id=1, created_at=NOON))
        d.commit()
        monkeypatch.setattr("app.services.accounts.utcnow", lambda: NOON)
        cb = client.get("/oauth/instagram/callback?code=C&state=" + "S" * 40, follow_redirects=False)
        bodies.append(cb.headers.get("location", ""))
        assert cb.status_code == 302 and "kind=ok" in cb.headers["location"]

        def boom(*a, **k):
            raise PlatformError("bad_request", "Graph said access_token=LEAKY123 for LONG-TOKEN-SECRET", False, 400)
        monkeypatch.setattr(pub, "publisher", lambda p: SimpleNamespace(publish=boom, check_status=boom))
        pid = make_post(client)
        failed = pub.publish_post(db(), pid, now=NOON, http=httpx.Client())
        assert failed.status == "failed"

        for path in ("/api/v1/accounts", f"/api/v1/posts/{pid}", f"/api/v1/posts/{pid}/attempts", "/api/v1/notifications",
                     "/health", "/accounts", "/", "/posts", f"/posts/{pid}", "/engagement", "/planner"):
            r = client.get(path)
            assert r.status_code == 200, path
            bodies.append(r.text)
        bodies.append(client.post(f"/api/v1/posts/{pid}/retry").text)
    finally:
        app.dependency_overrides.pop(get_platform_http, None)
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

    raw = db().get_bind().raw_connection()        # the engine the app really used (not whatever DATABASE_URL says now)
    try:
        dump = "\n".join(raw.driver_connection.iterdump())
    finally:
        raw.close()
    haystacks = {"responses": "\n".join(bodies), "logs": stream.getvalue(), "database": dump, "push bodies": "\n".join(pushed)}
    assert "[redacted]" in dump or "Graph said" in dump                                    # the failure really was recorded
    assert pushed, "a publish_failed push should have been attempted"
    for where, text in haystacks.items():
        for secret in secrets_:
            if where == "push bodies" and secret == "TOPIC-SECRET-XYZ":
                continue                                                                  # the ntfy topic is the provider's URL path, never its body
            assert secret not in text, f"{secret!r} leaked into {where}"
