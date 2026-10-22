"""Push notifications (PLAN_PHASE2 §9b).

One notify() entry point; provider by NOTIFY_PROVIDER; deduped by event_key so
a worker restart cannot re-send. Best-effort: notify() NEVER raises and never
blocks work. Notification credentials live only in env and never end up in
logs, the DB, API responses or message bodies.
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import NotificationLog

logger = logging.getLogger("app.services.notifications")

PROVIDERS = ("none", "telegram", "ntfy", "pushover")


def dashboard_url(settings, path: str) -> Optional[str]:
    """Absolute link for a push: PUBLIC_BASE_URL + dashboard path, or None."""
    base = settings.PUBLIC_BASE_URL
    if not base:
        return None
    return f"{base}{path}"


def _build_text(title: str, body: str, url: Optional[str]) -> str:
    text = title
    if body:
        text = f"{title}\n{body}"
    if url:
        text = f"{text}\n{url}"
    return text


def _send_telegram(http, settings, title: str, body: str, url: Optional[str]):
    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": _build_text(title, body, url),
        "disable_web_page_preview": True,
    }
    resp = http.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
    if resp.status_code >= 300:
        raise RuntimeError(f"telegram http {resp.status_code}")


def _send_ntfy(http, settings, title: str, body: str, url: Optional[str]):
    topic = settings.NTFY_TOPIC.get_secret_value()
    headers = {"Title": title}
    if url:
        headers["Click"] = url
    resp = http.post(f"{settings.NTFY_SERVER.rstrip('/')}/{topic}", content=_build_text(title, body, url), headers=headers)
    if resp.status_code >= 300:
        raise RuntimeError(f"ntfy http {resp.status_code}")


def _send_pushover(http, settings, title: str, body: str, url: Optional[str]):
    payload = {
        "token": settings.PUSHOVER_APP_TOKEN.get_secret_value(),
        "user": settings.PUSHOVER_USER_KEY.get_secret_value(),
        "title": title,
        "message": _build_text(title, body, url),
    }
    if url:
        payload["url"] = url
    resp = http.post("https://api.pushover.net/1/messages.json", data=payload)
    if resp.status_code >= 300:
        raise RuntimeError(f"pushover http {resp.status_code}")


def configured(settings=None) -> bool:
    settings = settings or get_settings()
    return settings.notify_configured


def notify(db: Session, event: str, key: str, title: str, body: str = "",
           url: Optional[str] = None, http=None) -> bool:
    """Send one push (provider by NOTIFY_PROVIDER). Never raises.

    Returns True when a push was actually sent; False when the provider is
    none/unconfigured, the key was already sent (dedupe), or the provider
    failed. The `notifications` table is the delivery log - no bodies.
    """
    settings = get_settings()
    provider = settings.NOTIFY_PROVIDER

    # 1. Delivery log first (event_key unique) -> worker restarts cannot re-send.
    #    A key whose earlier send FAILED is retried (same row); sent or
    #    "disabled" keys are final. ponytail: retries on every worker loop while
    #    the caller keeps asking; add backoff if a provider outage gets noisy.
    key = key[:120]
    log = db.execute(select(NotificationLog).where(NotificationLog.event_key == key)).scalar_one_or_none()
    if log is not None:
        if log.success or log.error_category == "disabled":
            return False
        log.provider = provider
    else:
        log = NotificationLog(event=event, event_key=key, provider=provider,
                              success=False, error_category=None)
        db.add(log)
        try:
            db.commit()
        except IntegrityError:  # concurrent insert of the same key
            db.rollback()
            return False

    if provider == "none" or not settings.notify_configured:
        # log-only: success stays False, marked "disabled" (PLAN §9b)
        log.error_category = "disabled"
        db.commit()
        return False

    link = dashboard_url(settings, url) if url else None
    try:
        if http is None:
            from app.platforms import http_client

            http = http_client(settings)
        if provider == "telegram":
            _send_telegram(http, settings, title, body, link)
        elif provider == "ntfy":
            _send_ntfy(http, settings, title, body, link)
        elif provider == "pushover":
            _send_pushover(http, settings, title, body, link)
        else:  # unreachable: validated by Settings
            raise RuntimeError(f"unknown provider {provider}")
        log.success = True
        db.commit()
        return True
    except Exception as e:  # never raise, never leak the exception text into UI
        logger.warning("notification %s failed: %s", event, e)
        log.success = False
        log.error_category = "http_error"
        db.commit()
        return False


def send_test(db: Session, http=None) -> bool:
    """Dashboard/API 'Send test notification' action (PLAN §9b/§12)."""
    from app.db import utcnow

    return notify(db, "test", f"test:{utcnow().isoformat(timespec='seconds')}",
                  "Test notification", "If you read this, pushes reach you.",
                  url="/")


def recent(db: Session, limit: int = 50) -> list[NotificationLog]:
    """Last N delivery-log rows (no secrets - PLAN §9b)."""
    rows = db.execute(
        select(NotificationLog).order_by(NotificationLog.created_at.desc()).limit(min(limit, 50))
    ).scalars().all()
    return list(rows)
