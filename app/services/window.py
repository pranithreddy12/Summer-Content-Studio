"""Posting-window utilities (PLAN_PHASE2 §6a).

Pure, timezone-aware functions implementing the hard posting-window rule
(§1 rule 11): nothing is published, delivered to TikTok drafts or reminded
outside the rule's window (default 08:00-21:00 local, Europe/London) unless
a human explicitly overrides a manual Publish now.

One shared implementation - every window check in the codebase calls these.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass
class Window:
    """A parsed posting window: local start/end times and their timezone."""

    start: time
    end: time  # INCLUSIVE: a post at exactly window_end is inside the window
    tz: ZoneInfo


def build_window(rule) -> Window:
    """Parse a posting rule (window_start/window_end strings, timezone,
    enforce_window) into a Window.

    Accepts any object with those attributes (PostingRule in prod, a stub in
    tests). Falls back to APP_TIMEZONE when rule.timezone is empty.
    """
    if getattr(rule, "enforce_window", True) is False:
        # Window is never checked when enforcement is off; still parse defaults
        start, end, tz = time(8, 0), time(21, 0), ZoneInfo("UTC")
    else:
        start = _parse_hhmm(rule.window_start)
        end = _parse_hhmm(rule.window_end)
        if start >= end:
            raise ValueError(f"window_start {rule.window_start} must be before window_end {rule.window_end}")
        tzname = getattr(rule, "timezone", None) or _app_timezone()
        tz = ZoneInfo(tzname)
    return Window(start=start, end=end, tz=tz)


def _parse_hhmm(value: str) -> time:
    """Parse a local 'HH:MM' string into a time."""
    h, m = value.strip().split(":")
    if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
        raise ValueError(f"invalid window time: {value!r} (expected HH:MM)")
    return time(int(h), int(m))


def _app_timezone() -> str:
    try:
        from app.config import get_settings

        return get_settings().APP_TIMEZONE
    except Exception:
        return "UTC"


def in_window(rule, now_utc: datetime) -> bool:
    """Is `now_utc` (aware) inside the rule's posting window?

    start <= local time <= end (end inclusive: 21:00:00.000000 allowed,
    anything after 21:00:00 is out).

    enforce_window=False -> always True.
    """
    if getattr(rule, "enforce_window", True) is False:
        return True
    window = build_window(rule)
    local = now_utc.astimezone(window.tz)
    return window.start <= local.time() <= window.end


def next_window_start(rule, now_utc: datetime) -> datetime:
    """The next window start (local window_start of "today" if now is before
    today's start, else tomorrow's) as an aware UTC datetime.

    DST-safe via zoneinfo; ambiguous local times resolve to the first
    occurrence (fold=0).
    """
    window = build_window(rule)
    local = now_utc.astimezone(window.tz)
    candidate = local.replace(
        hour=window.start.hour, minute=window.start.minute, second=0, microsecond=0
    )
    if candidate <= local:  # at/after today's start -> tomorrow's start
        candidate = datetime.combine(
            candidate.date() + timedelta(days=1),
            time(window.start.hour, window.start.minute),
            tzinfo=window.tz,
        )
    return candidate.astimezone(timezone.utc)


def clamp_to_window(rule, when_utc: datetime) -> datetime:
    """`when_utc` if it is inside the window, else the next window start."""
    if in_window(rule, when_utc):
        return when_utc
    return next_window_start(rule, when_utc)
