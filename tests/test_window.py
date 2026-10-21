"""Window utilities (PLAN_PHASE2 §6a)."""

from datetime import datetime, time, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services.window import build_window, in_window, next_window_start, clamp_to_window


def rule(tz="Europe/London", start="08:00", end="21:00", enforce=True):
    return SimpleNamespace(window_start=start, window_end=end, timezone=tz, enforce_window=enforce)


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# --- in_window: hard boundaries (rule: end is inclusive) ---

def test_before_window():
    # 07:59:59 BST = 06:59:59 UTC on 21 Oct (BST = UTC+1)
    assert in_window(rule(), utc(2026, 10, 21, 6, 59, 59)) is False


def test_at_start():
    # 08:00 BST = 07:00 UTC on 21 Oct 2026
    assert in_window(rule(), utc(2026, 10, 21, 7, 0, 0)) is True


def test_middle_of_window():
    assert in_window(rule(), utc(2026, 10, 21, 12, 0)) is True


def test_at_end_is_inclusive():
    # 21:00:00 local = 20:00 UTC in BST
    assert in_window(rule(), utc(2026, 10, 21, 20, 0, 0)) is True


def test_after_end_by_one_second():
    assert in_window(rule(), utc(2026, 10, 21, 20, 0, 1)) is False


def test_after_window():
    assert in_window(rule(), utc(2026, 10, 21, 22, 0)) is False


def test_enforce_window_false_always_true():
    r = rule(enforce=False)
    assert in_window(r, utc(2026, 10, 21, 3, 0)) is True
    assert in_window(r, utc(2026, 10, 21, 23, 0)) is True


# --- build_window / parsing ---

def test_bad_hhmm_rejected():
    with pytest.raises(ValueError):
        build_window(rule(start="25:00"))


def test_start_after_end_rejected():
    with pytest.raises(ValueError):
        build_window(rule(start="21:00", end="08:00"))


# --- next_window_start ---

def test_before_today_start_gives_today():
    # 04:00 UTC on 21 Oct = 05:00 BST -> next start is 08:00 BST = 07:00 UTC
    out = next_window_start(rule(), utc(2026, 10, 21, 4, 0))
    assert out == utc(2026, 10, 21, 7, 0)


def test_inside_window_gives_tomorrow():
    # 12:00 local BST = 11:00 UTC; tomorrow 08:00 BST = 07:00 UTC on 22 Oct
    out = next_window_start(rule(), utc(2026, 10, 21, 11, 0))
    assert out == utc(2026, 10, 22, 7, 0)


def test_at_window_start_gives_tomorrow():
    out = next_window_start(rule(), utc(2026, 10, 21, 7, 0))
    assert out == utc(2026, 10, 22, 7, 0)


def test_after_end_gives_tomorrow():
    out = next_window_start(rule(), utc(2026, 10, 21, 21, 30))
    assert out == utc(2026, 10, 22, 7, 0)


def test_result_is_always_inside_window():
    for now in [utc(2026, 10, 21, 2, 0), utc(2026, 10, 21, 12, 30), utc(2026, 10, 21, 23, 59)]:
        assert in_window(rule(), next_window_start(rule(), now)) is True


# --- DST week: UK clocks go back on 25 Oct 2026 ---

def test_next_start_across_clock_change_is_08_local():
    # Sat 24 Oct 2026 22:00 BST -> next start Sun 25 Oct 08:00 local.
    # DST ends 01:00 UTC on 25 Oct, so 08:00 local = 08:00 UTC (GMT now).
    out = next_window_start(rule(), utc(2026, 10, 24, 21, 0))  # 22:00 BST
    local = out.astimezone(ZoneInfo("Europe/London"))
    assert local.hour == 8 and local.minute == 0
    assert out == utc(2026, 10, 25, 8, 0)


def test_next_start_before_clock_change_is_bst():
    # Fri 24 Oct 04:00 BST = 03:00 UTC -> next start also 24 Oct 08:00 BST = 07:00 UTC
    out = next_window_start(rule(), utc(2026, 10, 24, 3, 0))
    assert out == utc(2026, 10, 24, 7, 0)


def test_day_after_clock_change_window_is_correct():
    # Mon 26 Oct 2026: GMT (UTC+0), so window is 08:00-21:00 UTC
    assert in_window(rule(), utc(2026, 10, 26, 8, 0)) is True
    assert in_window(rule(), utc(2026, 10, 26, 12, 0)) is True
    assert in_window(rule(), utc(2026, 10, 26, 20, 59)) is True
    assert in_window(rule(), utc(2026, 10, 26, 21, 1)) is False


def test_day_after_clock_change_end_boundary_is_20_59_utc():
    # end inclusive -> 21:00:00 local GMT = 21:00 UTC included; 21:00:01 excluded
    assert in_window(rule(), utc(2026, 10, 26, 21, 0, 0)) is True
    assert in_window(rule(), utc(2026, 10, 26, 21, 0, 1)) is False


# --- clamp ---

def test_clamp_inside_returns_when():
    t = utc(2026, 10, 21, 11, 0)
    assert clamp_to_window(rule(), t) == t


def test_clamp_outside_returns_next_start():
    out = clamp_to_window(rule(), utc(2026, 10, 21, 23, 0))
    assert out == utc(2026, 10, 22, 7, 0)


def test_clamp_is_idempotent():
    r = rule()
    for t in [utc(2026, 10, 21, 3, 0), utc(2026, 10, 21, 11, 0), utc(2026, 10, 21, 22, 0)]:
        once = clamp_to_window(r, t)
        assert clamp_to_window(r, once) == once
