"""Calendar 'Save changes': POST /calendar/save reschedules SCHEDULED posts, all or nothing (future, inside the
posting window). The page shows this week and next so a post can be dragged across."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import get_session_factory
from app.models import ContentType, Platform
from app.schemas import PostCreate, PostUpdate
from app.services.posts import approve_post, create_post, get_post, update_post

SAVE = "/calendar/save"


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


def local_of(pid):
    return reload(pid).planned_publish_at.astimezone(_tz())


def test_save_moves_to_another_day_keeping_the_time(app, client):
    pid = scheduled_post(_local_day(2), 12)
    target = _local_day(4)
    r = client.post(SAVE, json={"moves": [{"post_id": pid, "day": target.isoformat()}]})
    assert r.status_code == 200 and r.json() == {"ok": True, "message": "Saved 1 change."}, r.text
    local = local_of(pid)
    assert local.date() == target and local.strftime("%H:%M") == "12:00"
    post = reload(pid)
    assert post.status == "scheduled" and post.planned_by == "user"  # a manual move is never re-planned


def test_save_several_moves_with_new_times(app, client):
    a, b = scheduled_post(_local_day(2), 12), scheduled_post(_local_day(2), 14)
    r = client.post(SAVE, json={"moves": [
        {"post_id": a, "day": _local_day(3).isoformat(), "time": "09:15"},
        {"post_id": b, "day": _local_day(9).isoformat(), "time": "20:45"}]})  # the second one lands in the next week
    assert r.status_code == 200 and "Saved 2 changes" in r.json()["message"], r.text
    assert (local_of(a).date(), local_of(a).strftime("%H:%M")) == (_local_day(3), "09:15")
    assert (local_of(b).date(), local_of(b).strftime("%H:%M")) == (_local_day(9), "20:45")


def test_one_bad_move_saves_nothing(app, client):
    good, bad = scheduled_post(_local_day(2), 12), scheduled_post(_local_day(2), 13)
    before = (reload(good).planned_publish_at, reload(bad).planned_publish_at)
    r = client.post(SAVE, json={"moves": [
        {"post_id": good, "day": _local_day(3).isoformat(), "time": "10:00"},
        {"post_id": bad, "day": _local_day(3).isoformat(), "time": "23:30"}]})  # outside the 08:00-21:00 window
    assert r.status_code == 422 and f"#{bad}" in r.json()["message"] and "window" in r.json()["message"]
    assert (reload(good).planned_publish_at, reload(bad).planned_publish_at) == before  # all or nothing


def test_past_time_and_wrong_status_are_refused(app, client):
    pid = scheduled_post(_local_day(2), 12)
    before = reload(pid).planned_publish_at
    past = client.post(SAVE, json={"moves": [{"post_id": pid, "day": _local_day(-3).isoformat(), "time": "12:00"}]})
    assert past.status_code == 422 and "past" in past.json()["message"]
    assert reload(pid).planned_publish_at == before

    db = get_session_factory()()
    draft = create_post(db, PostCreate(profile_id=1, platform=Platform.instagram, content_type=ContentType.reel,
                                       campaign="lifestyle", caption="not scheduled"))
    wrong = client.post(SAVE, json={"moves": [{"post_id": draft.id, "day": _local_day(3).isoformat(), "time": "12:00"}]})
    assert wrong.status_code == 409 and "scheduled" in wrong.json()["message"]


def test_bad_requests_are_422_not_500(app, client):
    day = _local_day(3).isoformat()
    for body in ({}, {"moves": "x"}, {"moves": []}, {"moves": [{"day": day}]},
                 {"moves": [{"post_id": "x", "day": day, "time": "12:00"}]},
                 {"moves": [{"post_id": 1, "day": "nope", "time": "12:00"}]},
                 {"moves": [{"post_id": 2 ** 70, "day": day, "time": "12:00"}]},
                 {"moves": [{"post_id": 1, "day": day, "time": "99:99"}]}):
        assert client.post(SAVE, json=body).status_code == 422, body
    assert client.post(SAVE, json={"moves": [{"post_id": 999999, "day": day, "time": "12:00"}]}).status_code == 404


def test_calendar_shows_two_weeks_with_draggable_chips_and_controls(app, client):
    day = _local_day(2)
    pid = scheduled_post(day, 12)
    monday = day - timedelta(days=day.weekday())
    html = client.get(f"/calendar?week={monday.isoformat()}").text
    assert html.count('class="cal-day') == 14  # this week and next
    assert f'data-post-id="{pid}"' in html and 'draggable="true"' in html and f'>#{pid}</span>' in html
    for button in ('id="cal-save"', 'id="cal-undo"', 'id="cal-redo"', 'id="cal-discard"'):
        assert button in html
    # a post planned in the second week is on the same page, so it can be dragged back
    later = scheduled_post(monday + timedelta(days=9), 12)
    assert f'data-post-id="{later}"' in client.get(f"/calendar?week={monday.isoformat()}").text
