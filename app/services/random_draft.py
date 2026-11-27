"""Random draft batch: pick random library assets and generate one draft for each.

A human still reviews and approves every draft (nothing is published). Capped at MAX_COUNT per request.
"""

import logging
import random
import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import platforms
from app.config import get_settings
from app.models import ContentPost, MediaAsset, MediaType, ModelProfile
from app.schemas import GenerateRequest, RandomDraftRequest
from app.security import resolve_asset_file
from app.services import AppError, dropbox_link
from app.services.assets import list_assets
from app.services.generation import generate_draft
from app.services.posts import get_post

logger = logging.getLogger("app.services.random_draft")

MAX_COUNT = 5
MIN_VIDEO_BYTES = 1024  # smaller "videos" are failed uploads / placeholders
DEFAULT_CAMPAIGN = "lifestyle"
CONTENT_TYPE = {
    ("instagram", "image"): "feed_post", ("instagram", "video"): "reel",
    ("tiktok", "image"): "photo_post", ("tiktok", "video"): "video",
}


def _usable(asset: MediaAsset, settings) -> bool:
    """File exists inside its storage root (and isn't an empty placeholder video)."""
    try:
        path = resolve_asset_file(asset, settings)
    except HTTPException:
        return False
    return asset.media_type != MediaType.video.value or path.stat().st_size >= MIN_VIDEO_BYTES


def eligible_assets(db: Session, media_type: Optional[str] = None, tag: Optional[str] = None,
                    include_used: bool = False) -> list[MediaAsset]:
    """Active uploaded/synced assets the AI can actually look at. Dropbox links are excluded."""
    settings = get_settings()
    items = list_assets(db, media_type=media_type, tags=[tag] if tag else None,
                        unused=not include_used, limit=100_000)["items"]
    # Random drafts target Instagram/TikTok only, so Fanvue-only media is never picked (H7)
    return [a for a in items if a.storage_type in ("upload", "local_sync") and not a.fanvue_only
            and _usable(a, settings)]


def _campaign_for(asset: MediaAsset, requested: str) -> str:
    if requested != "auto":
        return requested
    return next((t.slug for t in asset.tags if t.is_category), DEFAULT_CAMPAIGN)


def _draft(db: Session, client, profile, req, target: str, asset: MediaAsset, key=None, group_key=None):
    gen = GenerateRequest(
        profile_id=profile.id, platform=target, content_type=CONTENT_TYPE[(target, asset.media_type)],
        campaign=_campaign_for(asset, req.campaign), asset_id=asset.id,
        seasonal_context=req.seasonal_context, idempotency_key=key)
    post = generate_draft(db, gen, client)
    if group_key:
        post = get_post(db, post.id)
        post.group_key = group_key
        db.commit()
    return post


def carried_over_videos(db: Session, settings) -> list[MediaAsset]:
    """Videos that went to TikTok on an earlier day but not to Instagram yet: Instagram uses them next (oldest first)."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo(settings.APP_TIMEZONE)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    on_instagram = select(ContentPost.media_asset_id).where(
        ContentPost.platform == "instagram", ContentPost.status != "archived", ContentPost.media_asset_id.isnot(None))
    first_tiktok = (select(ContentPost.media_asset_id.label("asset_id"), func.min(ContentPost.created_at).label("at"))
                    .where(ContentPost.platform == "tiktok", ContentPost.status != "archived",
                           ContentPost.media_asset_id.isnot(None)).group_by(ContentPost.media_asset_id).subquery())
    rows = db.execute(select(MediaAsset).join(first_tiktok, first_tiktok.c.asset_id == MediaAsset.id).where(
        MediaAsset.media_type == MediaType.video.value, MediaAsset.is_active == True,  # noqa: E712
        first_tiktok.c.at < today, MediaAsset.id.not_in(on_instagram)).order_by(first_tiktok.c.at)).scalars().all()
    return [a for a in rows if a.storage_type in ("upload", "local_sync") and not a.fanvue_only and _usable(a, settings)]


def _fanvue_twin(db: Session, post: ContentPost) -> ContentPost:
    """Copy an Instagram draft as a Fanvue draft (same text and media, subscribers only, free). Draft only."""
    post.group_key = post.group_key or uuid.uuid4().hex[:40]
    twin = ContentPost(
        model_profile_id=post.model_profile_id, media_asset_id=post.media_asset_id, platform="fanvue",
        content_type="fanvue_post", campaign=post.campaign, hook=post.hook, caption=post.caption,
        hashtags=[], call_to_action=post.call_to_action, content_notes=post.content_notes,
        source=post.source, group_key=post.group_key, fanvue_audience="subscribers")
    db.add(twin)
    db.commit()
    return get_post(db, twin.id)


def copy_to_fanvue(db: Session, post_id: int) -> ContentPost:
    """The 'Copy to Fanvue' button: a Fanvue draft from an existing Instagram/TikTok post. One copy per post."""
    post = get_post(db, post_id)
    if post.platform == "fanvue":
        raise AppError("validation_error", "This is already a Fanvue post.", 422)
    if post.status == "archived":
        raise AppError("invalid_transition", "Archived posts can't be copied.", 409)
    if post.group_key and db.execute(select(ContentPost.id).where(
            ContentPost.group_key == post.group_key, ContentPost.platform == "fanvue",
            ContentPost.status != "archived")).first():
        raise AppError("conflict", "This post already has a Fanvue copy.", 409)
    return _fanvue_twin(db, post)


def generate_random_drafts(db: Session, req: RandomDraftRequest, client, announce: bool = True,
                           fanvue_twin: bool = False, bundle: bool = False) -> dict:
    """Returns {"posts": [ContentPost], "errors": [...], "available": n}. Raises only if nothing was created.

    both_platforms (PLAN §9a step 7): each random VIDEO asset creates an
    Instagram + TikTok pair sharing a group_key (captions generated per
    platform); images produce an Instagram draft only (TikTok inbox is video
    only). Still drafts only - never approved.
    """
    profile = db.get(ModelProfile, req.profile_id) if req.profile_id else db.execute(
        select(ModelProfile).where(ModelProfile.is_active == True).order_by(ModelProfile.id)).scalars().first()
    if not profile:
        raise AppError("not_found", "No active model profile.", 404)

    pool = eligible_assets(db, req.media_type.value if req.media_type else None, req.tag, req.include_used)
    posts, errors = [], []
    if len(pool) < req.count and not req.tag and dropbox_link.configured():
        # Dropbox folder link: fetch just the missing files (a tag filter can't match untagged pulls)
        try:
            pulled = dropbox_link.pull(db, req.count - len(pool), req.media_type.value if req.media_type else None)
            errors += [{"asset_id": None, "filename": e["path"], "status": 502, "code": "dropbox_error",
                        "message": e["message"]} for e in pulled["errors"]]
        except (AppError, platforms.PlatformError) as e:
            errors.append({"asset_id": None, "filename": "", "status": getattr(e, "status", 502),
                           "code": getattr(e, "code", "dropbox_error"), "message": getattr(e, "message", str(e))})
        pool = eligible_assets(db, req.media_type.value if req.media_type else None, req.tag, req.include_used)
    available = len(pool)
    settings = get_settings()
    taken: set[int] = set()  # media used by this call (bundle mode)
    for i in range(req.count):
        carry = None
        if bundle:  # a waiting TikTok video goes to Instagram on half the days (oldest first), else a random pick
            waiting = [a for a in carried_over_videos(db, settings) if a.id not in taken]
            if waiting and random.random() < 0.5:
                carry = waiting[0]
        if carry is None and not pool:
            break
        asset = carry or pool.pop(random.randrange(len(pool)))
        pool = [a for a in pool if a.id != asset.id]
        taken.add(asset.id)
        group_key = uuid.uuid4().hex[:40] if (req.both_platforms or bundle) else None
        targets = (["instagram", "tiktok"] if asset.media_type == MediaType.video.value else ["instagram"])
        if bundle:  # same video on both only when it is fresh; images and carried-over videos get a different TikTok video
            targets = ["instagram"] + (["tiktok"] if asset.media_type == MediaType.video.value and not carry
                                       and settings.platform_enabled("tiktok") else [])
        elif not req.both_platforms:
            targets = [req.platform.value]
        for target in targets:
            key = f"{req.batch_key}-{i}-{target}" if req.batch_key else None
            if key:  # resubmitted batch: reuse what was already created, don't pay again
                done = db.execute(select(ContentPost).where(ContentPost.idempotency_key == key)).scalar_one_or_none()
                if done:
                    posts.append(done)
                    continue
            try:
                posts.append(_draft(db, client, profile, req, target, asset, key, group_key))
            except HTTPException as e:  # provider/validation error: stop (the next call would likely fail too)
                detail = e.detail if isinstance(e.detail, dict) else {"code": "error", "message": str(e.detail)}
                errors.append({"asset_id": asset.id, "filename": asset.filename, "status": e.status_code, **detail})
                break

    if bundle and settings.platform_enabled("tiktok"):  # a TikTok draft for every Instagram draft that has none yet
        for ig in [p for p in posts if p.platform == "instagram"]:
            if any(p.platform == "tiktok" and p.group_key == ig.group_key for p in posts):
                continue
            spare = [a for a in eligible_assets(db, "video", req.tag, req.include_used) if a.id not in taken]
            if not spare:
                errors.append({"asset_id": None, "filename": "", "status": 409, "code": "no_video",
                               "message": "No other video for TikTok (add videos to the library)."})
                break
            video = random.choice(spare)
            try:
                posts.append(_draft(db, client, profile, req, "tiktok", video))  # its own group: Instagram uses it tomorrow
                taken.add(video.id)
            except HTTPException as e:
                detail = e.detail if isinstance(e.detail, dict) else {"code": "error", "message": str(e.detail)}
                errors.append({"asset_id": video.id, "filename": video.filename, "status": e.status_code, **detail})
                break
    if (fanvue_twin or bundle) and settings.platform_enabled("fanvue"):  # one Fanvue copy per new Instagram draft
        posts += [_fanvue_twin(db, p) for p in list(posts) if p.platform == "instagram"]
    if not posts:
        if errors:
            raise AppError(errors[-1].get("code", "error"), errors[-1]["message"], errors[-1]["status"])
        raise AppError("no_assets", "No matching unused images/videos. Upload or sync media, "
                       "or tick 'include already-used assets'.", 409)
    if announce and get_settings().NOTIFY_NEW_DRAFTS:
        _notify_new_drafts(db, posts)
    return {"posts": [get_post(db, p.id) for p in posts], "errors": errors, "available": available}


MAX_DRAFT_PUSHES = 10


def _notify_new_drafts(db: Session, posts: list) -> None:
    """One push per new draft with an Approve button (Telegram), so a batch can be approved from the phone.
    Capped per batch; the rest are mentioned in one line."""
    from app.services.notifications import notify

    for post in posts[:MAX_DRAFT_PUSHES]:
        stored = get_post(db, post.id)
        snippet = " ".join((stored.caption or "").split())[:200]
        media = f"\n[{stored.asset.media_type}: {stored.asset.filename}]" if stored.asset else ""
        notify(db, "draft_ready", f"draft:{stored.id}", f"New draft #{stored.id} · {stored.platform} {stored.content_type}",
               snippet + media, url=f"/posts/{stored.id}", buttons=[("Approve ✅", f"approve:{stored.id}")])
    if len(posts) > MAX_DRAFT_PUSHES:
        notify(db, "draft_ready", f"drafts:{posts[0].id}:more", f"{len(posts) - MAX_DRAFT_PUSHES} more new drafts",
               "Open the studio to review them.", url="/posts?status_filter=draft")
