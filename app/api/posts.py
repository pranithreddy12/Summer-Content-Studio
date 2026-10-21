"""Posts API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ContentPost
from app.schemas import PostCreate, PostUpdate, PostOut, VariantOut, MarkPostedRequest
from app.services import AppError
from app.services.posts import (
    list_posts, get_post, create_post, update_post,
    approve_post, archive_post, restore_post, apply_variant, mark_posted,
)

router = APIRouter(prefix="/posts", tags=["posts"])


@router.get("")
async def list_posts_endpoint(
    platform: Optional[str] = None,
    status_filter: Optional[list[str]] = Query(None, alias="status"),
    campaign: Optional[str] = None,
    asset_id: Optional[int] = None,
    profile_id: Optional[int] = None,
    planned_from: Optional[str] = None,
    planned_to: Optional[str] = None,
    limit: int = Query(50, le=100),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List posts with filters."""
    planned_from_dt = None
    planned_to_dt = None
    if planned_from:
        planned_from_dt = _parse_aware_iso(planned_from, "planned_from")
    if planned_to:
        planned_to_dt = _parse_aware_iso(planned_to, "planned_to")

    result = list_posts(
        db,
        platform=platform,
        status=status_filter,
        campaign=campaign,
        asset_id=asset_id,
        profile_id=profile_id,
        planned_from=planned_from_dt,
        planned_to=planned_to_dt,
        limit=limit,
        offset=offset,
    )
    items = [PostOut.model_validate(p).model_dump(mode="json") for p in result["items"]]
    return {"items": items, "total": result["total"]}


def _parse_aware_iso(value: str, field: str):
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise AppError("validation_error", f"{field} must be an ISO 8601 datetime", status=422)
    if dt.tzinfo is None:
        raise AppError("validation_error", f"{field} must be timezone-aware", status=422)
    return dt


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_post_endpoint(
    req: PostCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a manual post."""
    # Header overrides body idempotency_key
    idempotency_header = request.headers.get("Idempotency-Key")
    idempotency_key = idempotency_header or req.idempotency_key

    # Check replay
    is_replay = False
    if idempotency_key:
        existing = db.execute(
            select(ContentPost).where(ContentPost.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing:
            is_replay = True

    post = create_post(db, req, idempotency_key=idempotency_key)

    status_code = 200 if is_replay else 201
    post = get_post(db, post.id)
    payload = PostOut.model_validate(post).model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=payload)


@router.get("/{post_id}", response_model=PostOut)
async def get_post_endpoint(post_id: int, db: Session = Depends(get_db)):
    """Get post by ID."""
    post = get_post(db, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@router.patch("/{post_id}", response_model=PostOut)
async def update_post_endpoint(post_id: int, req: PostUpdate, db: Session = Depends(get_db)):
    """Update post content. Cannot set status (extra='forbid' rejects it)."""
    update_post(db, post_id, req)  # raises AppError on invalid state
    return get_post(db, post_id)


@router.get("/{post_id}/variants", response_model=list[VariantOut])
async def list_variants(post_id: int, db: Session = Depends(get_db)):
    """List variants for a post."""
    post = get_post(db, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post.variants


@router.post("/{post_id}/variants/{variant_id}/apply", response_model=PostOut)
async def apply_variant_endpoint(post_id: int, variant_id: int, db: Session = Depends(get_db)):
    """Apply a variant to the post."""
    try:
        apply_variant(db, post_id, variant_id)
    except AppError:
        raise
    return get_post(db, post_id)


@router.post("/{post_id}/approve", response_model=PostOut)
async def approve_post_endpoint(post_id: int, db: Session = Depends(get_db)):
    """Approve a draft post (→ approved, or scheduled if planned time set)."""
    try:
        approve_post(db, post_id)
    except AppError:
        raise
    return get_post(db, post_id)


@router.post("/{post_id}/archive", response_model=PostOut)
async def archive_post_endpoint(post_id: int, db: Session = Depends(get_db)):
    """Archive a post."""
    try:
        archive_post(db, post_id)
    except AppError:
        raise
    return get_post(db, post_id)


@router.post("/{post_id}/restore", response_model=PostOut)
async def restore_post_endpoint(post_id: int, db: Session = Depends(get_db)):
    """Restore an archived post to draft."""
    try:
        restore_post(db, post_id)
    except AppError:
        raise
    return get_post(db, post_id)


@router.post("/{post_id}/mark-posted", response_model=PostOut)
async def mark_posted_endpoint(post_id: int, req: MarkPostedRequest, db: Session = Depends(get_db)):
    """The user asserts they posted the content by hand (published_via='manual')."""
    try:
        mark_posted(db, post_id, external_url=req.external_url)
    except AppError:
        raise
    return get_post(db, post_id)
