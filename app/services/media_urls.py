"""Signed, expiring public media URLs (PLAN_PHASE2 §11).

Instagram fetches media from a public URL, so `GET /m/{token}` is the ONLY file route
that skips Basic auth. A token is HMAC-SHA256 signed, bound to one asset id and valid
for one hour; any failure is indistinguishable (404).
"""

import base64
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Optional

from app.config import get_settings

TTL = timedelta(hours=1)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    secret = get_settings().MEDIA_URL_SECRET
    if secret is None:
        raise ValueError("MEDIA_URL_SECRET is not set")
    return _b64(hmac.new(secret.get_secret_value().encode(), payload.encode(), hashlib.sha256).digest())


def make_media_token(asset_id: int, now: datetime, ttl: timedelta = TTL) -> str:
    payload = _b64(f"{asset_id}.{int((now + ttl).timestamp())}".encode())
    return f"{payload}.{_sign(payload)}"


def verify_media_token(token: str, now: datetime) -> Optional[int]:
    """The asset id for a valid, unexpired token, else None (never raises)."""
    try:
        payload, sig = token.split(".")
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        asset_id, exp = _unb64(payload).decode().split(".")
        return int(asset_id) if now.timestamp() <= int(exp) else None
    except Exception:
        return None


def media_url_for(asset_id: int, now: datetime) -> Optional[str]:
    """Absolute https URL the platform can fetch, or None when PUBLIC_BASE_URL/MEDIA_URL_SECRET are unset."""
    settings = get_settings()
    if not settings.PUBLIC_BASE_URL or settings.MEDIA_URL_SECRET is None:
        return None
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/m/{make_media_token(asset_id, now)}"
