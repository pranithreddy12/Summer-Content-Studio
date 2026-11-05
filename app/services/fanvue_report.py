"""Fanvue promotion links and reports (PLAN_PHASE3 §7, §10).

- Tracking links: one per Instagram/TikTok bio (Lee pastes them himself) plus campaign links.
  Attribution is per LINK, not per post: a bio holds one link.
- Report: earnings (net, USD cents) and subscribers fetched live from Fanvue; nothing about
  earnings is stored here. A weekly Telegram digest summarises the last 7 days.
"""

import re
import threading
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings, publishing_mode
from app.db import utcnow
from app.models import NotificationLog, PlatformAccount, TrackingLink
from app.platforms import PlatformError, fanvue
from app.services import AppError, accounts

BIO_LINKS = (("summer-instagram-bio", "instagram"), ("summer-tiktok-bio", "tiktok"))
MAX_LINKS = 20
LINK_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{2,59}$")
SYNC_EVERY = timedelta(hours=6)
CACHE_TTL = timedelta(minutes=10)
SOURCES = ("subs", "renewals", "messages", "posts", "tips", "referrals", "other")

# ponytail: in-process cache (one web process); a restart just refetches
_cache: dict[tuple, tuple[datetime, dict]] = {}
_cache_lock = threading.Lock()


def money(cents: Optional[float]) -> str:
    return "-" if cents is None else f"${cents / 100:,.2f}"


def _account(db: Session) -> PlatformAccount:
    acct = db.execute(select(PlatformAccount).where(
        PlatformAccount.platform == "fanvue", PlatformAccount.status == "connected")
        .order_by(PlatformAccount.id)).scalars().first()
    if acct is None:
        raise AppError("account_not_connected", "Connect the Fanvue account first.", status=409)
    return acct


def _live_account(db: Session) -> PlatformAccount:
    if publishing_mode() != "live":
        raise AppError("mode_not_live", "Fanvue is only contacted in live mode (PUBLISHING_MODE=live).", status=409)
    return _account(db)


def _wrap(e: PlatformError, *secrets_) -> AppError:
    return AppError(e.code, accounts.sanitize(e.message, *secrets_), status=502 if e.transient else 409)


# ---- links -------------------------------------------------------------------

def list_links(db: Session) -> list[TrackingLink]:
    return list(db.execute(select(TrackingLink).order_by(TrackingLink.created_at, TrackingLink.id)).scalars())


def _upsert(db: Session, profile_id: int, item: dict, now: datetime) -> TrackingLink:
    link = db.execute(select(TrackingLink).where(TrackingLink.fanvue_uuid == str(item["uuid"]))).scalar_one_or_none()
    if link is None:
        link = TrackingLink(model_profile_id=profile_id, fanvue_uuid=str(item["uuid"]), created_at=now)
        db.add(link)
    eng = item.get("engagement") or {}
    earn = item.get("earnings") or {}
    link.name = str(item.get("name") or link.name or "link")[:100]
    link.platform = str(item.get("externalSocialPlatform") or link.platform or "other")[:20]
    link.link_url = str(item.get("linkUrl") or link.link_url or "")[:500]
    link.clicks = int(item.get("clicks") or 0)
    link.acquired_subscribers = int(eng.get("acquiredSubscribers") or 0)
    link.acquired_followers = int(eng.get("acquiredFollowers") or 0)
    link.earnings_net_cents = int(earn["totalNet"]) if earn.get("totalNet") is not None else None
    link.synced_at = now
    return link


def create_link(db: Session, http, name: str, platform: str, now: Optional[datetime] = None) -> TrackingLink:
    """Create one tracking link on Fanvue (live mode). Names: lowercase letters, digits, dashes."""
    now = now or utcnow()
    name = (name or "").strip().lower()
    if not LINK_NAME.match(name):
        raise AppError("validation_error", "Link name: 3-60 lowercase letters, digits or dashes "
                                           "(e.g. halloween-2027-ig).", status=422)
    if platform not in fanvue.LINK_PLATFORMS:
        raise AppError("validation_error", f"Platform must be one of: {', '.join(fanvue.LINK_PLATFORMS)}.", status=422)
    if db.execute(select(TrackingLink.id).where(TrackingLink.name == name)).first():
        raise AppError("duplicate", f"A link called '{name}' already exists.", status=409)
    if (db.execute(select(func.count(TrackingLink.id))).scalar() or 0) >= MAX_LINKS:
        raise AppError("limit_reached", f"At most {MAX_LINKS} tracking links.", status=409)
    acct = _live_account(db)
    token = None
    try:
        token = accounts.get_valid_token(db, acct, http, now)
        item = fanvue.create_tracking_link(http, token, name, platform)
    except PlatformError as e:
        raise _wrap(e, token) from e
    link = _upsert(db, acct.model_profile_id, item, now)
    db.commit()
    return link


def ensure_bio_links(db: Session, http, now: Optional[datetime] = None) -> list[TrackingLink]:
    """The two bio links, created once. Re-reads Fanvue first so a lost DB never creates duplicates."""
    now = now or utcnow()
    acct = _live_account(db)
    names = {l.name for l in list_links(db)}
    if all(n in names for n, _ in BIO_LINKS):
        return [l for l in list_links(db) if l.name in dict(BIO_LINKS)]
    token = None
    try:
        token = accounts.get_valid_token(db, acct, http, now)
        for item in fanvue.list_tracking_links(http, token):
            _upsert(db, acct.model_profile_id, item, now)
        db.commit()
        names = {l.name for l in list_links(db)}
        for name, platform in BIO_LINKS:
            if name not in names:
                _upsert(db, acct.model_profile_id, fanvue.create_tracking_link(http, token, name, platform), now)
                db.commit()
    except PlatformError as e:
        db.rollback()
        raise _wrap(e, token) from e
    return [l for l in list_links(db) if l.name in dict(BIO_LINKS)]


def sync_links(db: Session, http, now: datetime) -> dict:
    """Worker: refresh clicks / subscribers / earnings for every link (also ones made in the Fanvue app)."""
    acct = _account(db)
    token = accounts.get_valid_token(db, acct, http, now)
    items = fanvue.list_tracking_links(http, token)
    for item in items:
        if item.get("uuid"):
            _upsert(db, acct.model_profile_id, item, now)
    db.commit()
    return {"links": len(items)}


# ---- report ------------------------------------------------------------------

def _period(days: int, now: datetime, tz: str) -> tuple[datetime, datetime]:
    """The last `days` whole local days, ending at today's local midnight."""
    local_midnight = now.astimezone(ZoneInfo(tz)).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight - timedelta(days=days), local_midnight


def summary(db: Session, http, days: int = 7, now: Optional[datetime] = None, use_cache: bool = True) -> dict:
    """Net/gross earnings by source and subscriber changes for the last `days` days (live fetch,
    cached for 10 minutes). Amounts are USD cents; `net` is Lee's cut after Fanvue fees."""
    if days not in (7, 30):
        raise AppError("validation_error", "days must be 7 or 30.", status=422)
    now = now or utcnow()
    tz = get_settings().APP_TIMEZONE
    start, end = _period(days, now, tz)
    key = (days, start.isoformat())
    with _cache_lock:
        hit = _cache.get(key)
        if use_cache and hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    acct = _live_account(db)
    token = None
    try:
        token = accounts.get_valid_token(db, acct, http, now)
        earn = fanvue.earnings_summary(http, token, start, end, tz)
        subs = fanvue.subscriber_events(http, token, start, end)
    except PlatformError as e:
        raise _wrap(e, token) from e

    by_source = earn.get("breakdownBySource") or {}
    sources = [{"source": s, "net": (by_source.get(s) or {}).get("net"), "gross": (by_source.get(s) or {}).get("gross")}
               for s in SOURCES if by_source.get(s)]
    known = [x["net"] for x in sources if x["net"] is not None]
    out = {
        "days": days, "start": start.isoformat(), "end": end.isoformat(), "timezone": tz,
        "net": sum(known) if known else None,
        "gross": sum(x["gross"] for x in sources if x["gross"] is not None) if sources else None,
        "sources": sources,
        "this_month_net": ((earn.get("totals") or {}).get("thisMonth") or {}).get("net"),
        "new_subscribers": sum(int(r.get("newSubscribersCount") or 0) for r in subs),
        "cancelled_subscribers": sum(int(r.get("cancelledSubscribersCount") or 0) for r in subs),
        "fetched_at": now.isoformat(),
    }
    with _cache_lock:
        _cache[key] = (now, out)
    return out


def digest_text(s: dict, links: list[TrackingLink]) -> str:
    parts = ", ".join(f"{x['source']} {money(x['net'])}" for x in s["sources"] if x["net"])
    best = max(links, key=lambda l: l.acquired_subscribers, default=None)
    lines = [f"Last 7 days: {money(s['net'])} net" + (f" ({parts})" if parts else "") + ".",
             f"Subscribers: +{s['new_subscribers']} new, -{s['cancelled_subscribers']} cancelled."]
    if best is not None and best.acquired_subscribers:
        lines.append(f"Best link: {best.name} ({best.acquired_subscribers} subscribers).")
    return " ".join(lines)


def weekly_digest(db: Session, http, now: datetime) -> bool:
    """On FANVUE_REPORT_WEEKDAY from FANVUE_REPORT_HOUR (APP_TIMEZONE): one push per ISO week."""
    from app.services.notifications import notify

    s = get_settings()
    local = now.astimezone(ZoneInfo(s.APP_TIMEZONE))
    if local.weekday() != s.FANVUE_REPORT_WEEKDAY or local.hour < s.FANVUE_REPORT_HOUR:
        return False
    year, week, _ = local.isocalendar()
    key = f"fanvue_weekly:{year}-W{week:02d}"
    if db.execute(select(NotificationLog.id).where(NotificationLog.event_key == key,
                                                   NotificationLog.success == True)).first():  # noqa: E712
        return False
    data = summary(db, http, 7, now, use_cache=False)
    return notify(db, "fanvue_weekly", key, "Fanvue weekly report", digest_text(data, list_links(db)), url="/fanvue")
