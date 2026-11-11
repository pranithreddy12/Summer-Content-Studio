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


def _send_telegram(http, settings, title: str, body: str, url: Optional[str], buttons=None):
    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": _build_text(title, body, url),
        "disable_web_page_preview": True,
    }
    if buttons:  # inline buttons under the message: (label, callback_data) taps + an "Open" link when there is one
        row = [{"text": label, "callback_data": data} for label, data in buttons]
        if url:
            row.append({"text": "Open", "url": url})
        payload["reply_markup"] = {"inline_keyboard": [row]}
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
           url: Optional[str] = None, http=None, buttons=None) -> bool:
    """Send one push (provider by NOTIFY_PROVIDER). Never raises. `buttons` = [(label, callback_data)] are shown
    under the message by Telegram only (see poll_telegram); the other providers ignore them.

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
    own_client = http is None   # a client made here is closed here (callers' clients are theirs)
    try:
        if own_client:
            from app.platforms import http_client

            http = http_client(settings)
        if provider == "telegram":
            _send_telegram(http, settings, title, body, link, buttons)
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
    finally:
        if own_client and http is not None:
            http.close()


# ---- Telegram: approve a draft by tapping its button ------------------------------------------------------------
# The worker asks Telegram for new taps once per iteration (getUpdates, no public webhook, nothing new exposed).
# A tap is honoured only from the configured TELEGRAM_CHAT_ID. Approving is the same human approval as the Approve
# button in the studio (posts.approve_post); nothing is published by it.
_tg = {"offset": None}  # next getUpdates offset, in memory: after a restart Telegram replays recent taps, and a replay is harmless


def _tg_call(http, token: str, method: str, **payload):
    resp = http.post(f"https://api.telegram.org/bot{token}/{method}", json=payload)
    if resp.status_code >= 300:
        raise RuntimeError(f"telegram {method} http {resp.status_code}")
    return resp.json().get("result")


def _tg_try(http, token: str, method: str, **payload) -> None:
    try:
        _tg_call(http, token, method, **payload)
    except Exception as e:  # e.g. the button is too old to answer: never matters
        logger.info("telegram %s skipped: %s", method, e)


def _handle_tap(db: Session, http, settings, token: str, tap: dict) -> int:
    from app.services import AppError
    from app.services.posts import approve_post

    message = tap.get("message") or {}
    answer = lambda text: _tg_try(http, token, "answerCallbackQuery", callback_query_id=tap.get("id"), text=text[:190])
    if str((message.get("chat") or {}).get("id")) != str(settings.TELEGRAM_CHAT_ID):
        logger.warning("telegram button tap from another chat ignored")
        answer("Not allowed.")
        return 0
    kind, _, raw = (tap.get("data") or "").partition(":")
    if kind != "approve" or not raw.isdigit() or int(raw) >= 2 ** 62:
        answer("Unknown action.")
        return 0
    approved = 0
    try:
        approve_post(db, int(raw))
        text, approved = f"Approved #{raw}", 1
    except AppError as e:
        db.rollback()
        text = f"#{raw}: {e.message}"
    answer(text)
    where = {"chat_id": message.get("chat", {}).get("id"), "message_id": message.get("message_id")}
    if approved:
        _tg_try(http, token, "editMessageText", **where, disable_web_page_preview=True,
                text=f"{message.get('text', '')}\n\n✅ Approved")  # also removes the buttons
    else:
        _tg_try(http, token, "editMessageReplyMarkup", **where, reply_markup={"inline_keyboard": []})
    return approved


def poll_telegram(db: Session, http=None) -> int:
    """Handle Approve-button taps waiting at Telegram. Returns how many drafts were approved. Never raises."""
    settings = get_settings()
    if settings.NOTIFY_PROVIDER != "telegram" or not settings.notify_configured:
        return 0
    own_client = http is None
    approved = 0
    try:
        if own_client:
            from app.platforms import http_client

            http = http_client(settings)
        token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
        params = {"timeout": 0, "allowed_updates": ["callback_query"]}
        if _tg["offset"] is not None:
            params["offset"] = _tg["offset"]
        for update in _tg_call(http, token, "getUpdates", **params) or []:
            _tg["offset"] = update["update_id"] + 1
            if update.get("callback_query"):
                approved += _handle_tap(db, http, settings, token, update["callback_query"])
    except Exception as e:
        logger.warning("telegram poll failed: %s", e)
    finally:
        if own_client and http is not None:
            http.close()
    return approved


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
