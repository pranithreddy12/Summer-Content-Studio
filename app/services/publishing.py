"""Publishing service (PLAN_PHASE2 §8).

B1 scope: pre-flight `check_publishable` (also used at approve time and shown
in the UI) and the caption renderer. The full publish_post pipeline (claim,
attempts, retries, modes) is built in milestone B5.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import publishing_mode
from app.db import utcnow
from app.models import ContentPost, PlatformAccount, AccountStatus, StorageType
from app.services import AppError
from app.services.window import in_window

MAX_CAPTION_LENGTH = 2200  # Instagram caption limit


class RuleDefaults:
    """Fallback posting-window values when no posting_rules row exists."""

    window_start = "08:00"
    window_end = "21:00"
    enforce_window = True
    timezone = "Europe/London"


def get_rule(db: Session, profile_id: int, platform: str):
    """Posting rule for (profile, platform) or the hard-window defaults."""
    from app.models import PostingRule

    rule = db.execute(
        select(PostingRule).where(
            PostingRule.model_profile_id == profile_id,
            PostingRule.platform == platform,
        )
    ).scalar_one_or_none()
    return rule if rule else RuleDefaults()


def connected_account(db: Session, profile_id: int, platform: str) -> Optional[PlatformAccount]:
    """The connected account for (profile, platform), or None."""
    return db.execute(
        select(PlatformAccount).where(
            PlatformAccount.model_profile_id == profile_id,
            PlatformAccount.platform == platform,
            PlatformAccount.status == AccountStatus.connected.value,
        )
    ).scalar_one_or_none()


def asset_is_publishable(post: ContentPost) -> tuple[bool, Optional[str]]:
    """Media rules (PLAN §2/§7): upload always; local_sync only if the file
    exists; external_url never."""
    from app.services.assets import resolve_asset_file

    if post.media_asset_id is None:
        return False, "media_required"
    asset = post.asset
    if asset is None or not asset.is_active:
        return False, "media_required"
    if asset.storage_type == StorageType.external_url.value:
        return False, "media_not_publishable"
    # Platform media rules (docs/PLATFORM_API_NOTES.md): Instagram images must be JPEG;
    # TikTok inbox mode takes videos only.
    if post.platform == "instagram" and asset.media_type == "image" and (asset.mime_type or "") != "image/jpeg"             and post.content_type != "reel":
        return False, "media_not_publishable"
    if post.platform == "tiktok" and asset.media_type != "video":
        return False, "media_not_publishable"
    if asset.storage_type == StorageType.local_sync.value:
        try:
            resolve_asset_file(asset)
        except Exception:
            return False, "media_not_publishable"
    return True, None


def preflight_reasons(db: Session, post: ContentPost, now: Optional[datetime] = None) -> list[str]:
    """Human-readable publish-pre-flight reasons (shown in the dashboard)."""
    reasons: list[str] = []
    if post.status not in ("approved", "scheduled", "publishing", "sent_to_drafts"):
        reasons.append(f"status is '{post.status}'; only approved or scheduled posts can publish")
    if post.media_asset_id is None:
        reasons.append("media_required: attach an asset")
    else:
        ok, code = asset_is_publishable(post)
        if not ok and code == "media_not_publishable":
            reasons.append("media_not_publishable: external-url media cannot be served to the platform")
    if publishing_mode() == "live" and connected_account(db, post.model_profile_id, post.platform) is None:
        reasons.append("account_not_connected: connect the account on the Accounts page")
    if post.caption and len(post.caption) > MAX_CAPTION_LENGTH:
        reasons.append(f"caption_too_long: {len(post.caption)} > {MAX_CAPTION_LENGTH} chars")
    if now is not None and not in_window(get_rule(db, post.model_profile_id, post.platform), now):
        reasons.append("outside_posting_window: inside 08:00-21:00 only")
    return reasons


def check_publishable(
    db: Session,
    post: ContentPost,
    override_window: bool = False,
    now: Optional[datetime] = None,
) -> None:
    """Hard pre-flight: raises AppError(code, 409/422) on the first failure.
    Post status is never changed here."""
    if post.status not in ("approved", "scheduled"):
        raise AppError(
            "invalid_transition" if post.status in ("draft", "publishing", "archived", "published", "sent_to_drafts") else "invalid_transition",
            f"Cannot publish a post with status '{post.status}'.",
            status=409,
        )
    if post.media_asset_id is None:
        raise AppError("media_required", "A publishable post needs media attached.", status=409)
    ok, code = asset_is_publishable(post)
    if not ok:
        raise AppError(code, "Media is not publishable.", status=409)
    if len(post.caption or "") > MAX_CAPTION_LENGTH:
        raise AppError("caption_too_long", "Caption exceeds the platform limit.", status=422)
    if publishing_mode() == "live" and connected_account(db, post.model_profile_id, post.platform) is None:
        raise AppError("account_not_connected", "No connected platform account.", status=409)
    if override_window:
        return
    rule = get_rule(db, post.model_profile_id, post.platform)
    current = now if now is not None else utcnow()
    if not in_window(rule, current):
        raise AppError(
            "outside_posting_window",
            "Outside the posting window (08:00-21:00 local).",
            status=409,
        )


def render_caption(post: ContentPost) -> str:
    """The full caption sent to the platform: caption + blank line + hashtags.

    Built in exactly one place (PLAN §7)."""
    tags = " ".join("#" + h for h in (post.hashtags or []))
    if tags:
        return f"{post.caption}\n\n{tags}"
    return post.caption


# =========================================================================
# publish_post (PLAN_PHASE2 §8): claim -> attempt -> platform -> record
# =========================================================================

import logging  # noqa: E402
from datetime import timedelta  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models import PublishAttempt  # noqa: E402
from app.platforms import PlatformError, http_client, publisher  # noqa: E402
from app.services.window import clamp_to_window  # noqa: E402

logger = logging.getLogger("app.services.publishing")

BACKOFF_MINUTES = (5, 15, 60)  # transient failures: 1st, 2nd, 3rd retry delay


def list_attempts(db: Session, post_id: int) -> list[PublishAttempt]:
    return list(db.execute(select(PublishAttempt).where(PublishAttempt.post_id == post_id)
                           .order_by(PublishAttempt.id)).scalars())


def publish_post(db: Session, post_id: int, trigger: str = "manual", now: Optional[datetime] = None,
                 http=None, override_window: bool = False) -> ContentPost:
    """Publish one post according to PUBLISHING_MODE.

    off / reminders -> 409. dry_run -> pre-flight + payload check, one idempotent
    "would publish" attempt, NO platform HTTP, NO status change. live -> atomic claim,
    real publish, success/retry/fail recorded. Outcomes are recorded on the post; only
    pre-flight problems raise (and then the post is untouched).
    """
    from app.services.posts import get_post

    mode = publishing_mode()
    if mode == "off":
        raise AppError("publishing_disabled", "Publishing is switched off (PUBLISHING_MODE=off).", status=409)
    if mode == "reminders":
        raise AppError("publishing_reminders_only",
                       "Reminders mode: post by hand, then click Mark as posted.", status=409)
    post = get_post(db, post_id)
    if post is None:
        raise AppError("not_found", "Post not found", status=404)
    now = now or utcnow()
    check_publishable(db, post, override_window=override_window, now=now)
    if mode == "dry_run":
        return _dry_run(db, post, now)
    return _publish_live(db, post, trigger, now, http)


def _dry_run(db: Session, post: ContentPost, now: datetime) -> ContentPost:
    from app.services.assets import resolve_asset_file

    marker = f"dry:{(post.planned_publish_at or now).isoformat()}"
    exists = db.execute(select(PublishAttempt.id).where(
        PublishAttempt.post_id == post.id, PublishAttempt.trigger == "dry_run",
        PublishAttempt.platform_ref == marker)).first()
    if exists:
        return post
    caption = render_caption(post)
    resolve_asset_file(post.asset)  # payload check: the file really is there
    count = len(list_attempts(db, post.id))
    db.add(PublishAttempt(
        post_id=post.id, attempt_no=count + 1, trigger="dry_run", started_at=now, finished_at=now,
        success=None, platform_ref=marker,
        error_message=f"dry run: would publish {post.content_type} to {post.platform} "
                      f"({len(caption)} chars, media '{post.asset.filename}')"[:500]))
    db.commit()
    return post


def _publish_live(db: Session, post: ContentPost, trigger: str, now: datetime, http) -> ContentPost:
    from sqlalchemy import update

    from app.services import accounts
    from app.services.assets import resolve_asset_file
    from app.services.media_urls import media_url_for
    from app.services.posts import apply_transition

    settings = get_settings()
    account = connected_account(db, post.model_profile_id, post.platform)
    if http is None:
        http = http_client(settings)

    # Atomic claim: only one caller can move approved/scheduled -> publishing
    claimed = db.execute(update(ContentPost).where(
        ContentPost.id == post.id, ContentPost.status.in_(["approved", "scheduled"])
    ).values(status="publishing", claimed_at=now, platform_account_id=account.id,
             publish_attempt_count=ContentPost.publish_attempt_count + 1))
    if claimed.rowcount == 0:
        db.rollback()
        raise AppError("already_publishing", "This post is already being published.", status=409)
    attempt = PublishAttempt(post_id=post.id, account_id=account.id,
                             attempt_no=len(list_attempts(db, post.id)) + 1, trigger=trigger, started_at=now)
    db.add(attempt)
    db.commit()
    db.refresh(post)

    secrets_: list[str] = []
    try:
        token = accounts.get_valid_token(db, account, http, now)
        secrets_.append(token)
        try:
            path = resolve_asset_file(post.asset)
        except Exception as e:
            raise PlatformError("media_not_publishable", "The media file is not available on the server.",
                                False) from e

        def on_ref(ref: str) -> None:  # saved as soon as known so a crash stays recoverable
            attempt.platform_ref = ref
            db.commit()

        result = publisher(post.platform).publish(
            http, token, account.external_user_id, post, media_url_for(post.asset.id, now), path,
            on_ref=on_ref, caption=render_caption(post))
    except PlatformError as e:
        return _record_failure(db, post, attempt, account, e, now, secrets_)
    except AppError as e:  # e.g. the stored token cannot be decrypted
        return _record_failure(db, post, attempt, account,
                               PlatformError("account_not_connected", e.message, False), now, secrets_)
    except Exception:
        logger.exception("publish crashed", extra={"event": "publish_crash", "post_id": post.id})
        return _record_failure(db, post, attempt, account,
                               PlatformError("internal_error", "Unexpected error while publishing.", True),
                               now, secrets_)

    inbox = post.platform == "tiktok" and settings.TIKTOK_POST_MODE == "inbox"
    apply_transition(db, post, "inbox_success" if inbox else "publish_success")
    post.external_post_id = result.external_post_id
    post.external_url = result.external_url
    if not inbox:
        post.published_at = result.published_at or now
        post.published_via = "api"
    post.claimed_at = post.next_attempt_at = post.last_publish_error = None
    attempt.success, attempt.finished_at = True, now
    attempt.platform_ref = result.platform_ref or attempt.platform_ref
    db.commit()
    if inbox:
        from app.services.notifications import notify

        notify(db, "tiktok_ready", f"tiktok:{post.id}", "Video is in your TikTok drafts",
               "Open TikTok, add the AI label and press Publish.", url=f"/posts/{post.id}")
    return post


def _record_failure(db: Session, post: ContentPost, attempt: PublishAttempt, account, e: PlatformError,
                    now: datetime, secrets_: list[str]) -> ContentPost:
    from app.services import accounts
    from app.services.notifications import notify
    from app.services.posts import apply_transition

    msg = accounts.sanitize(e.message, *secrets_)
    attempt.success, attempt.finished_at = False, now
    attempt.error_code, attempt.error_message, attempt.http_status = e.code, msg, e.http_status
    attempt.platform_ref = e.platform_ref or attempt.platform_ref
    post.claimed_at = None

    if e.code == "token_expired":
        accounts.mark_attention(db, account, "expired", e.message)
        msg = f"Reconnect the {post.platform} account."
    count = post.publish_attempt_count
    if e.transient and count < get_settings().PUBLISH_MAX_ATTEMPTS:
        apply_transition(db, post, "publish_retry")
        delay = timedelta(minutes=BACKOFF_MINUTES[min(count, len(BACKOFF_MINUTES)) - 1])
        post.next_attempt_at = clamp_to_window(get_rule(db, post.model_profile_id, post.platform), now + delay)
        post.planned_publish_at = post.planned_publish_at or now
        post.last_publish_error = f"{e.code}: {msg}"[:500]
        db.commit()
        return post
    apply_transition(db, post, "publish_fail")
    post.next_attempt_at = None
    post.last_publish_error = f"{e.code}: {msg}"[:500]
    db.commit()
    notify(db, "publish_failed", f"failed:{post.id}:{attempt.attempt_no}",
           f"Post failed on {post.platform}", f"{e.code}: {msg}", url=f"/posts/{post.id}")
    return post


def retry_post(db: Session, post_id: int, now: Optional[datetime] = None) -> ContentPost:
    """failed -> scheduled again (planned time kept if still in the future, else now). The worker
    publishes it inside the posting window; nothing is sent from here."""
    from app.services.posts import apply_transition, get_post

    post = get_post(db, post_id)
    if post is None:
        raise AppError("not_found", "Post not found", status=404)
    now = now or utcnow()
    apply_transition(db, post, "retry")
    post.publish_attempt_count = 0
    post.next_attempt_at = post.last_publish_error = None
    if post.planned_publish_at is None or post.planned_publish_at <= now:
        post.planned_publish_at = now
    post.updated_at = now
    db.commit()
    db.refresh(post)
    return post
