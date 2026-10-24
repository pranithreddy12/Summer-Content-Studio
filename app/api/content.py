"""Content generation API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_claude_client
from app.db import get_db
from app.schemas import GenerateRequest, RandomDraftOut, RandomDraftRequest, RegenerateRequest, PostOut, VariantOut
from app.security import generation_rate_limit
from app.services.generation import generate_draft, regenerate
from app.services.random_draft import generate_random_drafts

router = APIRouter(prefix="/content", tags=["content"])


@router.post("/generate", response_model=PostOut, status_code=status.HTTP_201_CREATED)
def generate_content(
    request: Request,
    req: GenerateRequest,
    db: Session = Depends(get_db),
    client=Depends(get_claude_client),
):
    """Generate a new draft post via Claude.

    Header `Idempotency-Key` overrides body idempotency_key.
    Returns 200 if idempotent replay, 201 if new.
    """
    # Rate limit
    generation_rate_limit(request)

    # Header overrides body
    idempotency_header = request.headers.get("Idempotency-Key")
    if idempotency_header:
        req.idempotency_key = idempotency_header

    # Check if idempotent replay (return 200 instead of 201)
    is_replay = False
    if req.idempotency_key:
        from sqlalchemy import select
        from app.models import ContentPost
        existing = db.execute(
            select(ContentPost).where(ContentPost.idempotency_key == req.idempotency_key)
        ).scalar_one_or_none()
        if existing:
            is_replay = True

    post = generate_draft(db, req, client)

    if is_replay:
        # Return existing post with 200
        from app.services.posts import get_post as _get_post
        post = _get_post(db, post.id)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=200,
            content=PostOut.model_validate(post).model_dump(mode="json"),
        )

    # Need to re-load with relationships for serialization
    from app.services.posts import get_post as _get_post
    post = _get_post(db, post.id)
    return post


@router.post("/{post_id}/regenerate", response_model=VariantOut, status_code=status.HTTP_201_CREATED)
def regenerate_content(
    post_id: int,
    req: RegenerateRequest,
    request: Request,
    db: Session = Depends(get_db),
    client=Depends(get_claude_client),
):
    """Generate a new variant for an existing post. Rate-limited separately from generate."""
    generation_rate_limit(request)
    variant = regenerate(db, post_id, req, client)
    return variant


@router.post("/generate-random", response_model=RandomDraftOut, status_code=status.HTTP_201_CREATED)
def generate_random(
    request: Request,
    req: RandomDraftRequest,
    db: Session = Depends(get_db),
    client=Depends(get_claude_client),
):
    """Pick random unused assets (uploaded/synced) and generate a draft for each. Drafts only - never published."""
    generation_rate_limit(request)
    return generate_random_drafts(db, req, client)
