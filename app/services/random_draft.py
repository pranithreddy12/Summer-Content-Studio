"""Random draft batch: pick random library assets and generate one draft for each.

A human still reviews and approves every draft (nothing is published). Capped at MAX_COUNT per request.
"""

import logging
import random
import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ContentPost, MediaAsset, MediaType, ModelProfile
from app.schemas import GenerateRequest, RandomDraftRequest
from app.security import resolve_asset_file
from app.services import AppError
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


def generate_random_drafts(db: Session, req: RandomDraftRequest, client) -> dict:
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
    available = len(pool)
    posts, errors = [], []
    for i in range(req.count):
        if not pool:
            break
        asset = pool.pop(random.randrange(len(pool)))
        group_key = uuid.uuid4().hex[:40] if req.both_platforms else None
        targets = (["instagram", "tiktok"] if asset.media_type == MediaType.video.value else ["instagram"])
        if not req.both_platforms:
            targets = [req.platform.value]
        for target in targets:
            key = f"{req.batch_key}-{i}-{target}" if req.batch_key else None
            if key:  # resubmitted batch: reuse what was already created, don't pay again
                done = db.execute(select(ContentPost).where(ContentPost.idempotency_key == key)).scalar_one_or_none()
                if done:
                    posts.append(done)
                    continue
            gen = GenerateRequest(
                profile_id=profile.id, platform=target,
                content_type=CONTENT_TYPE[(target, asset.media_type)],
                campaign=_campaign_for(asset, req.campaign), asset_id=asset.id,
                seasonal_context=req.seasonal_context, idempotency_key=key,
            )
            try:
                post = generate_draft(db, gen, client)
                if group_key:
                    stored = get_post(db, post.id)
                    stored.group_key = group_key
                    db.commit()
                    post = stored
                posts.append(post)
            except HTTPException as e:  # provider/validation error: stop (the next call would likely fail too)
                detail = e.detail if isinstance(e.detail, dict) else {"code": "error", "message": str(e.detail)}
                errors.append({"asset_id": asset.id, "filename": asset.filename, "status": e.status_code, **detail})
                break

    if not posts:
        if errors:
            raise AppError(errors[-1].get("code", "error"), errors[-1]["message"], errors[-1]["status"])
        raise AppError("no_assets", "No matching unused images/videos. Upload or sync media, "
                       "or tick 'include already-used assets'.", 409)
    return {"posts": [get_post(db, p.id) for p in posts], "errors": errors, "available": available}
