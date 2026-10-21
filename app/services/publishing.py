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
    if connected_account(db, post.model_profile_id, post.platform) is None:
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
