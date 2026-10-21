"""Platform modules package (PLAN_PHASE2 §7).

B1/B2 slice: error/result types + the injectable http client. The platform
modules (instagram.py, tiktok.py) are built in milestone B4 against verified
official docs (rule 10).
"""

from dataclasses import dataclass, field
from datetime import datetime

import httpx


@dataclass
class PublishResult:
    external_post_id: str
    external_url: str | None
    published_at: datetime | None
    platform_ref: str | None  # container/publish id for stuck-claim checks


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    refresh_expires_at: datetime | None
    scopes: list[str]
    external_user_id: str
    username: str


class PlatformError(Exception):
    """Error raised by platform modules with a classified code (PLAN §8)."""

    def __init__(self, code: str, message: str, transient: bool,
                 http_status: int | None = None, platform_ref: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.transient = transient
        self.http_status = http_status
        self.platform_ref = platform_ref


def http_client(settings=None, timeout: float | None = None) -> httpx.Client:
    """The one injectable httpx client for all platform HTTP (rule 2).

    Tests override this via dependency injection / monkeypatching with an
    httpx.MockTransport client - nothing in the codebase constructs clients
    for platform calls elsewhere.
    """
    from app.config import get_settings

    settings = settings or get_settings()
    return httpx.Client(timeout=timeout or settings.PLATFORM_TIMEOUT_SECONDS)
