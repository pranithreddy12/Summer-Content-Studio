"""Generation service - orchestrates prompt building, Claude call, and persistence."""

import json
import logging
import time
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import (
    ContentPost, ContentVariant, GenerationLog, MediaAsset, ModelProfile,
    PostStatus, PostSource,
)
from app.prompts import PROMPT_VERSION, render_system, render_user
from app.prompts.platforms import HASHTAG_CAP
from app.schemas import GeneratedContent, GenerateRequest, RegenerateRequest
from app.security import get_or_create_tags
from app.services.claude_client import ClaudeClient, GenerationError
from app.services.posts import get_post

logger = logging.getLogger("app.services.generation")


# =========================================================================
# Post-parse validation (check_output)
# =========================================================================

def check_output(content: GeneratedContent, platform: str) -> GeneratedContent:
    """Validate and normalize Claude's output. Raises GenerationError on invalid output."""
    # Normalize hashtags: strip #, drop empties, dedupe case-insensitively, remove spaces
    seen = set()
    normalized_hashtags = []
    for tag in content.hashtags:
        tag = tag.strip().lstrip("#").strip()
        if not tag:
            continue
        if " " in tag:
            continue  # reject tags with spaces
        tag_key = tag.lower()
        if tag_key in seen:
            continue
        seen.add(tag_key)
        normalized_hashtags.append(tag)

    # Cap hashtags by platform (truncate, don't fail)
    cap = HASHTAG_CAP.get(platform, 15)
    normalized_hashtags = normalized_hashtags[:cap]

    # Caption validation
    caption = content.caption.strip()
    if not caption:
        raise GenerationError("invalid_output", "Caption is empty.", 502)
    if len(caption) > 2200:
        raise GenerationError(
            "invalid_output",
            f"Caption exceeds 2200 characters ({len(caption)}). Try regenerating.",
            502,
        )

    return GeneratedContent(
        content_format=content.content_format,
        hook=content.hook.strip(),
        caption=caption,
        hashtags=normalized_hashtags,
        call_to_action=content.call_to_action.strip(),
        content_notes=content.content_notes.strip(),
    )


# =========================================================================
# Generate draft
# =========================================================================

def generate_draft(
    db: Session,
    req: GenerateRequest,
    client: ClaudeClient,
) -> ContentPost:
    """
    Generate a new draft post via Claude.

    1. Idempotency check
    2. Load and validate entities
    3. Render prompts
    4. Call Claude
    5. Validate output
    6. Persist post + variant + log
    """
    # 1. Idempotency check
    if req.idempotency_key:
        existing = db.execute(
            select(ContentPost).where(ContentPost.idempotency_key == req.idempotency_key)
        ).scalar_one_or_none()
        if existing:
            return existing

    # 2. Load and validate entities
    profile = db.get(ModelProfile, req.profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if not profile.is_active:
        raise HTTPException(status_code=409, detail="Profile is inactive")

    asset = None
    if req.asset_id:
        asset = db.get(MediaAsset, req.asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        if not asset.is_active:
            raise HTTPException(status_code=409, detail="Asset is inactive")

    # Campaign must be existing tag slug
    from app.models import Tag
    campaign_tag = db.execute(select(Tag).where(Tag.slug == req.campaign)).scalar_one_or_none()
    if not campaign_tag:
        raise HTTPException(status_code=422, detail=f"Campaign '{req.campaign}' not found")

    # 3. Render prompts
    system = render_system(profile, req.platform, req.content_type)
    user = render_user(
        platform=req.platform,
        content_type=req.content_type,
        campaign_name=campaign_tag.name,
        asset=asset,
        asset_context=req.asset_context,
        seasonal_context=req.seasonal_context,
        tone_variation=req.tone_variation,
        cta_request=req.cta_request,
        extra_restrictions=req.extra_restrictions,
    )

    # 4. Call Claude
    start_time = time.time()
    try:
        result = client.generate(system, user)
    except GenerationError as e:
        duration_ms = int((time.time() - start_time) * 1000)
        # Log failure
        log = GenerationLog(
            model_profile_id=req.profile_id,
            media_asset_id=req.asset_id,
            post_id=None,
            provider="anthropic",
            model=client.settings.CLAUDE_MODEL,
            prompt_version=PROMPT_VERSION,
            purpose="generate",
            success=False,
            error_category=e.category,
            input_tokens=None,
            output_tokens=None,
            duration_ms=duration_ms,
            request_id=None,
            created_at=utcnow(),
        )
        db.add(log)
        db.commit()
        raise HTTPException(status_code=e.http_status, detail=e.message)

    duration_ms = int((time.time() - start_time) * 1000)

    # 5. Validate output
    try:
        validated = check_output(result.content, req.platform.value)
    except GenerationError as e:
        log = GenerationLog(
            model_profile_id=req.profile_id,
            media_asset_id=req.asset_id,
            post_id=None,
            provider="anthropic",
            model=result.model,
            prompt_version=PROMPT_VERSION,
            purpose="generate",
            success=False,
            error_category=e.category,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_ms=duration_ms,
            request_id=result.request_id,
            created_at=utcnow(),
        )
        db.add(log)
        db.commit()
        raise HTTPException(status_code=e.http_status, detail=e.message)

    # 6. Persist post + variant + log in single commit
    post = ContentPost(
        model_profile_id=req.profile_id,
        media_asset_id=req.asset_id,
        platform=req.platform.value,
        content_type=req.content_type.value,
        campaign=req.campaign,
        hook=validated.hook,
        caption=validated.caption,
        hashtags=validated.hashtags,
        call_to_action=validated.call_to_action,
        content_notes=validated.content_notes,
        status=PostStatus.draft.value,
        planned_publish_at=None,
        source=PostSource.ai.value,
        generation_input=req.model_dump(mode="json"),
        idempotency_key=req.idempotency_key,
        approved_at=None,
        archived_at=None,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    post.hashtags = validated.hashtags  # native list (JSON column)
    post.tags = [campaign_tag]

    # Success log (post_id set after flush)
    log = GenerationLog(
        model_profile_id=req.profile_id,
        media_asset_id=req.asset_id,
        post_id=None,
        provider="anthropic",
        model=result.model,
        prompt_version=PROMPT_VERSION,
        purpose="generate",
        success=True,
        error_category=None,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        duration_ms=duration_ms,
        request_id=result.request_id,
        created_at=utcnow(),
    )

    db.add(post)
    db.add(log)
    db.flush()

    # Link log to post
    log.post_id = post.id

    # Create first variant
    variant = ContentVariant(
        post_id=post.id,
        hook=validated.hook,
        caption=validated.caption,
        hashtags=validated.hashtags,
        call_to_action=validated.call_to_action,
        content_notes=validated.content_notes,
        content_format=validated.content_format,
        generation_log_id=log.id,
        created_at=utcnow(),
    )
    db.add(variant)

    try:
        db.commit()
    except IntegrityError:
        # Race on idempotency_key - rollback and return existing
        db.rollback()
        if req.idempotency_key:
            existing = db.execute(
                select(ContentPost).where(ContentPost.idempotency_key == req.idempotency_key)
            ).scalar_one_or_none()
            if existing:
                return existing
        raise HTTPException(status_code=500, detail="Failed to create post")

    db.refresh(post)
    return post


# =========================================================================
# Regenerate (new variant)
# =========================================================================

def regenerate(
    db: Session,
    post_id: int,
    req: RegenerateRequest,
    client: ClaudeClient,
) -> ContentVariant:
    """Generate a new variant for an existing post. Does not modify the post."""
    post = get_post(db, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.status == PostStatus.archived.value:
        raise HTTPException(status_code=409, detail="Cannot regenerate archived post")

    # Rebuild GenerateRequest from post.generation_input (or post fields for manual)
    profile = db.get(ModelProfile, post.model_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    asset = None
    if post.media_asset_id:
        asset = db.get(MediaAsset, post.media_asset_id)

    # Determine campaign tag name
    campaign_tag = None
    for tag in post.tags:
        if tag.is_category and tag.slug == post.campaign:
            campaign_tag = tag
            break
    if campaign_tag is None:
        from app.models import Tag
        campaign_tag = db.execute(select(Tag).where(Tag.slug == post.campaign)).scalar_one_or_none()
        if not campaign_tag:
            campaign_tag = type("Fake", (), {"name": post.campaign})()

    # Parse generation_input or build from post
    from app.models import Platform, ContentType
    platform = Platform(post.platform)
    content_type = ContentType(post.content_type)

    # Build context from post
    existing_caption = post.caption

    # Render prompts with avoid_repeating
    system = render_system(profile, platform, content_type)
    user = render_user(
        platform=platform,
        content_type=content_type,
        campaign_name=campaign_tag.name,
        asset=asset,
        asset_context="",
        seasonal_context="",
        tone_variation=req.tone_variation or "",
        cta_request="",
        extra_restrictions=[],
        avoid_repeating=existing_caption,
        extra_instructions=req.extra_instructions,
    )

    # Call Claude
    start_time = time.time()
    try:
        result = client.generate(system, user)
    except GenerationError as e:
        duration_ms = int((time.time() - start_time) * 1000)
        log = GenerationLog(
            model_profile_id=post.model_profile_id,
            media_asset_id=post.media_asset_id,
            post_id=post.id,
            provider="anthropic",
            model=client.settings.CLAUDE_MODEL,
            prompt_version=PROMPT_VERSION,
            purpose="regenerate",
            success=False,
            error_category=e.category,
            input_tokens=None,
            output_tokens=None,
            duration_ms=duration_ms,
            request_id=None,
            created_at=utcnow(),
        )
        db.add(log)
        db.commit()
        raise HTTPException(status_code=e.http_status, detail=e.message)

    duration_ms = int((time.time() - start_time) * 1000)

    # Validate
    try:
        validated = check_output(result.content, post.platform)
    except GenerationError as e:
        log = GenerationLog(
            model_profile_id=post.model_profile_id,
            media_asset_id=post.media_asset_id,
            post_id=post.id,
            provider="anthropic",
            model=result.model,
            prompt_version=PROMPT_VERSION,
            purpose="regenerate",
            success=False,
            error_category=e.category,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_ms=duration_ms,
            request_id=result.request_id,
            created_at=utcnow(),
        )
        db.add(log)
        db.commit()
        raise HTTPException(status_code=e.http_status, detail=e.message)

    # Success log
    log = GenerationLog(
        model_profile_id=post.model_profile_id,
        media_asset_id=post.media_asset_id,
        post_id=post.id,
        provider="anthropic",
        model=result.model,
        prompt_version=PROMPT_VERSION,
        purpose="regenerate",
        success=True,
        error_category=None,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        duration_ms=duration_ms,
        request_id=result.request_id,
        created_at=utcnow(),
    )
    db.add(log)
    db.flush()

    # Create variant
    variant = ContentVariant(
        post_id=post.id,
        hook=validated.hook,
        caption=validated.caption,
        hashtags=validated.hashtags,
        call_to_action=validated.call_to_action,
        content_notes=validated.content_notes,
        content_format=validated.content_format,
        generation_log_id=log.id,
        created_at=utcnow(),
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant