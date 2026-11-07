"""Worker (PLAN_PHASE2 §9): stuck-claim recovery, token refresh, planner tick, stage isolation,
graceful loop shutdown, CLI guards. Platform calls are stubbed / MockTransport."""
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from click.testing import CliRunner

import app.services.publishing as pub
import app.services.scheduler as sch
from app.models import ContentPost, NotificationLog, PlatformAccount, PostingRule, PublishAttempt
from app.platforms import PlatformError, PublishResult
from app.services import accounts
from tests.test_accounts import ig_http
from tests.test_publish_live import (NOON, JPG, MP4, NoHttp, db, live, make_account, make_post,  # noqa: F401
                                     post_of)


@pytest.fixture(autouse=True)
def fresh_throttle():
    sch._refresh_tried.clear()


def stub_platform(monkeypatch, check=None, publish_calls=None, tiktok_inbox=False):
    """pub.publisher -> object with publish/check_status stubs."""
    def publish(*a, **k):
        if publish_calls is not None:
            publish_calls.append(1)
        return PublishResult("EXT-NEW", None, NOON, "REF-NEW")

    def check_status(http, token, uid, ref):
        if isinstance(check, Exception):
            raise check
        return check(ref) if callable(check) else check
    monkeypatch.setattr(pub, "publisher", lambda platform: SimpleNamespace(publish=publish, check_status=check_status))


def stuck_post(client, ref="C1", claimed_ago=timedelta(minutes=20), platform="instagram", content_type="reel"):
    pid = make_post(client, platform, content_type)
    d = db()
    post = d.get(ContentPost, pid)
    post.status, post.claimed_at, post.publish_attempt_count = "publishing", NOON - claimed_ago, 1
    acct = d.query(PlatformAccount).filter_by(platform=platform).first()
    d.add(PublishAttempt(post_id=pid, account_id=acct.id if acct else None, attempt_no=1, trigger="schedule",
                         started_at=NOON - claimed_ago, platform_ref=ref))
    d.commit()
    return pid


def run(now=NOON, http=None):
    return sch.run_once(db(), now, http=http or NoHttp())


# ---- stuck-claim recovery --------------------------------------------------

def test_stuck_post_that_actually_went_out_is_marked_published_without_republishing(live, monkeypatch):
    calls = []
    stub_platform(monkeypatch, check=lambda ref: PublishResult("MEDIA-9", "https://ig/p/9", None, ref), publish_calls=calls)
    pid = stuck_post(live)
    r = run()
    assert r["stuck"] == {"published": 1, "retry": 0, "failed": 0} and calls == []
    post = post_of(pid)
    assert (post.status, post.published_via, post.external_post_id, post.claimed_at) == ("published", "api", "MEDIA-9", None)
    att = db().query(PublishAttempt).one()
    assert att.success is True and att.finished_at is not None


def test_stuck_tiktok_that_reached_the_inbox_becomes_sent_to_drafts(phase2, client, monkeypatch):
    phase2(PUBLISHING_MODE="live")
    make_account("tiktok")
    stub_platform(monkeypatch, check=lambda ref: PublishResult(ref, None, None, ref))
    pid = stuck_post(client, ref="v_pub1", platform="tiktok", content_type="video")
    run()
    assert post_of(pid).status == "sent_to_drafts"
    assert db().query(NotificationLog).filter_by(event_key=f"tiktok:{pid}").count() == 1


def test_stuck_post_the_platform_does_not_know_is_rescheduled_not_published(live, monkeypatch):
    calls = []
    stub_platform(monkeypatch, check=None, publish_calls=calls)
    pid = stuck_post(live)
    r = run()
    assert r["stuck"]["retry"] == 1 and calls == []                   # recovery itself never publishes
    post = post_of(pid)
    assert post.status == "scheduled" and post.claimed_at is None
    assert post.next_attempt_at == NOON + timedelta(minutes=5)         # first backoff, inside the window
    att = db().query(PublishAttempt).one()
    assert att.success is False and att.error_code == "timeout" and att.platform_ref == "C1"
    # the normal due-post path publishes it once the retry time arrives
    later = run(NOON + timedelta(minutes=6))
    assert later["published"] == 1 and calls == [1] and post_of(pid).status == "published"


def test_stuck_post_without_a_reference_is_retried_without_asking_the_platform(live, monkeypatch):
    checked = []
    stub_platform(monkeypatch, check=lambda ref: checked.append(ref))
    pid = stuck_post(live, ref=None)
    run()
    assert checked == [] and post_of(pid).status == "scheduled"


def test_a_claim_that_is_not_old_enough_is_left_alone(live, monkeypatch):
    stub_platform(monkeypatch, check=lambda ref: pytest.fail("must not look at a fresh claim"))
    pid = stuck_post(live, claimed_ago=timedelta(minutes=5))
    r = run()
    assert r["stuck"] == {"published": 0, "retry": 0, "failed": 0} and post_of(pid).status == "publishing"


def test_stuck_recovery_exhausts_retries_into_failed_with_a_push(live, monkeypatch):
    stub_platform(monkeypatch, check=None)
    pid = stuck_post(live)
    d = db()
    d.get(ContentPost, pid).publish_attempt_count = 3               # already used every attempt
    d.commit()
    r = run()
    assert r["stuck"]["failed"] == 1 and post_of(pid).status == "failed"
    assert db().query(NotificationLog).filter_by(event="publish_failed").count() == 1


def test_stuck_check_crash_is_a_generic_transient_error(live, monkeypatch):
    stub_platform(monkeypatch, check=RuntimeError("TOKEN-SECRET-A exploded"))
    pid = stuck_post(live)
    run()
    post = post_of(pid)
    assert post.status == "scheduled" and "TOKEN-SECRET-A" not in (post.last_publish_error or "")


def test_stuck_post_with_disconnected_account_fails_visibly(live, monkeypatch):
    stub_platform(monkeypatch, check=None)
    pid = stuck_post(live)
    d = db()
    d.query(PlatformAccount).one().status = "revoked"
    d.commit()
    run()
    assert post_of(pid).status == "scheduled"                          # recovery re-queued it (nothing reached the platform)
    run(NOON + timedelta(minutes=6))                                   # the retry hits the dead account: visible failure
    post = post_of(pid)
    assert post.status == "failed" and post.last_publish_error.startswith("account_not_connected")
    assert db().query(NotificationLog).filter_by(event="publish_failed").count() == 1


@pytest.mark.parametrize("mode", ["dry_run", "reminders"])
def test_recovery_only_runs_in_live_mode(phase2, client, monkeypatch, mode):
    phase2(PUBLISHING_MODE=mode, NOTIFY_PROVIDER="ntfy", NTFY_TOPIC="t")
    stub_platform(monkeypatch, check=lambda ref: pytest.fail("not in this mode"))
    pid = stuck_post(client)
    assert "stuck" not in run() and post_of(pid).status == "publishing"


# ---- token refresh ---------------------------------------------------------

def add_account(platform="instagram", expires=NOON + timedelta(days=3), refresh_token=None):
    d = db()
    d.add(PlatformAccount(model_profile_id=1, platform=platform, external_user_id="999", username="summer",
                          access_token_enc=accounts.encrypt("OLD"), token_expires_at=expires, scopes=[],
                          refresh_token_enc=accounts.encrypt(refresh_token) if refresh_token else None,
                          status="connected"))
    d.commit()


def test_instagram_token_inside_seven_days_is_refreshed_others_are_not(phase2, client):
    phase2(PUBLISHING_MODE="live")
    add_account("instagram", NOON + timedelta(days=3))
    add_account("tiktok", NOON + timedelta(days=3))                    # tiktok window is 24 h, so untouched
    r = run(http=ig_http())
    assert r["tokens"] == {"refreshed": 1, "failed": 0}
    ig = db().query(PlatformAccount).filter_by(platform="instagram").one()
    assert accounts.decrypt(ig.access_token_enc) == "REFRESHED-TOKEN" and ig.token_expires_at > NOON + timedelta(days=30)
    assert accounts.decrypt(db().query(PlatformAccount).filter_by(platform="tiktok").one().access_token_enc) == "OLD"


def test_far_from_expiry_is_not_refreshed(phase2, client):
    phase2(PUBLISHING_MODE="live")
    add_account("instagram", NOON + timedelta(days=40))
    assert run(http=NoHttp())["tokens"] == {"refreshed": 0, "failed": 0}


def test_rejected_refresh_expires_the_account_and_alerts(phase2, client):
    phase2(PUBLISHING_MODE="live")
    add_account("instagram", NOON + timedelta(days=2))
    r = run(http=ig_http(refresh_status=400))
    assert r["tokens"]["failed"] == 1
    assert db().query(PlatformAccount).one().status == "expired"
    assert db().query(NotificationLog).filter_by(event="account_attention").count() == 1


def test_failed_refresh_is_not_retried_every_loop(phase2, client):
    phase2(PUBLISHING_MODE="live")
    add_account("instagram", NOON + timedelta(days=2))
    seen = []

    def flaky(req):
        seen.append(req.url.path)
        return httpx.Response(503)
    http = httpx.Client(transport=httpx.MockTransport(flaky))
    assert run(NOON, http)["tokens"]["failed"] == 1
    assert run(NOON + timedelta(minutes=1), http)["tokens"] == {"refreshed": 0, "failed": 0}   # throttled
    assert run(NOON + timedelta(minutes=31), http)["tokens"]["failed"] == 1                    # tried again later
    assert len(seen) == 2


def test_no_token_refresh_outside_live_mode(phase2, client):
    phase2(PUBLISHING_MODE="dry_run")
    add_account("instagram", NOON + timedelta(days=1))
    assert "tokens" not in run(http=NoHttp())


# ---- planner tick ----------------------------------------------------------

def add_rules(active=True):
    d = db()
    for p in ("instagram", "tiktok"):
        d.add(PostingRule(model_profile_id=1, platform=p, posts_per_week=10, min_per_day=1, max_per_day=2,
                          window_start="08:00", window_end="21:00", min_gap_minutes=180,
                          timezone="Europe/London", is_active=active))
    d.commit()


def approved(client, n):
    ids = []
    for _ in range(n):
        pid = make_post(client, status="approved", planned=None)
        ids.append(pid)
    return ids


def scheduled_count():
    return db().query(ContentPost).filter(ContentPost.status == "scheduled").count()


def test_planner_runs_once_per_local_day_after_the_run_hour(phase2, client):
    phase2(PUBLISHING_MODE="dry_run", AUTO_PLAN_ENABLED="true", PLANNER_RUN_HOUR="6")
    add_rules()
    approved(client, 4)
    early = datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)           # 05:00 local < 06:00
    assert "planned" not in run(early, NoHttp())["planner"] or run(early, NoHttp())["planner"]["planned"] == 0
    assert scheduled_count() == 0
    t = datetime(2026, 11, 2, 6, 5, tzinfo=timezone.utc)
    r = run(t, NoHttp())
    assert r["planner"]["planned"] == 2 and r["planner"]["filled"] == 4 and scheduled_count() == 4   # IG + TikTok rules; posts are IG
    assert run(t + timedelta(minutes=1), NoHttp())["planner"]["planned"] == 0                         # same day: nothing
    approved(client, 1)
    assert run(t + timedelta(hours=5), NoHttp())["planner"]["planned"] == 0 and scheduled_count() == 4
    nxt = run(t + timedelta(days=1), NoHttp())                                                        # next local day: runs again
    assert nxt["planner"]["planned"] == 2 and scheduled_count() == 5


def test_planner_state_survives_a_restart(phase2, client):
    phase2(PUBLISHING_MODE="dry_run", AUTO_PLAN_ENABLED="true")
    add_rules()
    approved(client, 2)
    t = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)
    run(t, NoHttp())
    hb = db().query(sch.WorkerHeartbeat).one()
    assert set(hb.last_result["planner_ran"].values()) == {"2026-11-02"}
    # a brand new process has no memory except the heartbeat row: it must not re-plan today
    assert run(t + timedelta(minutes=5), NoHttp())["planner"]["planned"] == 0
    # ... and state is carried through loops where the planner does not run
    phase2(AUTO_PLAN_ENABLED="false")
    run(t + timedelta(minutes=10), NoHttp())
    assert db().query(sch.WorkerHeartbeat).one().last_result["planner_ran"]


def test_planner_is_off_unless_enabled_and_never_in_off_mode(phase2, client):
    add_rules()
    approved(client, 2)
    phase2(PUBLISHING_MODE="dry_run")                                  # AUTO_PLAN_ENABLED defaults to false
    assert "planner" not in run(datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc), NoHttp())
    phase2(PUBLISHING_MODE="off", AUTO_PLAN_ENABLED="true")
    assert "planner" not in run(datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc), NoHttp())
    assert scheduled_count() == 0


def test_inactive_rules_are_not_planned(phase2, client):
    phase2(PUBLISHING_MODE="dry_run", AUTO_PLAN_ENABLED="true")
    add_rules(active=False)
    approved(client, 2)
    assert run(datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc), NoHttp())["planner"]["planned"] == 0


def test_planner_failure_is_contained_and_not_retried_every_minute(phase2, client, monkeypatch):
    phase2(PUBLISHING_MODE="dry_run", AUTO_PLAN_ENABLED="true")
    add_rules()
    import app.services.planner as planner
    tries = []
    monkeypatch.setattr(planner, "plan_week", lambda *a, **k: tries.append(1) or (_ for _ in ()).throw(RuntimeError("x")))
    t = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)
    r = run(t, NoHttp())
    assert r["planner"]["planned"] == 0 and "errors" not in r
    run(t + timedelta(minutes=1), NoHttp())
    assert len(tries) == 2                                              # once per rule (IG + TikTok), then marked done for today


# ---- stage isolation + heartbeat -------------------------------------------

def test_one_failing_stage_does_not_stop_the_others_or_the_heartbeat(live, monkeypatch):
    stub_platform(monkeypatch)
    monkeypatch.setattr(sch, "publish_due", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    called = []
    monkeypatch.setattr(sch, "refresh_due_tokens", lambda *a, **k: called.append(1) or {"refreshed": 0, "failed": 0})
    r = run()
    assert r["errors"] == ["publish"] and called == [1] and r["tokens"] == {"refreshed": 0, "failed": 0}
    assert db().query(sch.WorkerHeartbeat).one().last_result["errors"] == ["publish"]


def test_heartbeat_makes_worker_ok_in_health_and_goes_stale(phase2, client):
    phase2(PUBLISHING_MODE="dry_run")
    assert client.get("/health").json()["worker_ok"] is False
    from app.db import utcnow
    run(utcnow(), NoHttp())
    h = client.get("/health").json()
    assert h["worker_ok"] is True and h["worker_last_seen"]
    d = db()
    d.query(sch.WorkerHeartbeat).one().last_seen = utcnow() - timedelta(minutes=6)
    d.commit()
    assert client.get("/health").json()["worker_ok"] is False


# ---- loop + shutdown -------------------------------------------------------

class Factory:
    """session factory counting sessions closed (no leaks) - wraps the real one."""

    def __init__(self):
        from app.db import get_session_factory
        self.real, self.opened, self.closed = get_session_factory(), 0, 0

    def __call__(self):
        self.opened += 1
        s = self.real()
        orig = s.close
        s.close = lambda: (setattr(self, "closed", self.closed + 1), orig())[1]
        return s


def test_run_forever_stops_when_asked_and_finishes_the_iteration(phase2, client):
    stop, lines, f = threading.Event(), [], Factory()

    def echo(line):
        lines.append(line)
        if len(lines) == 3:
            stop.set()
    assert sch.run_forever(stop, 0, echo=echo, session_factory=f) == 3
    assert f.opened == f.closed == 3


def test_run_forever_once(phase2, client):
    assert sch.run_forever(threading.Event(), 3600, once=True, echo=lambda s: None) == 1


def test_sleep_is_interruptible(phase2, client):
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    t0 = time.monotonic()
    sch.run_forever(stop, 3600, echo=lambda s: None)                   # would sleep an hour without the event
    assert time.monotonic() - t0 < 5


def test_a_crashing_iteration_does_not_kill_the_loop(phase2, client, monkeypatch):
    stop, lines, n = threading.Event(), [], {"i": 0}

    def flaky(db, now, http=None):
        n["i"] += 1
        if n["i"] <= 2:
            raise RuntimeError("secret internals")
        return {"ok": True}
    monkeypatch.setattr(sch, "run_once", flaky)

    def echo(line):
        lines.append(line)
        if n["i"] == 3:
            stop.set()
    assert sch.run_forever(stop, 0, echo=echo) == 3
    assert lines[:2] == ["worker iteration failed: RuntimeError"] * 2 and "secret" not in " ".join(lines)


# ---- CLI guards ------------------------------------------------------------

def cli_run(args, **env):
    import os
    from app.cli import cli
    from app.config import clear_settings_cache
    for k, v in env.items():
        os.environ[k] = v
    clear_settings_cache()
    try:
        return CliRunner().invoke(cli, args)
    finally:
        for k in env:
            os.environ.pop(k, None)
        clear_settings_cache()


def test_worker_refuses_live_without_prerequisites(app):
    r = cli_run(["worker", "--once"], PUBLISHING_MODE="live")
    assert r.exit_code == 1 and "Refusing to start" in r.output


def test_worker_refuses_reminders_without_a_push_channel(app):
    r = cli_run(["worker", "--once"], PUBLISHING_MODE="reminders")
    assert r.exit_code == 1 and "push channel" in r.output


def test_worker_once_runs_and_stops_cleanly(app):
    r = cli_run(["worker", "--once"], PUBLISHING_MODE="dry_run")
    assert r.exit_code == 0, r.output
    assert "Worker started" in r.output and "'mode': 'dry_run'" in r.output and "stopped cleanly" in r.output


# ---- persistent pre-flight problems are visible, not silently skipped ----------

def test_live_post_without_an_account_fails_visibly_and_can_be_retried(phase2, client, monkeypatch):
    phase2(PUBLISHING_MODE="live")
    stub_platform(monkeypatch)
    pid = make_post(client)                                            # scheduled, due, NO account connected
    r = run()
    assert (r["failed"], r["skipped"]) == (1, 0)
    post = post_of(pid)
    assert post.status == "failed" and post.last_publish_error.startswith("account_not_connected")
    att = db().query(PublishAttempt).one()
    assert (att.success, att.error_code, att.trigger) == (False, "account_not_connected", "schedule")
    assert db().query(NotificationLog).filter_by(event="publish_failed").count() == 1
    assert run(NOON + timedelta(minutes=1))["due"] == 0                # no repeat push / attempt every minute
    make_account()                                                     # user connects the account ...
    assert pub.retry_post(db(), pid, NOON + timedelta(minutes=2)).status == "scheduled"   # ... and clicks Retry
    assert run(NOON + timedelta(minutes=3))["published"] == 1


def test_dry_run_never_fails_posts_for_preflight_problems(phase2, client):
    phase2(PUBLISHING_MODE="dry_run")
    pid = make_post(client, "tiktok", "video", status="scheduled")
    pid2 = make_post(client)
    d = db()
    d.get(ContentPost, pid2).caption = "x" * 3000                     # caption_too_long
    d.commit()
    r = run()
    assert r["failed"] == 0 and post_of(pid2).status == "scheduled"


# ---- the posting window holds in EVERY mode -----------------------------------

@pytest.mark.parametrize("mode", ["dry_run", "reminders", "live"])
def test_nothing_happens_outside_the_window_in_any_mode(phase2, client, monkeypatch, mode):
    phase2(PUBLISHING_MODE=mode, NOTIFY_PROVIDER="ntfy", NTFY_TOPIC="t")
    make_account()
    stub_platform(monkeypatch, check=None)
    pid = make_post(client, planned=datetime(2026, 11, 2, 19, 0, tzinfo=timezone.utc))
    for hour, minute in ((21, 1), (23, 30), (3, 0)):                   # 21:01 local ... 03:00 next day
        day = 2 if hour > 12 else 3
        r = sch.run_once(db(), datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc), http=NoHttp())
        assert r["due"] == 0, (mode, hour, minute)
    assert post_of(pid).status == "scheduled" and post_of(pid).reminded_at is None
    assert db().query(PublishAttempt).count() == 0 and db().query(NotificationLog).count() == 0
    # ... and the very first minute of the next window does act
    r = sch.run_once(db(), datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc), http=NoHttp() if mode != "reminders" else
                     httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200))))
    assert r["due"] == 1


def test_only_one_worker_per_database(tmp_path):
    """Two workers would both refresh the rotating Fanvue token -> Fanvue revokes it (PLAN_PHASE3 P3-5)."""
    from types import SimpleNamespace

    from app.cli import worker_lock

    s = SimpleNamespace(DATABASE_URL=f"sqlite:///{tmp_path}/app.db")
    first = worker_lock(s)
    assert first is not None
    assert worker_lock(s) is None          # a second worker is refused
    first.close()                          # first worker exits (or crashes: the OS frees the lock)
    again = worker_lock(s)
    assert again is not None
    again.close()
