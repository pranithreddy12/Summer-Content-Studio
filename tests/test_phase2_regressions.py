"""Regression tests for defects found validating Phase 2 B1-B3 (2026-10-22)."""
import os
import random
from collections import Counter
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import clear_settings_cache
from tests.test_planner import TZ, db_session, make_approved

LON = ZoneInfo(TZ)
NOW = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def rules(app):
    from app.models import PostingRule

    db = db_session()
    for p in ("instagram", "tiktok"):
        db.add(PostingRule(model_profile_id=1, platform=p, posts_per_week=10, min_per_day=1, max_per_day=2,
                           window_start="08:00", window_end="21:00", min_gap_minutes=180,
                           timezone=TZ, is_active=True))
    db.commit()
    return {r.platform: r for r in db.query(PostingRule).all()}


@pytest.fixture
def env():
    """Set env vars for one test, restore afterwards."""
    saved = {}

    def setter(**kw):
        for k, v in kw.items():
            saved.setdefault(k, os.environ.get(k))
            os.environ[k] = v
        clear_settings_cache()

    yield setter
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    clear_settings_cache()


def _sent_client(status=200):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


# ---- planner ---------------------------------------------------------------

def test_mirrored_pairs_land_on_same_local_day(app, rules):
    from app.models import ContentPost
    from app.services.planner import plan_week

    for i in range(10):
        make_approved("instagram", group_key=f"g{i}", i=i)
        make_approved("tiktok", group_key=f"g{i}", i=100 + i)
    plan_week(db_session(), rules["instagram"], NOW, rng=random.Random(1))
    plan_week(db_session(), rules["tiktok"], NOW, rng=random.Random(2))
    days = {}
    for p in db_session().query(ContentPost).all():
        days.setdefault(p.group_key, {})[p.platform] = p.planned_publish_at.astimezone(LON).date()
    assert len(days) == 10
    assert all(v["instagram"] == v["tiktok"] for v in days.values()), days


def test_replanning_never_exceeds_posts_per_week(app, rules):
    from app.models import ContentPost
    from app.services.planner import plan_week

    for i in range(4):
        make_approved("instagram", i=i)
    plan_week(db_session(), rules["instagram"], NOW, rng=random.Random(3))
    for i in range(20):
        make_approved("instagram", i=50 + i)
    plan_week(db_session(), rules["instagram"], NOW, rng=random.Random(4))
    plan_week(db_session(), rules["instagram"], NOW, rng=random.Random(5))
    sched = [p for p in db_session().query(ContentPost).all() if p.status == "scheduled"]
    per_day = Counter(p.planned_publish_at.astimezone(LON).date() for p in sched)
    assert len(sched) == 10
    assert max(per_day.values()) <= 2


def test_no_slot_in_last_margin_of_window():
    from app.services.planner import END_MARGIN_MINUTES, make_slots

    rule = SimpleNamespace(posts_per_week=10, min_per_day=1, max_per_day=2, window_start="08:00",
                           window_end="21:00", min_gap_minutes=180, timezone=TZ, enforce_window=True)
    for seed in range(300):
        for s in make_slots(rule, date(2026, 11, 2), NOW, random.Random(seed)):
            lt = s.start_utc.astimezone(LON)
            assert lt.hour * 60 + lt.minute <= 21 * 60 - END_MARGIN_MINUTES


# ---- notifications / reminders ---------------------------------------------

def test_failed_push_is_retried_with_same_key(app, env):
    from app.models import NotificationLog
    from app.services.notifications import notify

    env(NOTIFY_PROVIDER="ntfy", NTFY_TOPIC="TEST-TOPIC")
    bad, _ = _sent_client(500)
    good, seen = _sent_client(200)
    assert notify(db_session(), "test", "retry-key", "t", http=bad) is False
    assert notify(db_session(), "test", "retry-key", "t", http=good) is True
    assert notify(db_session(), "test", "retry-key", "t", http=good) is False  # sent: final
    assert len(seen) == 1
    rows = db_session().query(NotificationLog).filter_by(event_key="retry-key").all()
    assert len(rows) == 1 and rows[0].success is True


def _scheduled_post(caption_i=1):
    from app.models import ContentPost

    pid = make_approved("instagram", i=caption_i)
    db = db_session()
    post = db.get(ContentPost, pid)
    post.status = "scheduled"
    post.hashtags = ["halloween", "spooky"]
    post.planned_publish_at = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    db.commit()
    return pid


def _run_reminders(monkeypatch, http, when):
    import app.services.scheduler as sch

    monkeypatch.setattr(sch, "publishing_mode", lambda: "reminders")
    return sch.run_once(db_session(), when, http=http)


def test_reminder_failed_push_not_marked_then_retried(app, env, monkeypatch):
    from app.models import ContentPost

    env(NOTIFY_PROVIDER="ntfy", NTFY_TOPIC="TEST-TOPIC")
    pid = _scheduled_post()
    when = datetime(2026, 11, 2, 12, 5, tzinfo=timezone.utc)
    bad, _ = _sent_client(500)
    r1 = _run_reminders(monkeypatch, bad, when)
    assert r1["reminded"] == 0
    assert db_session().get(ContentPost, pid).reminded_at is None
    good, seen = _sent_client(200)
    r2 = _run_reminders(monkeypatch, good, when)
    assert r2["reminded"] == 1
    assert db_session().get(ContentPost, pid).reminded_at is not None
    body = seen[0].content.decode()
    assert "#halloween #spooky" in body and "Mark as posted" in body  # full caption in the push
    r3 = _run_reminders(monkeypatch, good, when)
    assert r3["due"] == 0 and len(seen) == 1


def test_reminders_without_push_channel_do_nothing(app, monkeypatch):
    from app.models import ContentPost

    pid = _scheduled_post()
    r = _run_reminders(monkeypatch, None, datetime(2026, 11, 2, 12, 5, tzinfo=timezone.utc))
    assert r["reminded"] == 0 and r["skipped"] == 1
    assert db_session().get(ContentPost, pid).reminded_at is None


def test_blank_credentials_mean_unset(env):
    from app.config import get_settings

    env(NOTIFY_PROVIDER="telegram", TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")
    assert get_settings().notify_configured is False


# ---- dashboard -------------------------------------------------------------

def test_overview_worker_banner_only_when_mode_on_and_worker_dead(app, client, monkeypatch):
    from app.models import ContentPost

    pid = _scheduled_post()
    assert "Scheduler is not running" not in client.get("/").text  # mode off
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "PUBLISHING_MODE", "reminders")
    page = client.get("/").text
    assert "Scheduler is not running" in page and "/cli" not in page
    # fresh heartbeat -> banner gone
    import app.services.scheduler as sch
    from app.db import utcnow

    sch._heartbeat(db_session(), {}, utcnow())
    assert "Scheduler is not running" not in client.get("/").text


def test_post_detail_banner_follows_mode(app, client, monkeypatch):
    from app.config import get_settings

    pid = _scheduled_post()
    assert "NOT published automatically" in client.get(f"/posts/{pid}").text
    monkeypatch.setattr(get_settings(), "PUBLISHING_MODE", "live")
    page = client.get(f"/posts/{pid}").text
    assert "publish automatically while the scheduler runs" in page
    assert "NOT published automatically" not in page
