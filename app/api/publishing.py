"""Publishing API: publish now, retry, attempts (PLAN_PHASE2 §12).

Sync `def` endpoints: a publish can poll the platform for minutes, so FastAPI runs
them in its threadpool instead of blocking the event loop.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_platform_http
from app.db import get_db
from app.schemas import AttemptOut, PostOut, PublishRequest
from app.services import AppError
from app.services.posts import get_post
from app.services.publishing import list_attempts, publish_post, retry_post

router = APIRouter(prefix="/posts", tags=["publishing"])


@router.post("/{post_id}/publish", response_model=PostOut)
def publish_now(post_id: int, req: PublishRequest | None = None, db: Session = Depends(get_db),
                http=Depends(get_platform_http)):
    """Publish an approved/scheduled post now (live), or record a dry run. The outcome
    (published / scheduled for retry / failed) is on the returned post."""
    publish_post(db, post_id, trigger="manual", http=http, override_window=bool(req and req.override_window))
    return get_post(db, post_id)


@router.post("/{post_id}/retry", response_model=PostOut)
def retry(post_id: int, db: Session = Depends(get_db)):
    """failed -> scheduled again; the worker publishes it inside the posting window."""
    return retry_post(db, post_id)


@router.get("/{post_id}/attempts", response_model=list[AttemptOut])
def attempts(post_id: int, db: Session = Depends(get_db)):
    if get_post(db, post_id) is None:
        raise AppError("not_found", "Post not found", status=404)
    return list_attempts(db, post_id)
