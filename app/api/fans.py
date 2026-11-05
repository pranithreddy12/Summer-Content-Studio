"""Fan inbox API (PLAN_PHASE3 §11). Thin: the rules live in services/fan_inbox.py.

Drafting calls the LLM and sending talks to Fanvue, so both are rate limited and sync `def`
(threadpool). Nothing here sends on its own: one explicit POST per reply.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_claude_client, get_platform_http
from app.config import publishing_mode
from app.db import get_db, utcnow
from app.schemas import FanChatDetailOut, FanChatOut, FanDraftRequest, FanSendRequest
from app.security import generation_rate_limit
from app.services import AppError
from app.services import fan_inbox as svc
from app.services.claude_client import GenerationError

router = APIRouter(prefix="/fans", tags=["fans"])

STATUSES = ("needs_reply", "escalated", "replied", "ignored", "sending")


@router.get("/chats", response_model=list[FanChatOut])
def list_chats(status: Optional[str] = None, db: Session = Depends(get_db)):
    if status is not None and status not in STATUSES:
        raise AppError("validation_error", f"status must be one of: {', '.join(STATUSES)}", status=422)
    return svc.list_chats(db, status)


@router.get("/chats/{chat_id}", response_model=FanChatDetailOut)
def get_chat(chat_id: int, db: Session = Depends(get_db)):
    return svc.get_chat(db, chat_id)


@router.post("/chats/{chat_id}/draft", response_model=FanChatOut)
def draft(chat_id: int, request: Request, req: FanDraftRequest | None = None, db: Session = Depends(get_db),
          client=Depends(get_claude_client)):
    generation_rate_limit(request)
    try:
        return svc.draft_reply(db, chat_id, client, req.instructions if req else "")
    except GenerationError as e:  # provider problem: same shape as /content/generate
        raise HTTPException(status_code=e.http_status, detail={"code": e.category, "message": e.message})


@router.post("/chats/{chat_id}/send", response_model=FanChatOut)
def send(chat_id: int, req: FanSendRequest, request: Request, db: Session = Depends(get_db),
         http=Depends(get_platform_http)):
    generation_rate_limit(request)
    return svc.send_reply(db, chat_id, req.text, http, req.asset_id, req.price_cents)


@router.post("/chats/{chat_id}/ignore", response_model=FanChatOut)
def ignore(chat_id: int, db: Session = Depends(get_db)):
    return svc.ignore_chat(db, chat_id)


@router.post("/chats/{chat_id}/reopen", response_model=FanChatOut)
def reopen(chat_id: int, db: Session = Depends(get_db)):
    return svc.reopen_chat(db, chat_id)


@router.post("/sync")
def sync_now(db: Session = Depends(get_db), http=Depends(get_platform_http)):
    """Manual 'refresh now' (the worker does this every 2 minutes in live mode)."""
    if publishing_mode() != "live":
        raise AppError("mode_not_live", "The fan inbox syncs in live mode only.", status=409)
    return svc.sync(db, utcnow(), http)
