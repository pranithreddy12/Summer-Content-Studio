"""Scheduler worker (PLAN_PHASE2 §9).

One background process: `python -m app.cli worker`. A plain loop calling run_once()
every WORKER_INTERVAL_SECONDS. No Celery/Redis/APScheduler.

run_once stages (each isolated: one failing stage never stops the others):
  off        -> heartbeat only
  live       -> recover stuck claims
  reminders  -> post_due pushes
  dry_run/live -> publish (or record a dry run) due posts
  live       -> refresh tokens that are close to expiry
  not off    -> planner tick (AUTO_PLAN_ENABLED)
  always     -> heartbeat
Engagement sync joins in B7.
"""

import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import get_settings, publishing_mode
from app.db import utcnow
from app.models import ContentPost, PostStatus, PostingRule, WorkerHeartbeat

logger = logging.getLogger("app.services.scheduler")

REFRESH_WINDOW = {"instagram": timedelta(days=7), "tiktok": timedelta(hours=24)}
REFRESH_RETRY = timedelta(minutes=30)
_refresh_tried: dict[int, datetime] = {}  # ponytail: in-memory (one worker process); a restart simply retries once


def _heartbeat(db, result: dict, now) -> None:
    """Upsert the single worker_heartbeats row (id=1). `planner_ran` (last local day each rule was
    planned) is carried forward so a restart never re-runs today's plan."""
    hb = db.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.id == 1)).scalar_one_or_none()
    if hb is None:
        hb = WorkerHeartbeat(id=1, last_seen=now, last_result=result)
        db.add(hb)
    else:
        prev = hb.last_result or {}
        if "planner_ran" not in result and "planner_ran" in prev:
            result["planner_ran"] = prev["planner_ran"]
        hb.last_seen = now
        hb.last_result = result
    db.commit()


def worker_status(db) -> tuple[str | None, bool]:
    """(last_seen ISO or None, ok) - ok = heartbeat younger than 5 min (PLAN §5).
    Shared by /health and the overview banner."""
    from datetime import timezone

    try:
        hb = db.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.id == 1)).scalar_one_or_none()
    except Exception:  # table missing on a pre-0003 DB
        db.rollback()
        return None, False
    if hb is None:
        return None, False
    last = hb.last_seen if hb.last_seen.tzinfo else hb.last_seen.replace(tzinfo=timezone.utc)
    return last.isoformat(), (utcnow() - last).total_seconds() < 300


# ---- selection -------------------------------------------------------------

def due_reminder_posts(db, now: datetime, limit: int = 10) -> list[ContentPost]:
    """Scheduled posts whose slot is reached and not reminded yet, inside each
    post's posting window (hard rule §1 rule 11)."""
    from app.services.publishing import get_rule
    from app.services.window import in_window

    rows = db.execute(
        select(ContentPost)
        .where(
            ContentPost.status == PostStatus.scheduled.value,
            ContentPost.planned_publish_at.isnot(None),
            ContentPost.planned_publish_at <= now,
            ContentPost.next_attempt_at.is_(None),  # retries only matter in live mode
            ContentPost.reminded_at.is_(None),
        )
        .order_by(ContentPost.planned_publish_at.asc())
        .limit(limit)
    ).scalars().all()
    return [p for p in rows if in_window(get_rule(db, p.model_profile_id, p.platform), now)]


def due_publish_posts(db, now: datetime, limit: int = 10) -> list[ContentPost]:
    """Scheduled posts whose planned time and retry time have been reached, inside their
    posting window (hard rule). Oldest planned first."""
    from sqlalchemy import or_

    from app.services.publishing import get_rule
    from app.services.window import in_window

    rows = db.execute(
        select(ContentPost).where(
            ContentPost.status == PostStatus.scheduled.value,
            ContentPost.planned_publish_at.isnot(None),
            ContentPost.planned_publish_at <= now,
            or_(ContentPost.next_attempt_at.is_(None), ContentPost.next_attempt_at <= now),
        ).order_by(ContentPost.planned_publish_at.asc()).limit(limit)
    ).scalars().all()
    return [p for p in rows if in_window(get_rule(db, p.model_profile_id, p.platform), now)]


# ---- stages ----------------------------------------------------------------

def recover_stuck(db, now: datetime, http) -> dict:
    """Posts left in `publishing` by a crashed worker: verify with the platform before any retry."""
    from app.services.publishing import STUCK_AFTER, recover_stuck_claim

    rows = db.execute(select(ContentPost).where(
        ContentPost.status == PostStatus.publishing.value,
        ContentPost.claimed_at.isnot(None),
        ContentPost.claimed_at <= now - STUCK_AFTER,
    ).order_by(ContentPost.claimed_at)).scalars().all()
    out = {"published": 0, "retry": 0, "failed": 0}
    for post in rows:
        out[recover_stuck_claim(db, post, now, http)] += 1
    return out


def reminders(db, now: datetime, http, result: dict) -> None:
    """Slot reached (+ inside window) -> ONE post_due push; the post stays scheduled and a human
    posts it by hand and clicks Mark as posted. The push carries the full caption + hashtags so it
    works from a phone before the dashboard is reachable."""
    from app.services.notifications import notify
    from app.services.publishing import render_caption

    posts = due_reminder_posts(db, now)
    result["due"] = len(posts)
    if posts and not get_settings().notify_configured:
        result["note"] = "reminders mode but NOTIFY_PROVIDER has no credentials - nothing sent"
        result["skipped"] = len(posts)
        return
    for post in posts:
        sent = notify(
            db, "post_due", f"due:{post.id}",
            f"Time to post to {post.platform} ({post.content_type})",
            render_caption(post)[:3500] + "\n\nAfter posting, open the post and click Mark as posted.",
            url=f"/posts/{post.id}", http=http,
        )
        if sent:  # a failed push stays un-reminded and is retried next loop
            post.reminded_at = now
            db.add(post)
            result["reminded"] += 1
    db.commit()


def publish_due(db, now: datetime, http, mode: str, result: dict) -> None:
    """Due, inside the window, retry time reached -> publish (live) or record a dry run."""
    from sqlalchemy import func

    from app.models import PublishAttempt
    from app.services.publishing import PERSISTENT_PREFLIGHT, fail_preflight, publish_post

    def dry_attempts() -> int:
        return db.scalar(select(func.count()).select_from(PublishAttempt).where(PublishAttempt.trigger == "dry_run"))

    before = dry_attempts()
    posts = due_publish_posts(db, now)
    result["due"] = len(posts)
    for post in posts:
        try:
            out = publish_post(db, post.id, trigger="retry" if post.next_attempt_at else "schedule", now=now, http=http)
        except Exception as e:  # pre-flight problem: the post is untouched
            db.rollback()
            code = getattr(e, "code", type(e).__name__)
            if mode == "live" and code in PERSISTENT_PREFLIGHT:   # will never fix itself: fail visibly, user hits Retry
                fail_preflight(db, post.id, e, now)
                result["failed"] += 1
            else:
                logger.warning("publish skipped for post %s: %s", post.id, code)
                result["skipped"] += 1
            continue
        if out.status in ("published", "sent_to_drafts"):
            result["published"] += 1
        elif out.status == "failed":
            result["failed"] += 1
    if mode == "dry_run":
        result["dry_runs"] = dry_attempts() - before  # NEW "would publish" attempts only


def refresh_due_tokens(db, now: datetime, http) -> dict:
    """Refresh tokens close to expiry (Instagram: within 7 days, TikTok: within 24 h). A rejected
    refresh marks the account `expired` (+ push) inside accounts.refresh_account; transient failures
    are retried at most every 30 minutes."""
    from app.platforms import PlatformError
    from app.services import AppError, accounts

    out = {"refreshed": 0, "failed": 0}
    for acct in accounts.accounts_expiring(db, now, max(REFRESH_WINDOW.values())):
        if acct.token_expires_at - now > REFRESH_WINDOW.get(acct.platform, timedelta(days=7)):
            continue
        last = _refresh_tried.get(acct.id)
        if last and now - last < REFRESH_RETRY:
            continue
        _refresh_tried[acct.id] = now
        try:
            accounts.refresh_account(db, acct, http)
            out["refreshed"] += 1
        except (PlatformError, AppError) as e:
            db.rollback()
            logger.warning("token refresh failed for account %s: %s", acct.id, getattr(e, "code", "error"))
            out["failed"] += 1
    return out


def planner_tick(db, now: datetime, state: dict) -> dict:
    """AUTO_PLAN_ENABLED: once per local day, from PLANNER_RUN_HOUR on, plan the next 7 days for every
    active rule. `state` maps rule id -> last local day planned (persisted in the heartbeat row)."""
    from app.api.dependencies import get_claude_client
    from app.services.planner import plan_week

    settings = get_settings()
    out = {"planned": 0, "filled": 0}
    rules = db.execute(select(PostingRule).where(PostingRule.is_active == True).order_by(PostingRule.id)).scalars().all()  # noqa: E712
    for rule in rules:
        local = now.astimezone(ZoneInfo(rule.timezone))
        today = local.date().isoformat()
        if local.hour < settings.PLANNER_RUN_HOUR or state.get(str(rule.id)) == today:
            continue
        state[str(rule.id)] = today  # mark first: a failing plan must not retry every minute
        try:
            res = plan_week(db, rule, now, client=get_claude_client() if rule.auto_topup else None)
            out["planned"] += 1
            out["filled"] += len(res.filled)
        except Exception:
            db.rollback()
            logger.exception("planner tick failed", extra={"event": "planner_failed", "rule_id": rule.id})
    return out


# ---- one iteration ---------------------------------------------------------

def run_once(db, now, http=None) -> dict:
    """One worker iteration. Returns counters (used by tests and the heartbeat)."""
    settings = get_settings()
    mode = publishing_mode()
    result = {"mode": mode, "due": 0, "reminded": 0, "published": 0, "failed": 0, "skipped": 0}

    if mode == "off":
        result["note"] = "publishing disabled (PUBLISHING_MODE=off)"
        _heartbeat(db, result, now)
        return result

    if http is None and mode == "live":
        from app.platforms import http_client

        http = http_client(settings)

    def stage(name: str, fn) -> None:
        try:
            fn()
        except Exception:
            db.rollback()
            logger.exception("worker stage failed", extra={"event": "stage_failed", "stage": name})
            result.setdefault("errors", []).append(name)

    if mode == "live":
        stage("recover_stuck", lambda: result.__setitem__("stuck", recover_stuck(db, now, http)))
    if mode == "reminders":
        stage("reminders", lambda: reminders(db, now, http, result))
    else:
        stage("publish", lambda: publish_due(db, now, http, mode, result))
    if mode == "live":
        stage("refresh_tokens", lambda: result.__setitem__("tokens", refresh_due_tokens(db, now, http)))
    if settings.AUTO_PLAN_ENABLED:
        def tick() -> None:
            hb = db.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.id == 1)).scalar_one_or_none()
            state = dict((hb.last_result or {}).get("planner_ran", {})) if hb else {}
            result["planner"] = planner_tick(db, now, state)
            result["planner_ran"] = state
        stage("planner", tick)

    _heartbeat(db, result, now)
    return result


# ---- the loop --------------------------------------------------------------

def run_forever(stop: threading.Event, interval: float, http=None, once: bool = False, echo=print,
                session_factory=None, clock=utcnow) -> int:
    """Loop until `stop` is set (SIGTERM / Ctrl+C) or once. The iteration in progress always finishes
    before exit, and the sleep is interruptible so shutdown is immediate. Returns iterations run."""
    from app.db import get_session_factory

    session_factory = session_factory or get_session_factory()
    runs = 0
    while not stop.is_set():
        db = session_factory()
        try:
            echo(f"worker: {run_once(db, clock(), http=http)}")
        except Exception as e:  # one bad iteration never kills the worker
            logger.exception("worker iteration failed")
            echo(f"worker iteration failed: {type(e).__name__}")
        finally:
            db.close()
        runs += 1
        if once:
            break
        stop.wait(interval)
    return runs
