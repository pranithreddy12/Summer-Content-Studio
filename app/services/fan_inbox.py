"""Fanvue fan inbox (PLAN_PHASE3 §8): sync waiting chats, escalate what a human must handle,
AI-drafted replies that Lee edits and sends himself, pay-to-view offers inside a reply,
and deletion of old message text (H8).

Nothing here sends a message on its own: `send_reply` runs only when Lee presses Send.
Only the fan's uuid, handle and display name are stored about a fan; message text is
deleted after FAN_DATA_RETENTION_DAYS and never logged.
"""

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings, publishing_mode
from app.db import utcnow
from app.models import FanChat, FanMessage, GenerationLog, MediaAsset, ModelProfile, PlatformAccount
from app.platforms import PlatformError, fanvue
from app.services import AppError, accounts
from app.services.claude_client import GenerationError

logger = logging.getLogger("app.services.fan_inbox")

OPEN = ("needs_reply", "escalated")      # waiting for Lee
SENDABLE = ("needs_reply", "escalated", "ignored")
HISTORY = 10                             # messages given to the AI
FETCH = 20                               # messages fetched per waiting chat
MAX_DRAFT = 400
MAX_SEND = 1000
DRAFTS_PER_HOUR = 20
AUTO_DRAFTS_PER_SYNC = 5
TRIGGER_SKIP = ("AUTOMATED_", "BROADCAST")  # Fanvue's own automated messages never trigger a reply

# Fixed safety escalations (cannot be removed by configuration). Each: (reason shown to Lee, pattern).
SAFETY_ESCALATIONS = (
    ("may involve a minor", r"\b(minor|underage|under ?age|under 18|1[0-7] ?(yo|y/o|yrs?|years? old)|teen|teenager|"
                            r"school ?girl|high ?school|(i'?m|i am|im|aged?|age is) 1[0-7])\b"),
    ("asks whether Summer is real or AI", r"\b(are|r) (you|u) (real|human|a bot|a robot|an? ai|ai|fake)\b|"
                                          r"\breal (person|girl|woman)\b|\b(is this|this is|you'?re) (a bot|ai|an ai)\b"),
    ("wants to meet or exchange contact details", r"\b(meet ?up|meet (you|me|in person)|hang out|your address|"
                                                  r"where do you live|phone number|your number|whats ?app|snapchat|"
                                                  r"telegram|call me|video call|face ?time)\b"),
    ("refund or payment problem", r"\b(refund|chargeback|charge back|dispute|scam(med)?|money back|"
                                  r"(didn'?t|did not|never) (get|receive)|not received)\b"),
    ("mentions self-harm", r"\b(kill myself|suicid\w*|self[- ]?harm|end my life|hurt myself)\b"),
    ("legal threat", r"\b(lawyer|solicitor|sue (you|u)|police|court|legal action|report (you|u))\b"),
)
_SAFETY = [(reason, re.compile(p, re.IGNORECASE)) for reason, p in SAFETY_ESCALATIONS]


# ---- pure rules ------------------------------------------------------------

def needs_escalation(text: str, rules: Optional[dict] = None) -> Optional[str]:
    """Reason a human must handle this text, or None. Safety defaults first, then Lee's own topics."""
    text = text or ""
    for reason, pattern in _SAFETY:
        if pattern.search(text):
            return reason
    for topic in (rules or {}).get("escalate_topics") or []:
        topic = str(topic).strip()
        if topic and re.search(r"\b" + re.escape(topic) + r"\b", text, re.IGNORECASE):
            return f"mentions '{topic[:60]}'"
    return None


def draft_problem(reply: str, rules: Optional[dict] = None) -> Optional[str]:
    """Why an AI draft must not be offered: a never-say word, or text that itself needs a human."""
    low = reply.lower()
    for word in (rules or {}).get("never_say") or []:
        if str(word).strip() and str(word).strip().lower() in low:
            return "draft used a never-say word"
    reason = needs_escalation(reply)
    return f"draft touched a sensitive topic ({reason})" if reason else None


def _ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---- sync (worker) ---------------------------------------------------------

def _accounts(db: Session) -> list[PlatformAccount]:
    return list(db.execute(select(PlatformAccount).where(
        PlatformAccount.platform == "fanvue", PlatformAccount.status == "connected")).scalars())


def sync(db: Session, now: datetime, http) -> dict:
    """Pull chats where the fan spoke last; store new messages; mark what needs Lee. One chat failing
    never stops the others. Pushes: one per escalation, one digest per 15 minutes."""
    from app.services.notifications import notify

    out = {"chats": 0, "new_messages": 0, "needs_reply": 0, "escalated": 0, "answered_elsewhere": 0, "errors": 0}
    for account in _accounts(db):
        profile = db.get(ModelProfile, account.model_profile_id)
        rules = (profile.fan_rules if profile else None) or {}
        try:
            token = accounts.get_valid_token(db, account, http, now)
            waiting, complete = fanvue.list_waiting_chats(http, token)
        except (PlatformError, AppError) as e:
            db.rollback()
            logger.warning("fan inbox sync failed for account %s: %s", account.id, getattr(e, "code", "error"))
            out["errors"] += 1
            continue

        seen: set[str] = set()
        for item in waiting:
            fan = item.get("user") or {}
            fan_uuid = str(fan.get("uuid") or "")
            if not fan_uuid:
                continue
            seen.add(fan_uuid)
            out["chats"] += 1
            try:
                state = _sync_chat(db, account, fan, item, token, http, rules, now)
            except (PlatformError, IntegrityError) as e:   # IntegrityError: a manual sync raced the worker
                db.rollback()
                logger.warning("fan chat sync failed: %s", getattr(e, "code", "duplicate"))
                out["errors"] += 1
                continue
            out["new_messages"] += state["new"]
            if state["escalated"]:
                out["escalated"] += 1
                notify(db, "fan_escalated", f"fan:{state['chat_id']}:{state['newest']}",
                       "A fan needs you", f"@{state['handle']}: {state['reason']}", url=f"/fans/{state['chat_id']}")
            elif state["needs_reply"]:
                out["needs_reply"] += 1

        if complete:  # a waiting chat that dropped off Fanvue's list was answered in the Fanvue app
            gone = db.execute(select(FanChat).where(
                FanChat.model_profile_id == account.model_profile_id, FanChat.status.in_(OPEN))).scalars().all()
            for chat in gone:
                if chat.fan_uuid not in seen:
                    chat.status, chat.draft_text, chat.escalation_reason = "replied", None, None
                    out["answered_elsewhere"] += 1
            db.commit()

    waiting_now = waiting_count(db)
    if out["needs_reply"] or out["escalated"]:
        bucket = now.strftime("%Y%m%d%H") + str(now.minute // 15)
        notify(db, "fans_waiting", f"fans:{bucket}", "Fans are waiting",
               f"{waiting_now} fan chat(s) waiting for a reply.", url="/fans")
    return out


def _sync_chat(db, account, fan: dict, item: dict, token: str, http, rules: dict, now: datetime) -> dict:
    chat = db.execute(select(FanChat).where(FanChat.model_profile_id == account.model_profile_id,
                                            FanChat.fan_uuid == str(fan["uuid"]))).scalar_one_or_none()
    if chat is None:
        chat = FanChat(model_profile_id=account.model_profile_id, fan_uuid=str(fan["uuid"]), status="replied")
        db.add(chat)
    chat.handle = str(fan.get("handle") or "")[:100] or None
    chat.display_name = str(fan.get("displayName") or "")[:100] or None
    chat.unread_count = int(item.get("unreadMessagesCount") or 0)
    chat.last_message_at = _ts(item.get("lastMessageAt")) or chat.last_message_at
    db.flush()

    messages = fanvue.get_messages(http, token, chat.fan_uuid, FETCH)
    known = set(db.execute(select(FanMessage.fanvue_message_uuid).where(
        FanMessage.fanvue_message_uuid.in_([str(m.get("uuid")) for m in messages]))).scalars())
    new_in: list[str] = []
    newest = None
    for m in sorted(messages, key=lambda m: m.get("sentAt") or ""):
        uuid = str(m.get("uuid") or "")
        if not uuid:
            continue
        outgoing = str((m.get("sender") or {}).get("uuid") or "") == account.external_user_id
        mtype = str(m.get("type") or "")
        newest = (uuid, outgoing, mtype)
        if uuid in known:
            continue
        db.add(FanMessage(chat_id=chat.id, fanvue_message_uuid=uuid, direction="out" if outgoing else "in",
                          text=m.get("text"), message_type=mtype[:40] or None, sent_at=_ts(m.get("sentAt")),
                          sent_via="fanvue", created_at=now))
        if not outgoing and not mtype.startswith(TRIGGER_SKIP):
            new_in.append(m.get("text") or "")

    state = {"chat_id": chat.id, "handle": chat.handle or chat.fan_uuid[:8], "new": len(new_in), "escalated": False,
             "needs_reply": False, "reason": None, "newest": newest[0] if newest else "none"}
    if newest and newest[1]:                     # Summer spoke last after all
        chat.status, chat.draft_text = "replied", None
    elif new_in:
        reason = needs_escalation(" \n".join(new_in), rules)
        if reason:
            chat.status, chat.escalation_reason, chat.draft_text = "escalated", reason, None
            state.update(escalated=True, reason=reason)
        elif chat.status != "escalated":         # an open escalation stays with Lee until he answers
            chat.status, chat.draft_text = "needs_reply", None
            state["needs_reply"] = True
    db.commit()
    return state


def waiting_count(db: Session) -> int:
    return db.execute(select(func.count(FanChat.id)).where(FanChat.status.in_(OPEN))).scalar() or 0


# ---- reads -----------------------------------------------------------------

def list_chats(db: Session, status: Optional[str] = None, limit: int = 200) -> list[FanChat]:
    q = select(FanChat)
    if status:
        q = q.where(FanChat.status == status)
    # escalated first, then needs_reply, newest activity first
    q = q.order_by((FanChat.status != "escalated"), (FanChat.status != "needs_reply"),
                   FanChat.last_message_at.desc())
    return list(db.execute(q.limit(limit)).scalars())


def get_chat(db: Session, chat_id: int) -> FanChat:
    chat = db.get(FanChat, chat_id)
    if chat is None:
        raise AppError("not_found", "Fan chat not found.", status=404)
    return chat


# ---- AI draft --------------------------------------------------------------

def drafts_last_hour(db: Session, now: datetime) -> int:
    return db.execute(select(func.count(GenerationLog.id)).where(
        GenerationLog.purpose == "fan_reply", GenerationLog.created_at >= now - timedelta(hours=1))).scalar() or 0


def draft_reply(db: Session, chat_id: int, client, extra_instructions: str = "",
                now: Optional[datetime] = None) -> FanChat:
    """Ask the AI for a draft. The result is only stored for Lee to edit; nothing is sent.
    The AI may escalate instead, and a draft that breaks a rule turns into an escalation."""
    from app.prompts import FAN_REPLY_PROMPT_VERSION, render_fan_reply_system, render_fan_reply_user

    now = now or utcnow()
    chat = get_chat(db, chat_id)
    if chat.status != "needs_reply":
        raise AppError("invalid_transition", f"This chat is '{chat.status}'; only chats waiting for a reply get "
                                             "AI drafts.", status=409)
    if drafts_last_hour(db, now) >= DRAFTS_PER_HOUR:
        raise AppError("rate_limited", f"At most {DRAFTS_PER_HOUR} AI drafts per hour.", status=429)
    profile = db.get(ModelProfile, chat.model_profile_id)
    rules = profile.fan_rules or {}
    history = [(m.direction, m.text or "") for m in chat.messages if m.text][-HISTORY:]
    if not history or history[-1][0] != "in":
        raise AppError("invalid_transition", "There is no fan message to answer.", status=409)

    started = time.time()
    try:
        result = client.generate(render_fan_reply_system(profile), render_fan_reply_user(history, extra_instructions))
    except GenerationError as e:
        _log(db, chat, getattr(e, "provider", None) or "anthropic", getattr(e, "model", None) or "unknown",
             started, False, e.category, now=now)
        raise
    _log(db, chat, getattr(result, "provider", "anthropic"), result.model, started, True, None, result, now=now)

    content = result.content
    text = " ".join((content.caption or "").split())
    if (content.content_format or "").strip().lower() == "escalate" or not text:
        return _escalate(db, chat, f"AI: {text[:150]}" if text else "AI declined to draft a reply")
    if len(text) > MAX_DRAFT:
        text = text[:MAX_DRAFT].rsplit(" ", 1)[0].rstrip(",;:- ")
    problem = draft_problem(text, rules)
    if problem:
        return _escalate(db, chat, problem)
    chat.draft_text, chat.draft_generated_at = text, now
    db.commit()
    return chat


def _escalate(db: Session, chat: FanChat, reason: str) -> FanChat:
    from app.services.notifications import notify

    chat.status, chat.escalation_reason, chat.draft_text = "escalated", reason[:200], None
    db.commit()
    # The reason may paraphrase the fan's words (AI-written): it stays in the dashboard, not in the push
    notify(db, "fan_escalated", f"fan:{chat.id}:draft:{int(time.time() // 900)}", "A fan needs you",
           f"@{chat.handle or 'fan'}: the AI could not draft a safe reply - open the chat.", url=f"/fans/{chat.id}")
    return chat


def _log(db, chat, provider, model, started, ok, category, result=None, now=None) -> None:
    """generation_logs row, purpose='fan_reply'. No prompt or reply text is stored."""
    from app.prompts import FAN_REPLY_PROMPT_VERSION

    db.add(GenerationLog(
        model_profile_id=chat.model_profile_id, media_asset_id=None, post_id=None, provider=provider, model=model,
        prompt_version=FAN_REPLY_PROMPT_VERSION, purpose="fan_reply", success=ok, error_category=category,
        input_tokens=getattr(result, "input_tokens", None), output_tokens=getattr(result, "output_tokens", None),
        duration_ms=int((time.time() - started) * 1000), request_id=getattr(result, "request_id", None),
        created_at=now or utcnow()))
    db.commit()


def has_draft_work(db: Session) -> bool:
    return db.execute(select(FanChat.id).where(FanChat.status == "needs_reply", FanChat.draft_text.is_(None))
                      .limit(1)).first() is not None


def auto_draft(db: Session, now: datetime, client) -> dict:
    """Worker: draft for chats waiting without a draft (a few per run, inside the hourly cap)."""
    out = {"drafted": 0, "escalated": 0, "errors": 0}
    if not getattr(client, "is_configured", True):   # no AI key: Lee writes replies himself, no log spam
        return out
    chats = db.execute(select(FanChat).where(FanChat.status == "needs_reply", FanChat.draft_text.is_(None))
                       .order_by(FanChat.last_message_at).limit(AUTO_DRAFTS_PER_SYNC)).scalars().all()
    for chat in chats:
        if drafts_last_hour(db, now) >= DRAFTS_PER_HOUR:
            break
        try:
            chat = draft_reply(db, chat.id, client, now=now)
            out["escalated" if chat.status == "escalated" else "drafted"] += 1
        except AppError as e:
            db.rollback()
            if e.code == "rate_limited":
                break
            if e.code != "invalid_transition":   # e.g. the fan sent only media: nothing to draft, not an error
                logger.warning("fan auto-draft failed: %s", e.code)
                out["errors"] += 1
        except GenerationError as e:
            db.rollback()
            logger.warning("fan auto-draft failed: %s", e.category)
            out["errors"] += 1
    return out


# ---- human actions ---------------------------------------------------------

def send_reply(db: Session, chat_id: int, text: str, http, asset_id: Optional[int] = None,
               price_cents: Optional[int] = None, now: Optional[datetime] = None) -> FanChat:
    """Send Lee's (edited) reply, optionally with paid media. Live mode only; one send per waiting chat."""
    from app.services.assets import resolve_asset_file
    from app.services.publishing import connected_account

    now = now or utcnow()
    settings = get_settings()
    text = (text or "").strip()
    if not text:
        raise AppError("validation_error", "Reply text is required.", status=422)
    if len(text) > MAX_SEND:
        raise AppError("validation_error", f"Reply is longer than {MAX_SEND} characters.", status=422)
    if (asset_id is None) != (price_cents is None):
        raise AppError("validation_error", "Paid media needs both a media item and a price.", status=422)
    if price_cents is not None and not settings.FANVUE_PPV_MIN_CENTS <= price_cents <= settings.FANVUE_PPV_MAX_CENTS:
        raise AppError("validation_error", f"Price must be between {settings.FANVUE_PPV_MIN_CENTS / 100:.2f} and "
                                           f"{settings.FANVUE_PPV_MAX_CENTS / 100:.2f}.", status=422)
    if publishing_mode() != "live":
        raise AppError("publishing_disabled", "Fan replies are sent in live mode only (PUBLISHING_MODE=live).",
                       status=409)
    chat = get_chat(db, chat_id)
    asset = None
    if asset_id is not None:
        asset = db.get(MediaAsset, asset_id)
        if asset is None or not asset.is_active or asset.media_type not in ("image", "video"):
            raise AppError("not_found", "Media not found (images and videos only).", status=404)
    account = connected_account(db, chat.model_profile_id, "fanvue")
    if account is None:
        raise AppError("account_not_connected", "Connect the Fanvue account first.", status=409)

    previous = chat.status
    claimed = db.execute(update(FanChat).where(FanChat.id == chat_id, FanChat.status.in_(SENDABLE))
                         .values(status="sending"))
    if claimed.rowcount == 0:
        db.rollback()
        raise AppError("already_sent", "A reply was already sent (or is being sent) to this fan.", status=409)
    db.commit()

    secrets_: list[str] = []
    try:
        token = accounts.get_valid_token(db, account, http, now)
        secrets_.append(token)
        media = None
        if asset is not None:
            if not asset.fanvue_media_uuid:
                try:
                    path = resolve_asset_file(asset)
                except Exception as e:
                    raise PlatformError("media_required", "The media file is not available on the server.", False) from e
                asset.fanvue_media_uuid = fanvue.upload_media(http, token, path, asset.filename, asset.media_type)
                db.commit()
            media = [asset.fanvue_media_uuid]
        message_uuid = fanvue.send_message(http, token, chat.fan_uuid, text, media, price_cents)
    except (PlatformError, AppError) as e:
        db.rollback()
        db.execute(update(FanChat).where(FanChat.id == chat_id).values(status=previous))
        db.commit()
        code = getattr(e, "code", "error")
        message = accounts.sanitize(getattr(e, "message", str(e)), *secrets_)
        if code == "token_expired":
            accounts.mark_attention(db, account, "expired", message)
            message = "Reconnect the Fanvue account."
        elif code in ("timeout", "connection_error"):
            message += " Check the chat in the Fanvue app before sending again: it may have gone through."
        raise AppError(code, message, status=502 if getattr(e, "transient", False) else 409) from e

    chat = get_chat(db, chat_id)
    db.add(FanMessage(chat_id=chat.id, fanvue_message_uuid=message_uuid, direction="out", text=text,
                      message_type="SINGLE_RECIPIENT", sent_at=now, ppv_price_cents=price_cents,
                      media_asset_id=asset.id if asset else None, sent_via="studio", created_at=now))
    chat.status, chat.draft_text, chat.escalation_reason = "replied", None, None
    chat.last_message_at = now
    db.commit()
    db.refresh(chat)
    return chat


def ignore_chat(db: Session, chat_id: int) -> FanChat:
    chat = get_chat(db, chat_id)
    if chat.status == "sending":
        raise AppError("invalid_transition", "A reply is being sent.", status=409)
    chat.status, chat.draft_text = "ignored", None
    db.commit()
    return chat


STUCK_SENDING = timedelta(minutes=5)


def reopen_chat(db: Session, chat_id: int, now: Optional[datetime] = None) -> FanChat:
    """Back to needs_reply. Also frees a chat left in 'sending' by a crash (after 5 minutes)."""
    now = now or utcnow()
    chat = get_chat(db, chat_id)
    stuck = chat.status == "sending" and chat.updated_at and now - chat.updated_at > STUCK_SENDING
    if chat.status not in ("ignored", "escalated", "replied") and not stuck:
        raise AppError("invalid_transition", f"Cannot reopen a chat that is '{chat.status}'.", status=409)
    chat.status, chat.escalation_reason = "needs_reply", None
    db.commit()
    return chat


# ---- retention (H8) --------------------------------------------------------

def purge_old(db: Session, now: datetime) -> dict:
    """Delete message rows older than FAN_DATA_RETENTION_DAYS, then chats left with no messages
    (an open escalation is kept so Lee still sees it)."""
    cutoff = now - timedelta(days=get_settings().FAN_DATA_RETENTION_DAYS)
    msgs = db.execute(delete(FanMessage).where(func.coalesce(FanMessage.sent_at, FanMessage.created_at) < cutoff))
    empty = select(FanMessage.id).where(FanMessage.chat_id == FanChat.id).exists()
    chats = db.execute(delete(FanChat).where(~empty, FanChat.status != "escalated"))
    db.commit()
    return {"messages": msgs.rowcount or 0, "chats": chats.rowcount or 0}
