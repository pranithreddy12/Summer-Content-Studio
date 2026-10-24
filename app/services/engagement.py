"""Instagram engagement (PLAN_PHASE2 §10): metric snapshots, comment sync, AI-drafted replies that a
HUMAN edits and sends. No auto-replies, no DMs, no bulk send, TikTok not available.

Only posts published through the API (`published_via='api'`) are tracked: a manually posted
post has no media id we could look up.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings, publishing_mode
from app.db import utcnow
from app.models import Comment, ContentPost, GenerationLog, PostMetric
from app.platforms import PlatformError, publisher
from app.services import AppError, accounts
from app.services.claude_client import GenerationError

logger = logging.getLogger("app.services.engagement")

SYNC_EVERY = timedelta(minutes=30)    # worker cadence: 2 calls per tracked post per run stays well under platform limits
RECENT_DAYS = 14                      # posts younger than this are tracked
MAX_DRAFT = 300                       # reply draft limit
MAX_SEND = 1000                       # a human may write more than the draft limit, not unlimited
OPEN_STATUSES = ("none", "drafted")   # "unanswered"


# ---- sync ------------------------------------------------------------------

def tracked_posts(db: Session, now: datetime) -> list[ContentPost]:
    return list(db.execute(select(ContentPost).where(
        ContentPost.platform == "instagram", ContentPost.status == "published", ContentPost.published_via == "api",
        ContentPost.external_post_id.isnot(None), ContentPost.published_at >= now - timedelta(days=RECENT_DAYS),
    ).order_by(ContentPost.published_at)).scalars())


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z") if value else None
    except ValueError:
        return None


def sync(db: Session, now: datetime, http) -> dict:
    """Snapshot metrics and pull new comments for recent Instagram posts. One post failing never
    stops the others. Sends ONE digest push per hour when new comments arrived."""
    from app.services.notifications import notify
    from app.services.publishing import connected_account

    mod = publisher("instagram")
    out = {"posts": 0, "metrics": 0, "new_comments": 0, "errors": 0}
    fresh: list[Comment] = []
    for post in tracked_posts(db, now):
        out["posts"] += 1
        account = connected_account(db, post.model_profile_id, "instagram")
        if account is None:
            out["errors"] += 1
            continue
        batch: list[Comment] = []
        try:
            token = accounts.get_valid_token(db, account, http, now)
            m = mod.fetch_metrics(http, token, account.external_user_id, post.external_post_id)
            db.add(PostMetric(post_id=post.id, fetched_at=now, likes=m.get("likes"), comments=m.get("comments"),
                              shares=m.get("shares"), views=m.get("views"), saves=m.get("saves"),
                              raw={k: v for k, v in m.items() if isinstance(v, (int, float))}))
            out["metrics"] += 1
            for c in mod.fetch_comments(http, token, account.external_user_id, post.external_post_id):
                if (c["author_username"] or "").lower() == account.username.lower():
                    continue  # our own comment
                if db.execute(select(Comment.id).where(Comment.platform == "instagram",
                                                       Comment.external_id == c["external_id"])).first():
                    continue
                row = Comment(post_id=post.id, platform="instagram", external_id=c["external_id"],
                              author_username=(c["author_username"] or "")[:100], text=c["text"],
                              commented_at=_parse_ts(c["commented_at"]), created_at=now)
                db.add(row)
                batch.append(row)
            db.commit()
            fresh.extend(batch)  # only comments that were really stored count toward the digest
        except (PlatformError, AppError) as e:
            db.rollback()
            out["errors"] += 1
            logger.warning("engagement sync failed for post %s: %s", post.id, getattr(e, "code", "error"))
    out["new_comments"] = len(fresh)
    if fresh:
        snippets = "\n".join(f"@{r.author_username or '?'}: {r.text[:60]}" for r in fresh[:3])
        notify(db, "new_comments", f"comments:instagram:{now:%Y%m%d%H}",
               f"{len(fresh)} new Instagram comment{'s' if len(fresh) != 1 else ''}", snippets, url="/engagement")
    return out


# ---- reading ---------------------------------------------------------------

def latest_metrics(db: Session, post_id: int) -> Optional[PostMetric]:
    return db.execute(select(PostMetric).where(PostMetric.post_id == post_id)
                      .order_by(PostMetric.fetched_at.desc(), PostMetric.id.desc()).limit(1)).scalar_one_or_none()


def metrics_history(db: Session, post_id: int, limit: int = 100) -> list[PostMetric]:
    return list(db.execute(select(PostMetric).where(PostMetric.post_id == post_id)
                           .order_by(PostMetric.fetched_at.desc(), PostMetric.id.desc()).limit(limit)).scalars())


def totals_7d(db: Session, now: datetime) -> dict:
    """Latest snapshot per post published in the last 7 days, summed (Instagram only)."""
    ids = db.execute(select(ContentPost.id).where(
        ContentPost.platform == "instagram", ContentPost.status == "published",
        ContentPost.published_at >= now - timedelta(days=7))).scalars().all()
    likes = comments = tracked = 0
    for pid in ids:
        m = latest_metrics(db, pid)
        if m is not None:
            tracked += 1
            likes += m.likes or 0
            comments += m.comments or 0
    return {"posts": len(ids), "tracked": tracked, "likes": likes, "comments": comments}


def list_comments(db: Session, platform: Optional[str] = None, post_id: Optional[int] = None,
                  reply_status: Optional[list[str]] = None, limit: int = 50, offset: int = 0) -> dict:
    q = select(Comment)
    if platform:
        q = q.where(Comment.platform == platform)
    if post_id:
        q = q.where(Comment.post_id == post_id)
    if reply_status:
        q = q.where(Comment.reply_status.in_(reply_status))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.execute(q.order_by(Comment.commented_at.desc().nullslast(), Comment.id.desc())
                       .limit(min(limit, 100)).offset(offset)).scalars().all()
    return {"items": list(items), "total": total}


def unanswered_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Comment).where(Comment.reply_status.in_(OPEN_STATUSES))) or 0


def get_comment(db: Session, comment_id: int) -> Comment:
    c = db.get(Comment, comment_id)
    if c is None:
        raise AppError("not_found", "Comment not found.", status=404)
    return c


# ---- drafting (LLM) --------------------------------------------------------

def draft_reply(db: Session, comment_id: int, client, extra_instructions: str = "") -> tuple[Comment, bool]:
    """Ask the LLM for a reply draft. Returns (comment, suggest_ignore). The draft is only a suggestion:
    nothing is sent. An empty draft (the model declined) leaves the status alone and suggests ignoring."""
    from app.prompts import REPLY_PROMPT_VERSION, render_reply_system, render_reply_user

    comment = get_comment(db, comment_id)
    if comment.reply_status in ("sent", "sending"):
        raise AppError("already_sent", "A reply was already sent for this comment.", status=409)
    post = comment.post
    profile = post.profile
    started = time.time()
    try:
        result = client.generate(render_reply_system(profile),
                                 render_reply_user(post.caption, comment.author_username or "", comment.text,
                                                   extra_instructions))
    except GenerationError as e:
        _log(db, post, getattr(e, "provider", None) or "anthropic", getattr(e, "model", None) or "unknown",
             started, False, e.category)
        raise
    _log(db, post, getattr(result, "provider", "anthropic"), result.model, started, True, None, result)

    reply = " ".join((result.content.caption or "").split())
    if len(reply) > MAX_DRAFT:
        reply = reply[:MAX_DRAFT].rsplit(" ", 1)[0].rstrip(",;:- ")
    if not reply:
        db.commit()
        return comment, True
    comment.reply_text, comment.reply_status = reply, "drafted"
    db.commit()
    return comment, False


def _log(db: Session, post: ContentPost, provider: str, model: str, started: float, ok: bool,
         category: Optional[str], result=None) -> None:
    """generation_logs row with purpose='reply' (no prompt/response text is stored)."""
    from app.prompts import REPLY_PROMPT_VERSION

    db.add(GenerationLog(
        model_profile_id=post.model_profile_id, media_asset_id=None, post_id=post.id, provider=provider, model=model,
        prompt_version=REPLY_PROMPT_VERSION, purpose="reply", success=ok, error_category=category,
        input_tokens=getattr(result, "input_tokens", None), output_tokens=getattr(result, "output_tokens", None),
        duration_ms=int((time.time() - started) * 1000), request_id=getattr(result, "request_id", None),
        created_at=utcnow()))
    db.commit()


# ---- human actions ---------------------------------------------------------

def send_reply(db: Session, comment_id: int, text: str, http, now: Optional[datetime] = None) -> Comment:
    """Post the (human-edited) reply to Instagram. Live mode only; one send per comment (atomic claim)."""
    from app.services.publishing import connected_account

    text = (text or "").strip()
    if not text:
        raise AppError("validation_error", "Reply text is required.", status=422)
    if len(text) > MAX_SEND:
        raise AppError("validation_error", f"Reply is longer than {MAX_SEND} characters.", status=422)
    if publishing_mode() != "live":
        raise AppError("publishing_disabled", "Replies are sent in live mode only (PUBLISHING_MODE=live).", status=409)
    comment = get_comment(db, comment_id)
    if comment.platform != "instagram":
        raise AppError("unsupported", "Replies are only available for Instagram.", status=409)
    post = comment.post
    account = connected_account(db, post.model_profile_id, "instagram")
    if account is None:
        raise AppError("account_not_connected", "Connect the Instagram account first.", status=409)

    previous = comment.reply_status
    claimed = db.execute(update(Comment).where(
        Comment.id == comment_id, Comment.reply_status.in_(("none", "drafted", "ignored"))
    ).values(reply_status="sending"))
    if claimed.rowcount == 0:
        db.rollback()
        raise AppError("already_sent", "A reply was already sent (or is being sent) for this comment.", status=409)
    db.commit()

    secrets_: list[str] = []
    try:
        token = accounts.get_valid_token(db, account, http, now or utcnow())
        secrets_.append(token)
        new_id = publisher("instagram").send_reply(http, token, account.external_user_id, comment.external_id, text)
    except (PlatformError, AppError) as e:
        db.execute(update(Comment).where(Comment.id == comment_id).values(reply_status=previous))
        db.commit()
        code = getattr(e, "code", "error")
        message = accounts.sanitize(getattr(e, "message", str(e)), *secrets_)
        if code == "token_expired":
            accounts.mark_attention(db, account, "expired", message)
            message = "Reconnect the Instagram account."
        status = 502 if getattr(e, "transient", False) else 409
        raise AppError(code, message, status=status) from e
    comment = get_comment(db, comment_id)
    comment.reply_status, comment.reply_text, comment.reply_external_id = "sent", text, new_id
    db.commit()
    db.refresh(comment)
    return comment


def ignore_comment(db: Session, comment_id: int) -> Comment:
    comment = get_comment(db, comment_id)
    if comment.reply_status in ("sent", "sending"):
        raise AppError("already_sent", "A reply was already sent for this comment.", status=409)
    comment.reply_status = "ignored"
    db.commit()
    return comment
