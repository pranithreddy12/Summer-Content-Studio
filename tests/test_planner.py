"""Planner (PLAN_PHASE2 §9a/§16): property tests over many seeds for
make_slots; plan_week behaviour incl. idempotency, oldest-first, mirroring,
validation, deduped unfilled push."""

import random
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.config import get_settings
from app.services import AppError
from app.services.planner import (
    OCCUPIED_STATUSES, PlanResult, Slot, make_slots, plan_week, validate_rule_values,
)
from app.services.window import in_window
from app.db import get_session_factory

TZ = "Europe/London"


def lee_rule(active=True):
    return SimpleNamespace(
        model_profile_id=1, platform="instagram", posts_per_week=10,
        min_per_day=1, max_per_day=2, window_start="08:00", window_end="21:00",
        enforce_window=True, min_gap_minutes=180, timezone=TZ,
        auto_topup=False, is_active=active,
    )


# --------------------------------------------------------------------------
# make_slots property tests over many seeds
# --------------------------------------------------------------------------

def test_property_slots_over_many_seeds():
    rule = lee_rule()
    now = datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)  # far before the week
    week_start = date(2026, 10, 26)  # Monday after the DST change
    for seed in range(25):
        rng = random.Random(seed)
        slots = make_slots(rule, week_start, now, rng)
        assert len(slots) == rule.posts_per_week, f"seed {seed}: {len(slots)}"
        per_day = {}
        tz = ZoneInfo(TZ)
        for order, slot in enumerate(slots):
            slot_local = slot.start_utc.astimezone(tz)
            assert slot.local_day == slot_local.date()
            per_day.setdefault(slot.local_day, []).append(slot_local)
            # strictly inside 08:00-21:00 (end inclusive at the exact minute)
            assert "08:00" <= slot_local.strftime("%H:%M") <= "21:00"
            assert in_window(rule, slot.start_utc) is True
            assert slot.start_utc >= now + timedelta(minutes=get_settings().PLANNER_LEAD_MINUTES)
        # every day >= min, <= max; exactly 3 days have 2 slots (Lee's pattern)
        assert len(per_day) == 7
        assert all(len(v) >= rule.min_per_day for v in per_day.values())
        assert all(len(v) <= rule.max_per_day for v in per_day.values())
        doubles = [d for d, v in per_day.items() if len(v) == 2]
        assert len(doubles) == 3, f"seed {seed}: {len(doubles)}"
        for v in per_day.values():
            v.sort()
            for a, b in zip(v, v[1:]):
                assert (b - a).total_seconds() >= rule.min_gap_minutes * 60


def test_same_seed_same_slots_different_seeds_differ():
    rule = lee_rule()
    now = datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)
    week = date(2026, 10, 26)
    a = make_slots(rule, week, now, random.Random(7))
    b = make_slots(rule, week, now, random.Random(7))
    c = make_slots(rule, week, now, random.Random(8))
    assert [s.start_utc for s in a] == [s.start_utc for s in b]
    assert [s.start_utc for s in a] != [s.start_utc for s in c]


def test_lead_dropping():
    rule = lee_rule()
    week = date(2026, 10, 26)
    # now = Monday 08:30 local (07:30 UTC): today's 08:00 slot would already be past
    now = datetime(2026, 10, 26, 7, 30, tzinfo=timezone.utc)
    slots = make_slots(rule, week, now, random.Random(1))
    for slot in slots:
        assert slot.start_utc >= now + timedelta(minutes=get_settings().PLANNER_LEAD_MINUTES)


def test_dst_week_utc_offsets():
    rule = lee_rule()
    # Week spanning Sun 25 Oct 2026 (DST ends): Fri 23 -> Thu 29
    week = date(2026, 10, 23)
    now = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
    slots = make_slots(rule, week, now, random.Random(3))
    tz = ZoneInfo(TZ)
    for slot in slots:
        local = slot.start_utc.astimezone(tz)
        assert slot.local_day == local.date()
        assert "08:00" <= local.strftime("%H:%M") <= "21:00"
    # Sat 24 slots are BST (utc+1), Mon 26 GMT (utc+0) - implicit via correctness check above


def test_rule_validation_422():
    with pytest.raises(AppError) as e:
        validate_rule_values(0, 1, 2, "08:00", "21:00", 180, TZ)
    assert e.value.status == 422
    validate_rule_values(7, 1, 2, "08:00", "21:00", 180, TZ)  # ok
    with pytest.raises(AppError):
        validate_rule_values(21, 3, 2, "08:00", "21:00", 180, TZ)  # >7*max
    with pytest.raises(AppError):
        validate_rule_values(10, 2, 1, "08:00", "21:00", 180, TZ)  # min > max
    with pytest.raises(AppError):
        validate_rule_values(10, 1, 2, "08:00", "08:00", 180, TZ)  # empty window
    with pytest.raises(AppError):
        validate_rule_values(10, 1, 2, "08:00", "21:00", 180, "Not/AZone")
    validate_rule_values(10, 1, 2, "08:00", "21:00", 0, TZ)  # gap 0 is legal


# --------------------------------------------------------------------------
# plan_week (DB-backed)
# --------------------------------------------------------------------------

def db_session():
    return get_session_factory()()


def make_approved(platform="instagram", group_key=None, caption="cap", i=0):
    from app.schemas import PostCreate
    from app.models import Platform, ContentType
    from app.services.posts import create_post

    created = create_post(db_session(), PostCreate(
        profile_id=1, platform=Platform(platform),
        content_type=ContentType.reel if platform == "instagram" else ContentType.video,
        campaign="lifestyle", caption=f"{caption} {i}",
    ))
    from app.services.posts import approve_post, get_post

    approve_post(db_session(), created.id)
    db = db_session()
    post = get_post(db, created.id)
    if group_key:
        post.group_key = group_key
        db.commit()
    return created.id


@pytest.fixture
def rule_row(app):
    from app.models import PostingRule

    db = db_session()
    rule = PostingRule(model_profile_id=1, platform="instagram", posts_per_week=10,
                       min_per_day=1, max_per_day=2, window_start="08:00", window_end="21:00",
                       min_gap_minutes=180, timezone=TZ, is_active=True)
    db.add(rule)
    db.commit()
    return rule


def test_plan_week_schedules_approved_posts(app, client, rule_row):
    posts = [make_approved(caption="A", i=i) for i in range(12)]
    now = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
    week_start = date(2026, 10, 26)
    result = plan_week(db_session(), rule_row, now, rng=random.Random(4), start_day=week_start)
    assert len(result.filled) == 10  # exactly posts_per_week assignments
    from app.services.posts import get_post
    # 12 approved posts > 10 slots: the two newest remain approved, unplanned
    statuses = [get_post(db_session(), pid).status for pid in posts]
    assert statuses.count("approved") == 2
    from app.services.posts import get_post
    statuses = [get_post(db_session(), pid).status for pid in posts]
    assert statuses.count("scheduled") == 10
    planner_posts = [get_post(db_session(), pid) for pid in posts if get_post(db_session(), pid).status == "scheduled"]
    assert all(p.planned_by == "planner" for p in planner_posts)
    # all inside the window
    for p in planner_posts:
        local = p.planned_publish_at.astimezone(ZoneInfo(TZ))
        assert "08:00" <= local.strftime("%H:%M") <= "21:00"


def test_plan_week_idempotent(app, client, rule_row):
    for i in range(10):
        make_approved(caption="A", i=i)
    now = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
    week_start = date(2026, 10, 26)
    r1 = plan_week(db_session(), rule_row, now, rng=random.Random(9), start_day=week_start)
    r2 = plan_week(db_session(), rule_row, now, rng=random.Random(9), start_day=week_start)
    assert len(r1.filled) == 10
    assert len(r2.filled) == 0  # nothing new scheduled the second time
    from app.services.posts import get_post

    import sqlalchemy as sa
    from app.models import ContentPost

    planned = [p for p in db_session().execute(sa.select(ContentPost)).scalars().all()
               if p.status == "scheduled"]
    assert len(planned) == 10  # no double-scheduling


def test_plan_week_oldest_approved_first(app, client, rule_row):
    ids = [make_approved(caption="O", i=i) for i in range(3)]
    now = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
    result = plan_week(db_session(), rule_row, now, rng=random.Random(2), start_day=date(2026, 10, 26))
    times = []
    from app.services.posts import get_post
    for pid in ids:
        post = get_post(db_session(), pid)
        assert post.status == "scheduled"
        times.append(post.planned_publish_at)
    assert times == sorted(times)  # oldest -> earliest slot


def test_plan_week_never_touches_user_planned_posts(app, client, rule_row):
    from app.services.posts import get_post, update_post
    from app.schemas import PostUpdate

    pid = make_approved(caption="U", i=1)
    db = db_session()
    # user sets a planned time -> scheduled planned_by='user'
    update_post(db, pid, PostUpdate(planned_publish_at=datetime(2027, 3, 2, 12, 0, tzinfo=timezone.utc)))
    post = get_post(db, pid)
    assert post.planned_by == "user"
    now = datetime(2027, 2, 28, 20, 0, tzinfo=timezone.utc)
    plan_week(db_session(), rule_row, now, rng=random.Random(1), start_day=date(2027, 3, 1))
    post = get_post(db_session(), pid)
    assert post.planned_publish_at == datetime(2027, 3, 2, 12, 0, tzinfo=timezone.utc)
    assert post.planned_by == "user"


def test_plan_week_mirrors_group_key_same_local_day(app, client, rule_row):
    from app.models import PostingRule
    from app.services.planner import _rule_for
    from app.services.posts import get_post

    db = db_session()
    db.add(PostingRule(model_profile_id=1, platform="tiktok", posts_per_week=10,
                       min_per_day=1, max_per_day=2, window_start="08:00", window_end="21:00",
                       min_gap_minutes=180, timezone=TZ, is_active=True))
    db.commit()

    key = uuid.uuid4().hex[:20]
    ig = make_approved(platform="instagram", group_key=key, i=1)
    tt = make_approved(platform="tiktok", group_key=key, i=2)
    now = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)

    plan_week(db, _rule_for(db, 1, "instagram"), now, rng=random.Random(5), start_day=date(2026, 10, 26))
    plan_week(db, _rule_for(db, 1, "tiktok"), now, rng=random.Random(6), start_day=date(2026, 10, 26))
    p1 = get_post(db_session(), ig)
    p2 = get_post(db_session(), tt)
    assert p1.status == "scheduled" and p2.status == "scheduled"
    assert p1.planned_publish_at.astimezone(ZoneInfo(TZ)).date() == p2.planned_publish_at.astimezone(ZoneInfo(TZ)).date()


def test_planner_push_deduped_per_day(app, client, rule_row):
    # 5 approved posts on a 10-slot plan -> 5 unfilled slots -> slots_unfilled
    # push; a second plan run (same day key) must not send another one.
    for i in range(5):
        make_approved(caption="X", i=i)
    now = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
    r1 = plan_week(db_session(), rule_row, now, rng=random.Random(4), start_day=date(2026, 10, 26))
    assert len(r1.unfilled) > 0
    plan_week(db_session(), rule_row, now, rng=random.Random(4), start_day=date(2026, 10, 26))

    from sqlalchemy import select
    from app.models import NotificationLog

    rows = db_session().execute(
        select(NotificationLog).where(NotificationLog.event == "slots_unfilled")
    ).scalars().all()
    keys = [r.event_key for r in rows]
    assert len(keys) == 1 and len(set(keys)) == 1  # deduped: one push per day key


# --------------------------------------------------------------------------
# Planner API (PLAN §12)
# --------------------------------------------------------------------------

def test_api_rules_crud_and_validation(app, client, rule_row):
    rules = client.get("/api/v1/planner/rules").json()
    assert len(rules) == 1
    rule_id = rules[0]["id"]

    ok = client.put(f"/api/v1/planner/rules/{rule_id}", json={"posts_per_week": 14, "min_per_day": 2, "max_per_day": 2})
    assert ok.status_code == 200, ok.text
    assert ok.json()["posts_per_week"] == 14

    bad = client.put(f"/api/v1/planner/rules/{rule_id}", json={"posts_per_week": 28})  # >7*max_per_day(2)
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "validation_error"


def test_api_plan_inactive_rule_409(app, client, rule_row):
    # seeded rule_row fixture is active -> deactivate
    rules = client.get("/api/v1/planner/rules").json()
    rule_id = rules[0]["id"]
    client.put(f"/api/v1/planner/rules/{rule_id}", json={"is_active": False})
    resp = client.post("/api/v1/planner/plan", json={"profile_id": 1, "platform": "instagram"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "rule_inactive"


def test_api_plan_and_week(app, client, rule_row):
    for i in range(10):
        make_approved(caption="P", i=i)
    resp = client.post("/api/v1/planner/plan", json={"profile_id": 1, "platform": "instagram",
                                                        "start_day": "2027-03-01"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slots"] == 10 and body["filled"] == 10 and body["unfilled"] == 0

    week = client.get("/api/v1/planner/week?profile_id=1&platform=instagram&start=2027-03-01").json()
    assert week["start_day"] == "2027-03-01"
    assert len(week["items"]) == 10
    assert all(it["planned_by"] == "planner" for it in week["items"])
