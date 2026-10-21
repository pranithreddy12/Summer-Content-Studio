"""Weekly random-slot planner (PLAN_PHASE2 §9a).

Client pattern: 10 posts/week per platform, >= 1/day, 3 days with 2 posts,
random times, only 08:00-21:00 local (hard window §1 rule 11), the same 10
posts mirrored on both platforms. The planner schedules APPROVED posts and may
create DRAFTS (auto_topup) - it never approves anything.
"""

import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ContentPost, PostStatus
from app.services import AppError
from app.services.window import build_window

logger = logging.getLogger("app.services.planner")

# Statuses that count against a day's quota (already planned or done)
OCCUPIED_STATUSES = [
    PostStatus.scheduled.value,
    PostStatus.published.value,
    PostStatus.sent_to_drafts.value,
    PostStatus.publishing.value,
]


@dataclass
class Slot:
    start_utc: datetime  # aware UTC
    local_day: date      # date in the rule's timezone


@dataclass
class PlanResult:
    slots: list[Slot] = field(default_factory=list)
    filled: list[Slot] = field(default_factory=list)
    unfilled: list[Slot] = field(default_factory=list)
    topup_drafts: list = field(default_factory=list)


def validate_rule_values(posts_per_week: int, min_per_day: int, max_per_day: int,
                         window_start: str, window_end: str, min_gap_minutes: int,
                         timezone_name: str) -> None:
    """Rule validation (PLAN §9a). Raises AppError 422 on the first violation."""
    def fail(field, msg):
        raise AppError("validation_error", f"{field}: {msg}", status=422)

    if not (1 <= posts_per_week <= 28):
        fail("posts_per_week", "must be between 1 and 28")
    if min_per_day < 1 or max_per_day > 4 or min_per_day > max_per_day:
        fail("min_per_day/max_per_day", "need 1 <= min_per_day <= max_per_day <= 4")
    if posts_per_week < 7 * min_per_day:
        fail("posts_per_week", f"must be at least 7 * min_per_day ({7 * min_per_day})")
    if posts_per_week > 7 * max_per_day:
        fail("posts_per_week", f"must be at most 7 * max_per_day ({7 * max_per_day})")

    def minutes(value):
        try:
            h, m = value.strip().split(":")
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                raise ValueError(value)
            return int(h) * 60 + int(m)
        except Exception:
            fail("window", f"'{value}' must be local HH:MM")

    start_m, end_m = minutes(window_start), minutes(window_end)
    if start_m >= end_m:
        fail("window", f"window_start '{window_start}' must be before window_end '{window_end}'")
    if (end_m - start_m) < (max_per_day - 1) * min_gap_minutes + 1:
        fail("window", f"too short for {max_per_day} posts/day with a {min_gap_minutes} min gap")
    if min_gap_minutes < 0:
        fail("min_gap_minutes", "must be >= 0")
    try:
        ZoneInfo(timezone_name)
    except Exception:
        fail("timezone", f"'{timezone_name}' is not a valid IANA timezone")


def _day_counts(rule, rng: random.Random) -> list[int]:
    """7 per-day counts: every day gets min_per_day; the remainder goes to
    distinct random days (never above max_per_day). Lee's numbers: exactly
    3 random days get 2."""
    counts = [rule.min_per_day] * 7
    extra = rule.posts_per_week - 7 * rule.min_per_day
    while extra > 0:
        days = list(range(7))
        rng.shuffle(days)
        progressed = False
        for d in days:
            if extra == 0:
                break
            if counts[d] < rule.max_per_day:
                counts[d] += 1
                extra -= 1
                progressed = True
        if not progressed:
            break  # validation makes this unreachable
    return counts


def _pick_minutes(count: int, span: int, gap: int, rng: random.Random) -> list[int]:
    """`count` distinct minutes in [0, span] (inclusive), every gap >= `gap`.
    Up to 50 random attempts, then evenly spaced fallback."""
    if count <= 0:
        return []
    if count == 1:
        return [rng.randrange(span + 1)]
    for _attempt in range(50):
        picks = sorted(rng.choice(range(span + 1)) for _ in range(count))
        if len(set(picks)) == count and all((b - a) >= gap for a, b in zip(picks, picks[1:])):
            return picks
    step = span / count
    return [min(span, round(step * (i + 0.5))) for i in range(count)]


def make_slots(rule, start_day: date, now: datetime, rng: random.Random,
               day_targets: list[int] | None = None) -> list[Slot]:
    """7 days of random slots strictly inside the posting window.

    Pure: same rule + start_day + now + rng -> same slots. Drops any slot
    earlier than now + PLANNER_LEAD_MINUTES.
    """
    validate_rule_values(
        rule.posts_per_week, rule.min_per_day, rule.max_per_day,
        rule.window_start, rule.window_end, rule.min_gap_minutes, rule.timezone,
    )
    window = build_window(rule)
    tz = window.tz
    start_m = window.start.hour * 60 + window.start.minute
    end_m = window.end.hour * 60 + window.end.minute  # window end is inclusive
    span = end_m - start_m

    counts = list(day_targets) if day_targets is not None else _day_counts(rule, rng)
    lead = get_settings().PLANNER_LEAD_MINUTES
    cutoff = now + timedelta(minutes=lead)

    slots: list[Slot] = []
    day = start_day
    for i in range(7):
        for m in _pick_minutes(counts[i], span, rule.min_gap_minutes, rng):
            minute_of_day = start_m + m
            local_dt = datetime.combine(
                day, time(minute_of_day // 60, minute_of_day % 60), tzinfo=tz
            )
            start_utc = local_dt.astimezone(timezone.utc)
            if start_utc >= cutoff:  # drop slots earlier than now + lead
                slots.append(Slot(start_utc=start_utc, local_day=day))
        day = day + timedelta(days=1)
    return slots


def _rule_for(db: Session, profile_id: int, platform: str):
    from app.models import PostingRule

    return db.execute(
        select(PostingRule).where(PostingRule.model_profile_id == profile_id,
                                  PostingRule.platform == platform)
    ).scalar_one_or_none()


def _occupied_per_day(db: Session, rule, days: list[date]) -> dict[date, int]:
    """Posts already scheduled-or-beyond for each local day (rule tz)."""
    tz = ZoneInfo(rule.timezone)
    rows = db.execute(
        select(ContentPost).where(
            ContentPost.model_profile_id == rule.model_profile_id,
            ContentPost.platform == rule.platform,
            ContentPost.status.in_(OCCUPIED_STATUSES),
            ContentPost.planned_publish_at.isnot(None),
        )
    ).scalars().all()
    counts = {d: 0 for d in days}
    for post in rows:
        local_day = post.planned_publish_at.astimezone(tz).date()
        if local_day in counts:
            counts[local_day] += 1
    return counts


def _plan_post(db: Session, post, slot: Slot) -> None:
    """Apply the 'plan' transition: approved -> scheduled, planned_by='planner'."""
    from app.services.posts import apply_transition

    post.planned_publish_at = slot.start_utc
    post.planned_by = "planner"
    apply_transition(db, post, "plan", commit=False)
    post.updated_at = datetime.now(timezone.utc)
    db.add(post)


def _take_day(slots: list[Slot], taken: set[int], day: date, platform: str):
    """Earliest free slot on `day`, else None."""
    for slot in slots:
        if id(slot) in taken or slot.local_day != day:
            continue
        taken.add(id(slot))
        return slot
    return None


def _take_any(slots: list[Slot], taken: set[int], platform: str):
    for slot in slots:
        if id(slot) in taken:
            continue
        taken.add(id(slot))
        return slot
    return None


def plan_week(db: Session, rule, now: datetime, rng: random.Random | None = None,
              start_day: date | None = None, client=None) -> PlanResult:
    """Plan the next 7 days for one (profile, platform) rule. Idempotent:
    running twice never double-schedules. Never approves."""
    rng = rng or random.Random()
    tz = ZoneInfo(rule.timezone)
    settings = get_settings()

    validate_rule_values(
        rule.posts_per_week, rule.min_per_day, rule.max_per_day,
        rule.window_start, rule.window_end, rule.min_gap_minutes, rule.timezone,
    )

    if start_day is None:
        # default: tomorrow in the rule tz (today counts only for slots after now+lead)
        start_day = (now.astimezone(tz) + timedelta(days=1)).date()

    week_days = [start_day + timedelta(days=i) for i in range(7)]
    occupied = _occupied_per_day(db, rule, week_days)

    # Day targets reduced by posts that are already scheduled/published
    pattern = _day_counts(rule, rng)
    targets = [max(0, pattern[i] - occupied[d]) for i, d in enumerate(week_days)]
    slots = make_slots(rule, start_day, now, rng, day_targets=targets)

    result = PlanResult(slots=slots)
    taken: set[int] = set()

    # Candidate posts: approved, never planned, planner-owned slot to be.
    # Posts where planned_by='user' (they have a planned time) and posts past
    # approval are never touched.
    candidates = db.execute(
        select(ContentPost).where(
            ContentPost.model_profile_id == rule.model_profile_id,
            ContentPost.platform == rule.platform,
            ContentPost.status == PostStatus.approved.value,
            ContentPost.planned_publish_at.is_(None),
        ).order_by(ContentPost.created_at.asc())
    ).scalars().all()

    singletons = [p for p in candidates if not p.group_key]
    groups: dict[str, list] = {}
    for p in candidates:
        if p.group_key:
            groups.setdefault(p.group_key, []).append(p)
    # group order = oldest member first
    ordered_groups = sorted(groups.values(), key=lambda ms: min(m.created_at for m in ms))

    # ---- Singletons: earliest slot first
    for post in singletons:
        slot = _take_any(slots, taken, post.platform)
        if slot is None:
            break
        _plan_post(db, post, slot)
        result.filled.append(slot)

    # ---- Groups (mirroring): same local day on each platform
    for members in ordered_groups:
        member_slots: dict[int, Slot] = {}
        # earliest local day with a free slot for the first member's platform
        days = sorted({s.local_day for s in slots if id(s) not in taken})
        assigned_together = False
        for day in days:
            picks = {}
            ok = True
            trial: set[int] = set(taken)
            for m in members:
                # keep platforms distinct within the group
                slot = _take_day(slots, trial, day, m.platform)
                if slot is None:
                    ok = False
                    break
                picks[id(m)] = slot
            if ok:
                taken = trial
                member_slots = picks
                assigned_together = True
                break
        if not assigned_together:
            # No common day: fill each member independently (earliest slot)
            for m in members:
                slot = _take_any(slots, taken, m.platform)
                if slot is not None:
                    member_slots[id(m)] = slot

        for m in members:
            slot = member_slots.get(id(m))
            if slot is None:
                continue
            _plan_post(db, m, slot)
            result.filled.append(slot)

    result.unfilled = [s for s in slots if id(s) not in taken]

    # ---- Optional AI draft top-up: DRAFTS only, never approved; per-run cap
    if rule.auto_topup and client is not None and result.unfilled \
            and settings.PLANNER_MAX_TOPUP_PER_DAY > 0:
        from app.schemas import RandomDraftRequest
        from app.models import Platform
        from app.services.random_draft import generate_random_drafts

        remaining_cap = settings.PLANNER_MAX_TOPUP_PER_DAY
        platform_enum = Platform(rule.platform)
        while remaining_cap > 0 and result.unfilled:
            result.unfilled.pop(0)
            try:
                out = generate_random_drafts(db, RandomDraftRequest(
                    platform=platform_enum, campaign="auto", count=1,
                    profile_id=rule.model_profile_id,
                ), client)
                result.topup_drafts.extend(out["posts"])
                remaining_cap -= 1
            except Exception as e:  # top-up is best-effort, never blocks the plan
                logger.warning("planner top-up failed: %s", e)
                break

    # ---- slots_unfilled push: unfilled slots within 48 h, deduped per day
    if result.unfilled:
        first = min(s.start_utc for s in result.unfilled)
        within_48h = [s for s in result.unfilled if s.start_utc <= first + timedelta(hours=48)]
        if within_48h:
            from app.services.notifications import notify

            first_day = sorted({s.local_day for s in within_48h})[0]
            description = ", ".join(
                s.start_utc.astimezone(tz).strftime("%H:%M")
                for s in sorted(within_48h, key=lambda s: s.start_utc)
            )
            notify(db, "slots_unfilled",
                   f"slots:{rule.model_profile_id}:{rule.platform}:{first_day.isoformat()}",
                   "Empty posting slots",
                   f"{len(within_48h)} slot(s) on the plan have no approved post ({description}).",
                   url="/planner")
            db.commit()

    db.commit()
    return result
