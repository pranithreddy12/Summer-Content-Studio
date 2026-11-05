"""Posts service - CRUD, transitions, variants, stats."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func, desc, or_
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import utcnow
from app.models import (
    ContentPost, ContentVariant, ModelProfile, MediaAsset, Tag,
    Platform, PostStatus, PostSource
)
from app.schemas import PostCreate, PostUpdate
from app.services import AppError


# =========================================================================
# Status Transition Rules
# =========================================================================

# (action, allowed_from_status) -> new_status
TRANSITIONS = {
    ("approve", "draft"): "approved",
    ("approve", "approved"): "approved",  # idempotent
    ("archive", "draft"): "archived",
    ("archive", "approved"): "archived",
    ("archive", "scheduled"): "archived",
    ("archive", "failed"): "archived",  # Phase 2: failed is archivable
    ("restore", "archived"): "draft",
    # --- Phase 2 (PLAN_PHASE2 §8) ---
    # Service-only transitions (claim/publish_success/inbox_success/publish_retry/
    # publish_fail) are applied by services/publishing.py via apply_transition().
    ("claim", "approved"): "publishing",
    ("claim", "scheduled"): "publishing",
    ("publish_success", "publishing"): "published",       # published_via='api'
    ("inbox_success", "publishing"): "sent_to_drafts",     # TikTok inbox mode
    ("mark_posted", "approved"): "published",              # user asserts they posted it
    ("mark_posted", "scheduled"): "published",
    ("mark_posted", "sent_to_drafts"): "published",
    ("publish_retry", "publishing"): "scheduled",          # next_attempt_at set
    ("publish_fail", "publishing"): "failed",
    ("preflight_fail", "approved"): "failed",              # worker found a persistent pre-flight problem
    ("preflight_fail", "scheduled"): "failed",
    ("retry", "failed"): "scheduled",                      # resets attempt count
    ("edit", "failed"): "draft",
    ("plan", "approved"): "scheduled",                     # planner sets planned_by='planner'
}


def apply_transition(db: Session, post: ContentPost, action: str, commit: bool = True) -> str:
    """Apply a transition by action name and return the new status.

    Single choke-point for status changes (services only; no endpoint accepts
    `status` in a body).
    """
    new_status = _validate_transition(post.status, action)
    post.status = new_status
    return new_status


def mark_posted(db: Session, post_id: int, external_url: Optional[str] = None) -> ContentPost:
    """User asserts they posted the content by hand (PLAN §8).

    Works in every PUBLISHING_MODE; the human is the source of truth.
    Records published_via='manual' and published_at = now (the click).
    """
    post = get_post(db, post_id)
    if not post:
        raise AppError("not_found", "Post not found", status=404)

    if external_url is not None:
        if not external_url.startswith("https://"):
            raise AppError("validation_error", "external_url must start with https://", status=422)
        if len(external_url) > 500:
            raise AppError("validation_error", "external_url too long", status=422)

    new_status = apply_transition(db, post, "mark_posted")
    post.published_at = utcnow()
    post.published_via = "manual"
    if external_url:
        post.external_url = external_url
    post.next_attempt_at = None
    post.updated_at = utcnow()

    db.commit()
    db.refresh(post)
    return post


def _validate_transition(current_status: str, action: str) -> str:
    """Validate status transition and return new status."""
    key = (action, current_status)
    if key not in TRANSITIONS:
        raise AppError(
            "invalid_transition",
            f"Cannot {action} a post with status '{current_status}'.",
            status=409,
        )
    return TRANSITIONS[key]


# =========================================================================
# Core CRUD
# =========================================================================

def create_post(db: Session, req: PostCreate, idempotency_key: Optional[str] = None) -> ContentPost:
    """Create a new post (manual or AI-generated)."""
    # Check idempotency
    if idempotency_key:
        existing = db.execute(
            select(ContentPost).where(ContentPost.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing:
            return existing

    # Validate campaign tag exists
    campaign_tag = db.execute(select(Tag).where(Tag.slug == req.campaign)).scalar_one_or_none()
    if not campaign_tag:
        raise AppError("not_found", f"Campaign tag '{req.campaign}' does not exist", status=404)

    # Validate asset if provided
    if req.asset_id:
        asset = db.get(MediaAsset, req.asset_id)
        if not asset or not asset.is_active:
            raise AppError("not_found", "Asset not found or inactive", status=404)
        ensure_asset_allowed(req.platform.value, asset)

    # Validate profile
    profile = db.get(ModelProfile, req.profile_id)
    if not profile or not profile.is_active:
        raise AppError("not_found", "Profile not found or inactive", status=404)

    # Parse hashtags (store without #)
    hashtags = [h.strip().lstrip("#") for h in req.hashtags if h.strip()]

    post = ContentPost(
        model_profile_id=req.profile_id,
        media_asset_id=req.asset_id,
        platform=req.platform.value,
        content_type=req.content_type.value,
        campaign=req.campaign,
        hook=req.hook,
        caption=req.caption,
        hashtags=hashtags,
        call_to_action=req.call_to_action,
        content_notes=req.content_notes,
        status=PostStatus.draft.value,
        planned_publish_at=req.planned_publish_at,
        source=PostSource.manual.value,
        generation_input=None,
        idempotency_key=idempotency_key or req.idempotency_key,
        created_at=utcnow(),
        updated_at=utcnow(),
    )

    # Add campaign tag
    post.tags = [campaign_tag]

    # Add additional tags
    if req.tags:
        tag_objs = get_or_create_tags(db, req.tags)
        post.tags.extend(tag_objs)

    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def get_post(db: Session, post_id: int) -> Optional[ContentPost]:
    """Get post by ID with relationships loaded."""
    return db.execute(
        select(ContentPost)
        .options(
            joinedload(ContentPost.profile),
            joinedload(ContentPost.asset),
            joinedload(ContentPost.tags),
            joinedload(ContentPost.variants),
        )
        .where(ContentPost.id == post_id)
    ).unique().scalar_one_or_none()


def list_posts(
    db: Session,
    platform: Optional[str] = None,
    status: Optional[list[str]] = None,
    campaign: Optional[str] = None,
    asset_id: Optional[int] = None,
    profile_id: Optional[int] = None,
    planned_from: Optional[datetime] = None,
    planned_to: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List posts with filters."""
    query = select(ContentPost).options(
        joinedload(ContentPost.asset),
        joinedload(ContentPost.profile),
        joinedload(ContentPost.tags),
    )

    # Default: exclude archived unless explicitly requested
    if status is None or "archived" not in status:
        query = query.where(ContentPost.status != PostStatus.archived.value)

    if platform:
        query = query.where(ContentPost.platform == platform)
    if status:
        query = query.where(ContentPost.status.in_(status))
    if campaign:
        query = query.where(ContentPost.campaign == campaign)
    if asset_id:
        query = query.where(ContentPost.media_asset_id == asset_id)
    if profile_id:
        query = query.where(ContentPost.model_profile_id == profile_id)
    if planned_from:
        query = query.where(ContentPost.planned_publish_at >= planned_from)
    if planned_to:
        query = query.where(ContentPost.planned_publish_at <= planned_to)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar() or 0

    # Apply ordering and pagination
    query = query.order_by(
        ContentPost.planned_publish_at.asc().nullslast(),
        desc(ContentPost.created_at)
    ).limit(limit).offset(offset)

    items = db.execute(query).unique().scalars().all()

    return {"items": items, "total": total}


# =========================================================================
# Status Actions
# =========================================================================

def ensure_asset_allowed(platform: str, asset: Optional[MediaAsset]) -> None:
    """H7 (PLAN_PHASE3 §6): Fanvue-only media never goes to Instagram/TikTok.
    Checked when media is attached, at approve, and again at publish (asset_is_publishable)."""
    if asset is not None and asset.fanvue_only and platform != "fanvue":
        raise AppError(
            "fanvue_only_media",
            "This media is marked Fanvue-only and can't be used on Instagram or TikTok.",
            status=409,
        )


def approve_post(db: Session, post_id: int) -> ContentPost:
    """Approve a post (draft -> approved, or approved with planned time -> scheduled)."""
    post = get_post(db, post_id)
    if not post:
        raise AppError("not_found", "Post not found", status=404)

    new_status = _validate_transition(post.status, "approve")
    ensure_asset_allowed(post.platform, post.asset)

    # If approved and has planned_publish_at, becomes scheduled
    if new_status == "approved" and post.planned_publish_at:
        new_status = "scheduled"

    post.status = new_status
    if new_status in ("approved", "scheduled"):
        post.approved_at = utcnow()
    post.updated_at = utcnow()

    db.commit()
    db.refresh(post)
    return post


def archive_post(db: Session, post_id: int) -> ContentPost:
    """Archive a post."""
    post = get_post(db, post_id)
    if not post:
        raise AppError("not_found", "Post not found", status=404)

    new_status = _validate_transition(post.status, "archive")
    post.status = new_status
    post.archived_at = utcnow()
    post.updated_at = utcnow()

    db.commit()
    db.refresh(post)
    return post


def restore_post(db: Session, post_id: int) -> ContentPost:
    """Restore an archived post to draft."""
    post = get_post(db, post_id)
    if not post:
        raise AppError("not_found", "Post not found", status=404)

    new_status = _validate_transition(post.status, "restore")
    post.status = new_status
    post.archived_at = None
    post.updated_at = utcnow()

    db.commit()
    db.refresh(post)
    return post


# =========================================================================
# Content Editing
# =========================================================================

def update_post(db: Session, post_id: int, req: PostUpdate) -> ContentPost:
    """Update post content (resets approval if approved/scheduled)."""
    post = get_post(db, post_id)
    if not post:
        raise AppError("not_found", "Post not found", status=404)

    # Cannot edit archived or published posts
    if post.status in (PostStatus.archived.value, PostStatus.published.value):
        raise AppError("invalid_transition", f"Cannot edit a post with status '{post.status}'.", status=409)
    # A post claimed by the publisher is untouchable until the worker resolves it
    if post.status == PostStatus.publishing.value:
        raise AppError("invalid_transition", "Post is being published; try again shortly.", status=409)

    update_data = req.model_dump(exclude_unset=True)

    # Fields that count as a "content edit" (resets approval per transition table)
    CONTENT_FIELDS = {"hook", "caption", "hashtags", "call_to_action", "content_notes", "asset_id", "tags", "campaign",
                      "fanvue_audience", "fanvue_price_cents"}   # a new price/audience is re-approved too

    # Fanvue offer fields belong to Fanvue posts only; the price must be inside the PPV range (H6)
    if any(update_data.get(k) is not None for k in ("fanvue_audience", "fanvue_price_cents"))             and post.platform != "fanvue":
        raise AppError("validation_error", "Audience and price apply to Fanvue posts only.", status=422)
    if update_data.get("fanvue_price_cents") is not None:
        s = get_settings()
        if not s.FANVUE_PPV_MIN_CENTS <= update_data["fanvue_price_cents"] <= s.FANVUE_PPV_MAX_CENTS:
            raise AppError("validation_error", f"Price must be between {s.FANVUE_PPV_MIN_CENTS / 100:.2f} and "
                                               f"{s.FANVUE_PPV_MAX_CENTS / 100:.2f}.", status=422)
    content_changed = bool(CONTENT_FIELDS & set(update_data.keys()))

    # Handle hashtags
    if "hashtags" in update_data:
        hashtags = [h.strip().lstrip("#") for h in update_data["hashtags"] if h.strip()]
        post.hashtags = hashtags
        del update_data["hashtags"]

    # Handle tags
    if "tags" in update_data:
        tag_names = update_data["tags"]
        del update_data["tags"]
        # Clear existing non-category tags, keep category tags
        category_tags = [t for t in post.tags if t.is_category]
        new_tags = get_or_create_tags(db, tag_names)
        post.tags = category_tags + new_tags

    # Handle asset_id
    if "asset_id" in update_data:
        asset_id = update_data["asset_id"]
        del update_data["asset_id"]
        if asset_id is not None:
            asset = db.get(MediaAsset, asset_id)
            if not asset or not asset.is_active:
                raise AppError("not_found", "Asset not found or inactive", status=404)
            ensure_asset_allowed(post.platform, asset)
        post.media_asset_id = asset_id

    # Handle campaign change (campaign tag must exist)
    if "campaign" in update_data:
        new_campaign = update_data["campaign"]
        campaign_tag = db.execute(select(Tag).where(Tag.slug == new_campaign)).scalar_one_or_none()
        if not campaign_tag:
            raise AppError("not_found", f"Campaign tag '{new_campaign}' does not exist", status=404)

    # Handle planned_publish_at (explicit None clears it)
    if "planned_publish_at" in update_data:
        planned = update_data["planned_publish_at"]
        del update_data["planned_publish_at"]
        if planned is not None and planned < utcnow():
            raise AppError("validation_error", "planned_publish_at cannot be in the past", status=422)

        post.planned_publish_at = planned
        if planned is not None:
            # A user-set time is never moved by the planner (PLAN §8)
            post.planned_by = "user"

        # Transition rules for planned_publish_at
        if post.status == PostStatus.approved.value and planned is not None:
            post.status = PostStatus.scheduled.value
        elif post.status == PostStatus.scheduled.value and planned is None:
            post.status = PostStatus.approved.value

    # Validate new campaign before applying
    if "campaign" in update_data:
        post.campaign = update_data["campaign"]
        del update_data["campaign"]

    # Apply remaining fields
    for field, value in update_data.items():
        setattr(post, field, value)

    # Content change resets approval (approved/scheduled -> draft) and lets a
    # failed post be edited back into the working queue (PLAN §8)
    if content_changed and post.status in (
        PostStatus.approved.value, PostStatus.scheduled.value, PostStatus.failed.value
    ):
        post.status = PostStatus.draft.value
        post.approved_at = None

    post.updated_at = utcnow()
    db.commit()
    db.refresh(post)
    return post


# =========================================================================
# Variants
# =========================================================================

def add_variant(db: Session, post_id: int, variant_data: dict, generation_log_id: Optional[int] = None) -> ContentVariant:
    """Add a variant to a post."""
    post = get_post(db, post_id)
    if not post:
        raise AppError("not_found", "Post not found", status=404)

    if post.status == PostStatus.archived.value:
        raise AppError("invalid_transition", "Cannot add variant to archived post", status=409)

    variant = ContentVariant(
        post_id=post_id,
        hook=variant_data.get("hook", ""),
        caption=variant_data.get("caption", ""),
        hashtags=[h.strip().lstrip("#") for h in variant_data.get("hashtags", []) if h and h.strip()],
        call_to_action=variant_data.get("call_to_action", ""),
        content_notes=variant_data.get("content_notes", ""),
        content_format=variant_data.get("content_format", ""),
        generation_log_id=generation_log_id,
        created_at=utcnow(),
    )

    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


def apply_variant(db: Session, post_id: int, variant_id: int) -> ContentPost:
    """Apply a variant's content to the post."""
    post = get_post(db, post_id)
    if not post:
        raise AppError("not_found", "Post not found", status=404)

    variant = db.get(ContentVariant, variant_id)
    if not variant or variant.post_id != post_id:
        raise AppError("not_found", "Variant not found", status=404)

    if post.status in (PostStatus.archived.value, PostStatus.published.value, PostStatus.publishing.value,
                       PostStatus.sent_to_drafts.value):
        raise AppError("invalid_transition", f"Cannot apply variant to post with status '{post.status}'.", status=409)

    # Copy variant fields to post
    post.hook = variant.hook
    post.caption = variant.caption
    post.hashtags = variant.hashtags
    post.call_to_action = variant.call_to_action
    post.content_notes = variant.content_notes

    # Content change resets approval
    if post.status in (PostStatus.approved.value, PostStatus.scheduled.value, PostStatus.failed.value):
        post.status = PostStatus.draft.value
        post.approved_at = None

    post.updated_at = utcnow()
    db.commit()
    db.refresh(post)
    return post


# =========================================================================
# Stats
# =========================================================================

def stats(db: Session) -> dict:
    """Get dashboard statistics."""
    # Total active assets
    total_assets = db.execute(
        select(func.count(MediaAsset.id)).where(MediaAsset.is_active == True)
    ).scalar() or 0

    # Total non-archived posts
    total_posts = db.execute(
        select(func.count(ContentPost.id)).where(ContentPost.status != PostStatus.archived.value)
    ).scalar() or 0

    # Counts by status
    status_counts = {}
    for status_val in [s.value for s in PostStatus]:
        count = db.execute(
            select(func.count(ContentPost.id)).where(ContentPost.status == status_val)
        ).scalar() or 0
        status_counts[status_val] = count

    # Counts by platform x status
    platform_status = {}
    for platform in [p.value for p in Platform]:
        platform_status[platform] = {}
        for status_val in [s.value for s in PostStatus]:
            count = db.execute(
                select(func.count(ContentPost.id)).where(
                    ContentPost.platform == platform,
                    ContentPost.status == status_val
                )
            ).scalar() or 0
            platform_status[platform][status_val] = count

    # Drafts awaiting approval
    drafts_awaiting = status_counts.get("draft", 0)

    # Approved
    approved_count = status_counts.get("approved", 0)

    # Scheduled
    scheduled_count = status_counts.get("scheduled", 0)

    return {
        "total_assets": total_assets,
        "total_posts": total_posts,
        "drafts_awaiting_approval": drafts_awaiting,
        "approved": approved_count,
        "scheduled": scheduled_count,
        "by_status": status_counts,
        "by_platform_status": platform_status,
    }


# Import get_or_create_tags from security
from app.security import get_or_create_tags