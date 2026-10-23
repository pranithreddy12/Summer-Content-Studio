"""Engagement API (PLAN_PHASE2 §12): metrics, comments, human-sent replies.

Drafting calls the LLM and sending posts to Instagram, so both are rate limited and sync `def`
(threadpool). Nothing here sends a reply on its own: one explicit POST per comment.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_claude_client, get_platform_http
from app.db import get_db, utcnow
from app.schemas import CommentOut, DraftReplyOut, DraftReplyRequest, MetricOut, SendReplyRequest
from app.security import generation_rate_limit
from app.services import AppError, engagement as svc
from app.services.posts import get_post

router = APIRouter(tags=["engagement"])


@router.get("/posts/{post_id}/metrics")
def post_metrics(post_id: int, db: Session = Depends(get_db)):
    if get_post(db, post_id) is None:
        raise AppError("not_found", "Post not found", status=404)
    latest = svc.latest_metrics(db, post_id)
    return {"latest": MetricOut.model_validate(latest) if latest else None,
            "history": [MetricOut.model_validate(m) for m in svc.metrics_history(db, post_id)]}


@router.get("/comments")
def list_comments(platform: Optional[str] = None, post_id: Optional[int] = None,
                  reply_status: Optional[list[str]] = Query(None), limit: int = Query(50, ge=1, le=100),
                  offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    res = svc.list_comments(db, platform, post_id, reply_status, limit, offset)
    return {"items": [CommentOut.model_validate(c) for c in res["items"]], "total": res["total"]}


@router.post("/comments/{comment_id}/draft-reply", response_model=DraftReplyOut)
def draft_reply(comment_id: int, request: Request, req: DraftReplyRequest | None = None,
                db: Session = Depends(get_db), client=Depends(get_claude_client)):
    generation_rate_limit(request)
    comment, suggest_ignore = svc.draft_reply(db, comment_id, client, req.instructions if req else "")
    return DraftReplyOut(comment=CommentOut.model_validate(comment), suggest_ignore=suggest_ignore)


@router.post("/comments/{comment_id}/send-reply", response_model=CommentOut)
def send_reply(comment_id: int, req: SendReplyRequest, request: Request, db: Session = Depends(get_db),
               http=Depends(get_platform_http)):
    generation_rate_limit(request)
    return svc.send_reply(db, comment_id, req.text, http)


@router.post("/comments/{comment_id}/ignore", response_model=CommentOut)
def ignore_comment(comment_id: int, db: Session = Depends(get_db)):
    return svc.ignore_comment(db, comment_id)


@router.post("/engagement/sync")
def sync_now(db: Session = Depends(get_db), http=Depends(get_platform_http)):
    """Manual 'refresh now' (the worker does this every few minutes in live mode)."""
    from app.config import publishing_mode

    if publishing_mode() != "live":
        raise AppError("publishing_disabled", "Engagement sync runs in live mode only.", status=409)
    return svc.sync(db, utcnow(), http)
