"""Platform account connections (PLAN_PHASE2 §6.2/§12/§15): OAuth connect flow,
Fernet-encrypted tokens at rest, refresh, disconnect.

Tokens are secrets: encrypted in the DB, never logged, never returned by the API,
never in exception messages. OAuth `state` is random, single-use and expires after
10 minutes; redirect URIs are built only from PUBLIC_BASE_URL.
"""

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.models import AccountStatus, ModelProfile, OAuthState, PlatformAccount
from app.platforms import PlatformError, publisher
from app.services import AppError

logger = logging.getLogger("app.services.accounts")

PLATFORMS = ("instagram", "tiktok", "fanvue")
PKCE_PLATFORMS = ("fanvue",)        # OAuth flows that require a code_verifier
WORKER_ONLY_REFRESH = ("fanvue",)   # rotating refresh tokens with reuse detection (PLAN_PHASE3 P3-5)

# True only inside `python -m app.cli worker`: the one process allowed to refresh rotating tokens.
# A web request and the worker refreshing at the same moment would present the same single-use
# refresh token twice, and Fanvue's reuse detection would revoke the connection.
is_worker = False
STATE_TTL = timedelta(minutes=10)
REFRESH_SKEW = timedelta(minutes=5)  # refresh when less than this is left

# Env vars each platform needs before OAuth can start (names only are ever shown in the UI)
REQUIRED_ENV = {
    "instagram": ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET", "INSTAGRAM_CONFIG_ID"),
    "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"),
    "fanvue": ("FANVUE_CLIENT_ID", "FANVUE_CLIENT_SECRET"),
}
COMMON_ENV = ("PUBLIC_BASE_URL", "TOKEN_ENCRYPTION_KEY")


@dataclass
class TokenView:
    """Decrypted view of an account handed to platform modules (never persisted or serialised)."""

    access_token: str
    refresh_token: Optional[str]
    scopes: list
    external_user_id: str
    username: str


# ---- encryption ------------------------------------------------------------

def _fernet(settings=None) -> Fernet:
    settings = settings or get_settings()
    key = settings.TOKEN_ENCRYPTION_KEY
    if key is None:
        raise AppError("not_configured", "TOKEN_ENCRYPTION_KEY is not set. See README.", status=503)
    try:
        return Fernet(key.get_secret_value().encode())
    except (ValueError, TypeError) as e:
        raise AppError("not_configured", "TOKEN_ENCRYPTION_KEY is not a valid Fernet key.", status=503) from e


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise AppError("not_configured", "Stored token cannot be decrypted (was TOKEN_ENCRYPTION_KEY changed?). "
                                         "Reconnect the account.", status=503) from e


def sanitize(message: str, *secrets_: Optional[str]) -> str:
    """Strip token values and `access_token=...` fragments from a message; cap at 500 chars."""
    import re

    text = message or ""
    for s in secrets_:
        if s:
            text = text.replace(s, "[redacted]")
    text = re.sub(r"(access_token|client_secret|refresh_token)=[^&\s\"']+", r"\1=[redacted]", text)
    return text[:500]


def view(account: PlatformAccount) -> TokenView:
    return TokenView(
        access_token=decrypt(account.access_token_enc),
        refresh_token=decrypt(account.refresh_token_enc) if account.refresh_token_enc else None,
        scopes=list(account.scopes or []),
        external_user_id=account.external_user_id,
        username=account.username,
    )


# ---- configuration ---------------------------------------------------------

def missing_env(platform: str, settings=None) -> list[str]:
    """Env var NAMES still needed before `platform` can be connected."""
    settings = settings or get_settings()
    out = []
    for name in COMMON_ENV + REQUIRED_ENV[platform]:
        if not getattr(settings, name, None):
            out.append(name)
    return out


def _check_platform(platform: str) -> None:
    if platform not in PLATFORMS:
        raise AppError("not_found", f"Unknown platform '{platform}'.", status=404)


# ---- OAuth flow ------------------------------------------------------------

def start_oauth(db: Session, platform: str, profile_id: int, now: Optional[datetime] = None) -> str:
    """Create a single-use state and return the platform authorize URL."""
    _check_platform(platform)
    settings = get_settings()
    missing = missing_env(platform, settings)
    if missing:
        raise AppError("not_configured", "Missing configuration: " + ", ".join(missing), status=400)
    _fernet(settings)  # fail early on an invalid key
    if db.get(ModelProfile, profile_id) is None:
        raise AppError("not_found", "Profile not found.", status=404)

    now = now or utcnow()
    db.execute(delete(OAuthState).where(OAuthState.created_at < now - STATE_TTL))
    state = secrets.token_urlsafe(32)
    verifier = challenge = None
    if platform in PKCE_PLATFORMS:  # RFC 7636 S256; the verifier never leaves the server
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    db.add(OAuthState(state=state, platform=platform, model_profile_id=profile_id, code_verifier=verifier,
                      created_at=now))
    db.commit()
    return publisher(platform).authorize_url(settings, state, challenge)


def complete_oauth(db: Session, platform: str, code: str, state: str, http,
                   now: Optional[datetime] = None) -> PlatformAccount:
    """Validate + burn the state, exchange the code, store encrypted tokens (upsert per profile+platform)."""
    _check_platform(platform)
    now = now or utcnow()
    row = db.get(OAuthState, state) if state else None
    valid = row is not None and row.platform == platform and now - row.created_at <= STATE_TTL
    profile_id = row.model_profile_id if row is not None else None
    verifier = row.code_verifier if row is not None else None
    if row is not None:  # single use: burn it whether or not it was valid
        db.delete(row)
        db.commit()
    if not valid:
        raise AppError("invalid_state", "The connection link expired or was already used. Start again.", status=400)

    settings = get_settings()
    try:
        ts = publisher(platform).exchange_code(http, settings, code, verifier)
    except PlatformError as e:
        raise AppError(e.code, sanitize(e.message, code, verifier), status=502 if e.transient else 400) from e

    account = db.execute(select(PlatformAccount).where(
        PlatformAccount.model_profile_id == profile_id, PlatformAccount.platform == platform)).scalar_one_or_none()
    if account is None:
        account = PlatformAccount(model_profile_id=profile_id, platform=platform)
        db.add(account)
    _store(account, ts)
    db.commit()
    db.refresh(account)
    return account


def _store(account: PlatformAccount, ts) -> None:
    account.external_user_id = ts.external_user_id
    account.username = ts.username
    account.access_token_enc = encrypt(ts.access_token)
    account.refresh_token_enc = encrypt(ts.refresh_token) if ts.refresh_token else None
    account.token_expires_at = ts.expires_at
    account.refresh_expires_at = ts.refresh_expires_at
    account.scopes = list(ts.scopes or [])
    account.status = AccountStatus.connected.value
    account.last_error = None


# ---- tokens ----------------------------------------------------------------

def mark_attention(db: Session, account: PlatformAccount, status: str, message: str) -> None:
    """Flag an account (expired/error), keep a sanitized reason, push one alert per day."""
    account.status = status
    account.last_error = sanitize(message)[:300]
    db.commit()
    from app.services.notifications import notify

    notify(db, "account_attention", f"acct:{account.id}:{status}:{utcnow().date().isoformat()}",
           f"{account.platform.title()} needs attention",
           f"{account.platform.title()} account '{account.username}' is {status}: reconnect it on the Accounts page.",
           url="/accounts")


def refresh_account(db: Session, account: PlatformAccount, http) -> PlatformAccount:
    """Refresh the stored token. A refresh failure marks the account `expired` and re-raises."""
    settings = get_settings()
    v = view(account)
    try:
        ts = publisher(account.platform).refresh(http, settings, v)
    except PlatformError as e:
        if e.code == "token_expired":
            mark_attention(db, account, AccountStatus.expired.value, sanitize(e.message, v.access_token, v.refresh_token))
        raise
    _store(account, ts)
    db.commit()
    return account


def get_valid_token(db: Session, account: PlatformAccount, http, now: Optional[datetime] = None) -> str:
    """Decrypted access token, refreshed first when it expires within 5 minutes.

    Fanvue tokens are refreshed only by the worker (`is_worker`); elsewhere a still-valid token is
    used as-is and an expired one means the worker is not running."""
    now = now or utcnow()
    if account.status != AccountStatus.connected.value:
        raise PlatformError("account_not_connected", f"Account is {account.status}; reconnect it.", False)
    if account.token_expires_at and account.token_expires_at - now < REFRESH_SKEW:
        if account.platform in WORKER_ONLY_REFRESH and not is_worker:
            if account.token_expires_at <= now:
                raise PlatformError("worker_not_running", f"The {account.platform.title()} connection is kept fresh "
                                    "by the worker, which seems to be stopped. Start it and try again.", True)
        else:
            refresh_account(db, account, http)
    return decrypt(account.access_token_enc)


def accounts_expiring(db: Session, now: datetime, window: timedelta) -> list[PlatformAccount]:
    return list(db.execute(select(PlatformAccount).where(
        PlatformAccount.status == AccountStatus.connected.value,
        PlatformAccount.token_expires_at.isnot(None),
        PlatformAccount.token_expires_at <= now + window)).scalars())


def disconnect(db: Session, account_id: int) -> PlatformAccount:
    """Wipe the tokens and mark the account revoked (the row stays for history)."""
    account = db.get(PlatformAccount, account_id)
    if account is None:
        raise AppError("not_found", "Account not found.", status=404)
    account.access_token_enc = ""
    account.refresh_token_enc = None
    account.token_expires_at = account.refresh_expires_at = None
    account.status = AccountStatus.revoked.value
    account.last_error = None
    db.commit()
    return account


def list_accounts(db: Session) -> list[PlatformAccount]:
    return list(db.execute(select(PlatformAccount).order_by(PlatformAccount.id)).scalars())
