"""Calendar drag-and-drop: POST /calendar/move reschedules a SCHEDULED post (future, inside the posting window)."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import get_session_factory
from app.models import ContentType, Platform
from app.schemas import PostCreate, PostUpdate
from app.services.posts import approve_post, create_post, get_post, update_post

MOVE = "/calendar/move"


def _tz():
    return ZoneInfo(get_settings().APP_TIMEZONE)


def _local_day(offset_days: int):
    return (datetime.now(_tz()) + timedelta(days=offset_days)).date()


def _utc(day, hh, mm=0):
    return datetime.combine(day, time(hh, mm), tzinfo=_tz()).astimezone(timezone.utc)


def scheduled_post(day, hh=12):
    db = get_session_factory()()
    post = create_post(db, PostCreate(profile_id=1, platform=Platform.instagram, content_type=ContentType.reel,
                                      campaign="lifestyle", caption="cal"))
    approve_post(db, post.id)
    update_post(db, post.id, PostUpdate(planned_publish_at=_utc(day, hh)))
    return post.id


def reload(pid):
    return get_post(get_session_factory()(), pid)


def test_drag_to_another_day_keeps_the_time(app, client):
    pid = scheduled_post(_local_day(2), 12)
    target = _local_day(4)
    r = client.post(MOVE, json={"post_id": pid, "day": target.isoformat()})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    post = reload(pid)
    local = post.planned_publish_at.astimezone(_tz())
    assert local.date() == target and local.strftime("%H:%M") == "12:00"
    assert post.status == "scheduled" and post.planned_by == "user"  # a manual move is never re-planned


def test_move_with_a_new_time(app, client):
    pid = scheduled_post(_local_day(2), 12)
    r = client.post(MOVE, json={"post_id": pid, "day": _local_day(3).isoformat(), "time": "15:30"})
    assert r.status_code == 200, r.text
    local = reload(pid).planned_publish_at.astimezone(_tz())
    assert (local.date(), local.strftime("%H:%M")) == (_local_day(3), "15:30")


def test_move_outside_window_in_the_past_or_wrong_status_is_refused(app, client):
    pid = scheduled_post(_local_day(2), 12)
    before = reload(pid).planned_publish_at
    late = client.post(MOVE, json={"post_id": pid, "day": _local_day(3).isoformat(), "time": "23:30"})
    assert late.status_code == 422 and "window" in late.json()["message"]
    past = client.post(MOVE, json={"post_id": pid, "day": _local_day(-3).isoformat(), "time": "12:00"})
    assert past.status_code == 422 and past.json()["ok"] is False
    assert reload(pid).planned_publish_at == before  # nothing changed

    db = get_session_factory()()
    draft = create_post(db, PostCreate(profile_id=1, platform=Platform.instagram, content_type=ContentType.reel,
                                       campaign="lifestyle", caption="not scheduled"))
    wrong = client.post(MOVE, json={"post_id": draft.id, "day": _local_day(3).isoformat(), "time": "12:00"})
    assert wrong.status_code == 409 and "scheduled" in wrong.json()["message"]


def test_bad_requests_are_422_not_500(app, client):
    for body in ({}, {"post_id": "x", "day": "2026-01-01"}, {"post_id": 1, "day": "nope"},
                 {"post_id": 2 ** 70, "day": "2026-01-01", "time": "12:00"},
                 {"post_id": 1, "day": "2026-01-01", "time": "99:99"}):
        assert client.post(MOVE, json=body).status_code == 422, body
    assert client.post(MOVE, json={"post_id": 999999, "day": _local_day(3).isoformat(), "time": "12:00"}).status_code == 404


def test_calendar_marks_scheduled_posts_draggable(app, client):
    day = _local_day(2)
    pid = scheduled_post(day, 12)
    monday = day - timedelta(days=day.weekday())
    html = client.get(f"/calendar?week={monday.isoformat()}").text
    assert f'data-post-id="{pid}"' in html and 'draggable="true"' in html and 'data-day="' in html
    assert f'>#{pid}</span>' in html  # the draft number is shown on the chip
