"""Notifications API (PLAN_PHASE2 §12): test push + delivery log."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.services import AppError
from app.services.notifications import configured, notify, recent, send_test

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut:
    pass


@router.post("/test")
async def send_test_notification(db: Session = Depends(get_db)):
    settings = get_settings()
    if not configured(settings):
        raise AppError("not_configured",
                       f"NOTIFY_PROVIDER={settings.NOTIFY_PROVIDER} has no credentials.", status=503)
    ok = send_test(db)
    return {"sent": ok, "provider": settings.NOTIFY_PROVIDER}


@router.get("")
async def list_notifications(db: Session = Depends(get_db)):
    """Last 50 delivery-log rows - no message bodies, no secrets (PLAN §9b)."""
    rows = recent(db)
    return {
        "items": [
            {
                "id": r.id,
                "event": r.event,
                "event_key": r.event_key,
                "provider": r.provider,
                "success": r.success,
                "error_category": r.error_category,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }
