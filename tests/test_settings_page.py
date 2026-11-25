"""Studio Settings page: values saved in the database override .env, validate, reset, and really change behaviour."""

import pytest

from app.config import get_settings
from app.db import get_session_factory
from app.models import AppSetting
from app.services import AppError, app_settings
from app.services.publishing import check_publishable, preflight_reasons
from app.services.scheduler import due_publish_posts
from tests.test_publish_live import NOON, make_post, post_of


def db():
    return get_session_factory()()


def save(client, **form):
    r = client.post("/settings", data=form, follow_redirects=False)
    assert r.status_code in (302, 303), r.text
    return r


def test_page_renders_every_group(client):
    html = client.get("/settings").text
    for text in ("Publishing mode", "Platforms to publish to", "TikTok delivery", "Plan and draft every day",
                 "Push channel", "Lowest paid-message price"):
        assert text in html
    assert 'href="/settings"' in html  # in the menu


def test_saved_value_overrides_env_and_reset_restores_it(client):
    assert get_settings().PUBLISHING_MODE == "off"            # .env value (tests force off)
    save(client, PUBLISHING_MODE="reminders", PLANNER_RUN_HOUR="9", ENABLED_PLATFORMS=["instagram", "tiktok", "fanvue"])
    assert get_settings().PUBLISHING_MODE == "reminders" and get_settings().PLANNER_RUN_HOUR == 9
    assert {r.key for r in db().query(AppSetting)} >= {"PUBLISHING_MODE", "PLANNER_RUN_HOUR"}
    client.post("/settings/reset", data={"key": "PUBLISHING_MODE"})
    assert get_settings().PUBLISHING_MODE == "off" and get_settings().PLANNER_RUN_HOUR == 9
    client.post("/settings/reset")
    assert get_settings().PLANNER_RUN_HOUR == 6 and db().query(AppSetting).count() == 0


def test_unticked_checkbox_turns_a_switch_off(client):
    save(client, AUTO_PLAN_ENABLED="on", AUTO_SCHEDULE_ON_APPROVE="on", ENABLED_PLATFORMS=["instagram"])
    assert get_settings().AUTO_PLAN_ENABLED and get_settings().AUTO_SCHEDULE_ON_APPROVE
    save(client, AUTO_SCHEDULE_ON_APPROVE="on", ENABLED_PLATFORMS=["instagram"])   # AUTO_PLAN_ENABLED not sent = unticked
    assert not get_settings().AUTO_PLAN_ENABLED and get_settings().AUTO_SCHEDULE_ON_APPROVE


@pytest.mark.parametrize("form", [
    {"PLANNER_RUN_HOUR": "99"}, {"PLANNER_RUN_HOUR": "abc"}, {"PUBLISHING_MODE": "yolo"},
    {"FANVUE_PPV_MIN_CENTS": "9000", "FANVUE_PPV_MAX_CENTS": "500"},
    {"PUBLISHING_MODE": "live"},                      # tests have no PUBLIC_BASE_URL / keys: refused
    {"ENABLED_PLATFORMS": ["myspace"]},
])
def test_bad_values_change_nothing(client, form):
    before = get_settings().model_dump()
    save(client, **form)
    assert db().query(AppSetting).count() == 0 and get_settings().model_dump() == before


def test_secrets_are_not_editable_here(client):
    html = client.get("/settings").text
    assert "TIKTOK_CLIENT_SECRET" not in html and "TOKEN_ENCRYPTION_KEY" not in html
    save(client, TIKTOK_CLIENT_SECRET="hacked", DASHBOARD_PASSWORD="x", ENABLED_PLATFORMS=["instagram"])
    assert not {r.key for r in db().query(AppSetting)} & {"TIKTOK_CLIENT_SECRET", "DASHBOARD_PASSWORD"}


def test_switching_a_platform_off_stops_its_posts(client):
    pid = make_post(client, status="scheduled")                   # instagram reel, due at NOON
    assert not any(r.startswith("platform_disabled") for r in preflight_reasons(db(), post_of(pid), NOON))
    assert [p.id for p in due_publish_posts(db(), NOON)] == [pid]
    save(client, ENABLED_PLATFORMS=["tiktok", "fanvue"])          # instagram unticked
    assert any(r.startswith("platform_disabled") for r in preflight_reasons(db(), post_of(pid), NOON))
    with pytest.raises(AppError) as e:
        check_publishable(db(), post_of(pid), now=NOON)
    assert e.value.code == "platform_disabled"
    assert due_publish_posts(db(), NOON) == []                     # the worker skips it, nothing is marked failed
    assert post_of(pid).status == "scheduled"


def test_worker_process_sees_a_saved_change(client):
    """The worker has its own process: it re-reads the table (cache expires), it is not told."""
    import app.config as config

    app_settings.save(db(), {"PLANNER_RUN_HOUR": "11", "ENABLED_PLATFORMS": ["instagram"]})
    config._OVERRIDES.update(value={}, at=0.0)                      # a stale copy, as in another process
    assert get_settings().PLANNER_RUN_HOUR == 11                    # TTL passed -> re-read from the table


ALL = ["instagram", "tiktok", "fanvue"]


def test_fully_automatic_platform_approves_and_schedules_new_drafts(client, halloween_asset, fake_claude):
    """AUTO_APPROVE_PLATFORMS: the planner's own AI drafts for that platform go straight to scheduled (no human);
    the draft push is replaced by an 'auto-approved' one. A platform that is not ticked keeps its drafts as drafts."""
    import random
    from datetime import datetime, timezone

    from app.models import ContentPost, PostingRule

    from tests.test_telegram_approvals import GIF

    for i in range(12):                                                      # every run uses up to 3 unused assets
        client.post("/api/v1/assets/upload", files={"file": (f"g{i}.gif", GIF + bytes([i]), "image/gif")})
    d = db()
    rule = PostingRule(model_profile_id=1, platform="instagram", posts_per_week=10, min_per_day=1, max_per_day=2,
                       window_start="08:00", window_end="21:00", min_gap_minutes=180, timezone="Europe/London",
                       is_active=True, auto_topup=True)
    d.add(rule)
    d.commit()
    from app.services.planner import plan_week

    def run():
        before = {p.id for p in db().query(ContentPost)}
        plan_week(db(), db().get(PostingRule, rule.id), datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc),
                  rng=random.Random(1), client=fake_claude)
        return [p for p in db().query(ContentPost) if p.id not in before]

    manual = run()
    assert manual and all(p.status == "draft" for p in manual)               # default: a human approves
    save(client, AUTO_APPROVE_PLATFORMS=["instagram"], ENABLED_PLATFORMS=ALL)
    auto = run()
    assert auto and all(p.source == "ai" and p.status == "scheduled" and p.planned_publish_at for p in auto)
    assert all(p.status == "draft" for p in db().query(ContentPost).filter(ContentPost.id.in_([m.id for m in manual])))
    save(client, AUTO_APPROVE_PLATFORMS=["tiktok"], ENABLED_PLATFORMS=ALL)     # a different platform ticked
    assert all(p.status == "draft" for p in run())


def test_draft_and_published_pushes_can_be_switched_off(client, fake_claude, monkeypatch):
    import app.services.notifications as notifications
    from tests.test_telegram_approvals import GIF, URL

    sent = []
    monkeypatch.setattr(notifications, "notify", lambda db, event, *a, **k: sent.append(event) or True)
    client.post("/api/v1/assets/upload", files={"file": ("a.gif", GIF, "image/gif")})
    client.post("/api/v1/assets/upload", files={"file": ("b.gif", GIF + b"1", "image/gif")})
    assert client.post(URL, json={"platform": "instagram", "count": 1}).status_code == 201
    assert sent == ["draft_ready"]
    save(client, ENABLED_PLATFORMS=ALL)                                        # NOTIFY_NEW_DRAFTS not sent = unticked
    sent.clear()
    assert client.post(URL, json={"platform": "instagram", "count": 1}).status_code == 201
    assert sent == [] and not get_settings().NOTIFY_NEW_DRAFTS and not get_settings().NOTIFY_PUBLISHED


# ---- change log, pause button, quiet days, daily cap, approve-after-hours -------------------------------------

def test_every_change_is_logged_and_shown(client):
    save(client, PLANNER_RUN_HOUR="8", ENABLED_PLATFORMS=ALL)
    client.post("/settings/reset", data={"key": "PLANNER_RUN_HOUR"})
    log = [(c.key, c.old, c.new) for c in app_settings.changes(db())]
    assert ("PLANNER_RUN_HOUR", "6", "8") in log
    assert any(k == "PLANNER_RUN_HOUR" and "server value" in n for k, o, n in log)
    page = client.get("/settings").text
    assert "Recent changes" in page and "PLANNER_RUN_HOUR" in page


def test_pause_and_resume_put_the_mode_back(client):
    save(client, PUBLISHING_MODE="reminders", ENABLED_PLATFORMS=ALL)
    assert "Pause posting" in client.get("/settings").text
    client.post("/settings/pause")
    assert get_settings().PUBLISHING_MODE == "off" and app_settings.paused(db())
    assert "Resume" in client.get("/settings").text
    client.post("/settings/resume")
    assert get_settings().PUBLISHING_MODE == "reminders" and not app_settings.paused(db())
    client.post("/settings/reset")
    client.post("/settings/pause")                                   # already off: refused, nothing stored
    assert not app_settings.paused(db()) and get_settings().PUBLISHING_MODE == "off"


def test_quiet_day_is_outside_the_window_and_gets_no_slots(client):
    import random
    from datetime import date, datetime, timezone

    from app.services.planner import make_slots
    from app.services.window import in_window
    from tests.test_planner import lee_rule

    sunday_noon = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    monday_noon = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    assert in_window(lee_rule(), sunday_noon) and in_window(lee_rule(), monday_noon)
    save(client, NO_POST_WEEKDAYS=["sun"], ENABLED_PLATFORMS=ALL)
    assert not in_window(lee_rule(), sunday_noon) and in_window(lee_rule(), monday_noon)
    slots = make_slots(lee_rule(), date(2026, 10, 26), datetime(2026, 10, 21, tzinfo=timezone.utc), random.Random(3))
    assert slots and all(s.local_day.weekday() != 6 for s in slots)


def test_daily_cap_delays_the_extra_posts(client):
    a, b = make_post(client, status="scheduled"), make_post(client, status="scheduled")
    assert len(due_publish_posts(db(), NOON)) == 2
    save(client, MAX_POSTS_PER_DAY="1", ENABLED_PLATFORMS=ALL)
    assert len(due_publish_posts(db(), NOON)) == 1
    s = db()
    first = s.get(type(post_of(a)), a)
    first.status, first.published_at = "published", NOON                  # one went out today
    s.commit()
    assert due_publish_posts(db(), NOON) == []                            # cap reached
    from datetime import timedelta
    assert [p.id for p in due_publish_posts(db(), NOON + timedelta(days=1))] == [b]   # next day: the extra one goes


def test_unanswered_automatic_drafts_are_approved_after_the_set_hours(client, halloween_asset, fake_claude):
    import random
    from datetime import datetime, timedelta, timezone

    from app.db import utcnow
    from app.models import ContentPost, PostingRule
    from app.services.planner import approve_stale_drafts, plan_week
    from tests.test_telegram_approvals import GIF

    for i in range(4):
        client.post("/api/v1/assets/upload", files={"file": (f"s{i}.gif", GIF + bytes([i + 40]), "image/gif")})
    d = db()
    rule = PostingRule(model_profile_id=1, platform="instagram", posts_per_week=10, min_per_day=1, max_per_day=2,
                       window_start="08:00", window_end="21:00", min_gap_minutes=180, timezone="Europe/London",
                       is_active=True, auto_topup=True)
    d.add(rule)
    d.commit()
    plan_week(db(), db().get(PostingRule, rule.id), datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc),
              rng=random.Random(1), client=fake_claude)
    drafts = [p.id for p in db().query(ContentPost).filter(ContentPost.source == "ai")]
    assert drafts
    later = utcnow() + timedelta(hours=3)
    assert approve_stale_drafts(db(), later) == 0                          # setting is 0 = off
    save(client, AUTO_APPROVE_AFTER_HOURS="2", ENABLED_PLATFORMS=ALL)
    assert approve_stale_drafts(db(), utcnow() + timedelta(hours=1)) == 0  # too young
    assert approve_stale_drafts(db(), later) == len(drafts)
    assert {p.status for p in db().query(ContentPost).filter(ContentPost.id.in_(drafts))} <= {"approved", "scheduled"}


# ---- draft-writing options ---------------------------------------------------------------------------------

def test_ai_client_follows_the_model_chosen_on_the_page(client):
    from app.api.dependencies import get_claude_client

    first = get_claude_client()
    assert first.settings.CLAUDE_MODEL != "claude-haiku-5-5" and get_claude_client() is first     # reused while nothing changes
    save(client, CLAUDE_MODEL="claude-haiku-5-5", CLAUDE_EFFORT="low", LLM_PROVIDER="mistral", ENABLED_PLATFORMS=ALL)
    now = get_claude_client()
    assert now is not first and (now.settings.CLAUDE_MODEL, now.settings.CLAUDE_EFFORT, now.settings.LLM_PROVIDER) == (
        "claude-haiku-5-5", "low", "mistral")


def test_a_custom_model_from_the_server_survives_saving_the_page(env, client):
    env(CLAUDE_MODEL="my-custom-model")
    page = client.get("/settings").text
    assert "my-custom-model (set on the server)" in page
    save(client, CLAUDE_MODEL="my-custom-model", ENABLED_PLATFORMS=ALL)
    assert get_settings().CLAUDE_MODEL == "my-custom-model" and db().query(AppSetting).filter_by(key="CLAUDE_MODEL").count() == 0
    save(client, CLAUDE_MODEL="not-a-listed-model", ENABLED_PLATFORMS=ALL)                    # typed junk is still refused
    assert get_settings().CLAUDE_MODEL == "my-custom-model"


def test_automatic_drafts_follow_the_media_options(client, monkeypatch):
    import random
    from datetime import date

    from app.models import PostingRule
    from app.services import random_draft
    from app.services.planner import plan_week

    s = db()
    rules = {p: PostingRule(model_profile_id=1, platform=p, posts_per_week=7, min_per_day=1, max_per_day=1,
                            window_start="08:00", window_end="21:00", min_gap_minutes=180, timezone="UTC",
                            is_active=True, auto_topup=True) for p in ("instagram", "tiktok")}
    s.add_all(rules.values())
    s.commit()
    asked = []
    monkeypatch.setattr(random_draft, "generate_random_drafts", lambda db_, req, c, **kw: asked.append(req) or {"posts": []})

    def top_up(platform):
        asked.clear()
        plan_week(s, rules[platform], datetime(2031, 3, 1, 20, 0, tzinfo=timezone.utc), rng=random.Random(1),
                  start_day=date(2031, 3, 3), client=object())
        assert asked
        return asked[0]

    from datetime import datetime, timezone
    first = top_up("instagram")
    assert first.media_type is None and not first.include_used and not first.both_platforms        # defaults
    save(client, AUTO_DRAFT_MEDIA="image", AUTO_DRAFT_INCLUDE_USED="on", AUTO_DRAFT_BOTH_PLATFORMS="on", ENABLED_PLATFORMS=ALL)
    ig = top_up("instagram")
    assert ig.media_type.value == "image" and ig.include_used and ig.both_platforms
    tt = top_up("tiktok")
    assert tt.media_type.value == "video" and tt.include_used and not tt.both_platforms           # TikTok: video only, no twin of a twin
