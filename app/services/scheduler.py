"""Scheduler worker (PLAN_PHASE2 §9).

One background process: `python -m app.cli worker`. A plain while-True loop
calling run_once() every WORKER_INTERVAL_SECONDS. No Celery/Redis/APScheduler.

B3 slice: heartbeat, publishing-mode dispatch, `reminders` mode (post_due
pushes with dedupe, window-enforced). The `live` / `dry_run` publish branches
hook into services.publishing.publish_post, completed in milestone B5, and
stuck-claim recovery / token refresh / planner tick / engagement complete B6.
"""

import logging
from datetime import datetime

from sqlalchemy import select

from app.config import get_settings, publishing_mode
from app.db import utcnow
from app.models import ContentPost, WorkerHeartbeat, PostStatus

logger = logging.getLogger("app.services.scheduler")


def _heartbeat(db, result: dict, now) -> None:
    """Upsert the single worker_heartbeats row (id=1)."""
    hb = db.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.id == 1)).scalar_one_or_none()
    if hb is None:
        hb = WorkerHeartbeat(id=1, last_seen=now, last_result=result)
        db.add(hb)
    else:
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
            ContentPost.next_attempt_at.is_(None),  # retries only matter in live mode (B5)
            ContentPost.reminded_at.is_(None),
        )
        .order_by(ContentPost.planned_publish_at.asc())
        .limit(limit)
    ).scalars().all()

    in_window_rows = []
    for post in rows:
        rule = get_rule(db, post.model_profile_id, post.platform)
        if in_window(rule, now):
            in_window_rows.append(post)
    return in_window_rows


def run_once(db, now, http=None) -> dict:
    """One worker iteration. Returns counters (used by tests and the heartbeat)."""
    from app.services.notifications import notify

    settings = get_settings()
    mode = publishing_mode()
    result = {"mode": mode, "due": 0, "reminded": 0, "published": 0, "failed": 0, "skipped": 0}

    if mode == "off":
        result["note"] = "publishing disabled (PUBLISHING_MODE=off)"
        _heartbeat(db, result, now)
        return result

    if mode == "reminders":
        # Slot reached (+ inside window) -> ONE post_due push, post stays
        # scheduled; a human posts it by hand and clicks Mark as posted.
        # The push carries the full caption + hashtags so it is usable from a
        # phone even before the dashboard is reachable (no server yet).
        from app.services.publishing import render_caption

        posts = due_reminder_posts(db, now)
        result["due"] = len(posts)
        if posts and not settings.notify_configured:
            result["note"] = "reminders mode but NOTIFY_PROVIDER has no credentials - nothing sent"
            result["skipped"] = len(posts)
            posts = []
        for post in posts:
            sent = notify(
                db, "post_due", f"due:{post.id}",
                f"Time to post to {post.platform} ({post.content_type})",
                render_caption(post)[:3500] + "\n\nAfter posting, open the post and click Mark as posted.",
                url=f"/posts/{post.id}",
                http=http,
            )
            if sent:  # a failed push stays un-reminded and is retried next loop
                post.reminded_at = now
                db.add(post)
                result["reminded"] += 1
        db.commit()

    elif mode in ("dry_run", "live"):
        # Publish pipeline is wired in milestone B5 (publishing.publish_post);
        # B6 adds stuck-claim recovery, token refresh, planner tick + engagement.
        posts = due_reminder_posts(db, now)
        result["due"] = len(posts)
        result["note"] = "publish pipeline activates in B5/B6"
        result["skipped"] = len(posts)

    _heartbeat(db, result, now)
    return result
