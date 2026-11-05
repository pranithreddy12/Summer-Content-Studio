"""Fanvue API (PLAN_PHASE3 §6): OAuth 2.0 + PKCE, rotating refresh tokens, multipart
media upload, posts.

Every call is recorded in docs/PLATFORM_API_NOTES.md ("Fanvue", checked 2026-11-06
against https://api.fanvue.com/docs/openapi-v1.json and the guides
authentication/implementation-guide, tutorials/uploading-media). Fanvue has no
sandbox: tests use httpx.MockTransport only.

Rules that matter here:
- token endpoint auth is `client_secret_basic` (id/secret in the Basic header, never the body);
- refresh tokens are single-use and rotate: the caller persists BOTH new tokens before use,
  and only the worker refreshes (accounts.get_valid_token, PLAN_PHASE3 rule P3-5);
- we never send `publishAt`: our worker owns timing and the posting window.
"""

import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx

from app.platforms import PlatformError, PublishResult, TokenSet, callback_url, json_or_empty, send

API = "https://api.fanvue.com"
AUTHORIZE_URL = "https://auth.fanvue.com/oauth2/auth"
TOKEN_URL = "https://auth.fanvue.com/oauth2/token"
SYSTEM_SCOPES = ("openid", "offline_access", "offline")
POLL_SECONDS, POLL_MAX = 5, 120          # media processing: up to 10 minutes
RETRY_AFTER_MAX = 120                    # honour Retry-After inside a call up to this many seconds
SIGN_BATCH = 100                         # part URLs signed per request
RECOVERY_LOOKBACK = timedelta(minutes=5)
_sleep = time.sleep  # tests replace this


# ---- HTTP helpers -------------------------------------------------------------

def _headers(token: str, settings=None) -> dict:
    from app.config import get_settings

    settings = settings or get_settings()
    return {"Authorization": f"Bearer {token}", "X-Fanvue-API-Version": settings.FANVUE_API_VERSION,
            "Accept": "application/json"}


def _fail(resp: httpx.Response, what: str, ref: str | None = None) -> None:
    """Raise a classified PlatformError for an HTTP error response (codes in PLATFORM_API_NOTES)."""
    s = resp.status_code
    if s < 400:
        return
    body = json_or_empty(resp)
    detail = body.get("message") or body.get("error") or ""
    msg = f"Fanvue {what} failed ({s}){': ' + str(detail) if detail else ''}"[:300]
    if s == 401:
        raise PlatformError("token_expired", msg, False, s, ref)
    if s == 403:
        raise PlatformError("missing_scope", msg + " - reconnect Fanvue and allow the requested access.", False, s, ref)
    if s == 410:
        raise PlatformError("api_version_sunset", msg + " - FANVUE_API_VERSION must be updated.", False, s, ref)
    if s in (429, 503):
        raise PlatformError("rate_limited", msg, True, s, ref)
    if s >= 500:
        raise PlatformError("upstream_error", msg, True, s, ref)
    raise PlatformError("bad_request", msg, False, s, ref)


def _call(http, method: str, url: str, what: str, ref: str | None = None, **kwargs) -> httpx.Response:
    """send() + one in-call retry on 429/503 when Retry-After is short; then classify errors."""
    resp = send(http, method, url, **kwargs)
    if resp.status_code in (429, 503):
        try:
            wait = int(resp.headers.get("Retry-After", ""))
        except ValueError:
            wait = None
        if wait is not None and 0 <= wait <= RETRY_AFTER_MAX:
            _sleep(wait)
            resp = send(http, method, url, **kwargs)
    _fail(resp, what, ref)
    return resp


# ---- OAuth --------------------------------------------------------------------

def authorize_url(settings, state: str, code_challenge: str | None = None) -> str:
    if not code_challenge:
        raise PlatformError("bad_request", "Fanvue requires PKCE (code_challenge).", False)
    scope = " ".join(SYSTEM_SCOPES + tuple(settings.FANVUE_SCOPES.split()))
    q = {"client_id": settings.FANVUE_CLIENT_ID, "response_type": "code",
         "redirect_uri": callback_url(settings, "fanvue"), "scope": scope, "state": state,
         "code_challenge": code_challenge, "code_challenge_method": "S256"}
    return AUTHORIZE_URL + "?" + urlencode(q, quote_via=quote)


def _token_call(http, settings, **fields) -> dict:
    resp = send(http, "POST", TOKEN_URL, data=fields,
                auth=(settings.FANVUE_CLIENT_ID, settings.FANVUE_CLIENT_SECRET.get_secret_value()))
    body = json_or_empty(resp)
    if resp.status_code >= 400 or not body.get("access_token"):
        detail = str(body.get("error_description") or body.get("error") or f"HTTP {resp.status_code}")[:200]
        if resp.status_code >= 500:
            raise PlatformError("upstream_error", f"Fanvue sign-in service error: {detail}", True, resp.status_code)
        # invalid_grant (used/expired code or refresh token), invalid_client, ...: a human must reconnect
        raise PlatformError("token_expired", f"Fanvue refused the token request: {detail}", False, resp.status_code)
    return body


def _tokenset(body: dict, now: datetime, external_user_id: str, username: str,
              old_refresh: str | None = None) -> TokenSet:
    exp = now + timedelta(seconds=int(body["expires_in"])) if body.get("expires_in") else None
    scopes = [s for s in str(body.get("scope", "")).split() if s]
    return TokenSet(body["access_token"], body.get("refresh_token") or old_refresh, exp, None,
                    scopes, external_user_id, username)


def exchange_code(http, settings, code: str, code_verifier: str | None = None) -> TokenSet:
    if not code_verifier:
        raise PlatformError("bad_request", "Fanvue requires PKCE (code_verifier).", False)
    now = datetime.now(timezone.utc)
    body = _token_call(http, settings, grant_type="authorization_code", code=code,
                       redirect_uri=callback_url(settings, "fanvue"), code_verifier=code_verifier)
    me = json_or_empty(_call(http, "GET", f"{API}/v1/users/me", "profile lookup",
                             headers=_headers(body["access_token"], settings)))
    if not me.get("uuid"):
        raise PlatformError("bad_request", "Fanvue returned no account id.", False)
    # Only uuid + handle are kept; /users/me also returns the e-mail, which is never stored (H8)
    return _tokenset(body, now, str(me["uuid"]), str(me.get("handle") or me["uuid"]))


def refresh(http, settings, account) -> TokenSet:
    """New access AND refresh token (the old refresh token is now dead). The caller stores both."""
    now = datetime.now(timezone.utc)
    body = _token_call(http, settings, grant_type="refresh_token", refresh_token=account.refresh_token)
    return _tokenset(body, now, account.external_user_id, account.username, old_refresh=None)


# ---- media --------------------------------------------------------------------

def upload_media(http, token: str, path: Path, name: str, media_type: str) -> str:
    """Multipart upload (5 steps in PLATFORM_API_NOTES) -> media uuid once Fanvue says `ready`."""
    size = path.stat().st_size
    resp = _call(http, "POST", f"{API}/v1/media/uploads", "upload start", headers=_headers(token),
                 json={"name": name[:100], "filename": path.name, "mediaType": media_type, "sizeBytes": size})
    s = json_or_empty(resp)
    media_uuid, upload_id, part_size = s.get("mediaUuid"), s.get("uploadId"), s.get("partSize")
    if not media_uuid or not upload_id or not part_size:
        raise PlatformError("bad_request", "Fanvue returned no upload session.", False)
    total = s.get("totalParts") or max(1, math.ceil(size / part_size))

    parts = []
    with path.open("rb") as fh:
        for first in range(1, total + 1, SIGN_BATCH):
            last = min(total, first + SIGN_BATCH - 1)
            signed = json_or_empty(_call(http, "GET", f"{API}/v1/media/uploads/{upload_id}/parts/urls",
                                         "upload signing", media_uuid, headers=_headers(token),
                                         params={"from": first, "to": last}))
            urls = {int(p["partNumber"]): p["url"] for p in signed.get("parts") or []}
            for n in range(first, last + 1):
                if n not in urls:
                    raise PlatformError("bad_request", f"Fanvue did not sign upload part {n}.", False)
                fh.seek((n - 1) * part_size)
                put = send(http, "PUT", urls[n], content=fh.read(part_size))  # signed URL: NO auth header
                if put.status_code >= 500:
                    raise PlatformError("upstream_error", f"Fanvue upload part {n} failed ({put.status_code}).",
                                        True, put.status_code, "media:" + media_uuid)
                if put.status_code >= 300 or not put.headers.get("ETag"):
                    raise PlatformError("media_rejected", f"Fanvue rejected upload part {n} ({put.status_code}).",
                                        False, put.status_code)
                parts.append({"PartNumber": n, "ETag": put.headers["ETag"]})

    _call(http, "PATCH", f"{API}/v1/media/uploads/{upload_id}", "upload completion", headers=_headers(token),
          json={"parts": parts})
    for i in range(POLL_MAX):
        m = json_or_empty(_call(http, "GET", f"{API}/v1/media/{media_uuid}", "media status", headers=_headers(token)))
        if m.get("status") == "ready":
            return media_uuid
        if m.get("status") == "error":
            raise PlatformError("media_rejected", f"Fanvue could not process the media ({m.get('errorReason', 'unknown')}).",
                                False)
        if i < POLL_MAX - 1:
            _sleep(POLL_SECONDS)
    raise PlatformError("timeout", "Fanvue is still processing the media.", True, platform_ref="media:" + media_uuid)


# ---- posts --------------------------------------------------------------------

def _result(p: dict) -> PublishResult:
    published = p.get("publishedAt") or p.get("createdAt")
    when = datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None
    return PublishResult(str(p["uuid"]), None, when, str(p["uuid"]))


def publish(http, token: str, external_user_id: str, post, media_url: str | None, media_path=None,
            on_ref=None, caption: str | None = None) -> PublishResult:
    """Upload the asset once (uuid kept on the asset row), then create the post.

    `on_ref("media:<uuid>@<iso time>")` is called before the post is created; the publishing
    service commits it with the asset's new `fanvue_media_uuid`, so a crash can neither lose the
    upload nor create a second post blindly (check_status looks for it first)."""
    asset = post.asset
    if asset is None or asset.media_type not in ("image", "video"):
        raise PlatformError("media_not_publishable", "Fanvue posts need an image or a video.", False)
    media_uuid = asset.fanvue_media_uuid
    if not media_uuid:
        if media_path is None or not Path(media_path).is_file():
            raise PlatformError("media_required", "The media file is not available on the server.", False)
        media_uuid = upload_media(http, token, Path(media_path), asset.filename, asset.media_type)
        asset.fanvue_media_uuid = media_uuid
    if on_ref:
        on_ref(f"media:{media_uuid}@{datetime.now(timezone.utc).isoformat()}")

    body = {"text": caption if caption is not None else post.caption, "mediaUuids": [media_uuid],
            "audience": post.fanvue_audience or "subscribers"}
    if post.fanvue_price_cents:
        body["price"] = post.fanvue_price_cents
    resp = _call(http, "POST", f"{API}/v1/posts", "post creation", headers=_headers(token), json=body)
    created = json_or_empty(resp)
    if not created.get("uuid"):
        raise PlatformError("bad_request", "Fanvue returned no post id.", False)
    return _result(created)


def check_status(http, token: str, external_user_id: str, platform_ref: str) -> PublishResult | None:
    """Did an interrupted attempt create the post? Ref is a post uuid, or `media:<uuid>@<time>`
    (saved before POST /v1/posts): then look for a recent post carrying that media.
    # ponytail: matches on media uuid + time only; also compare caption if one asset is ever
    # posted twice within minutes."""
    if not platform_ref.startswith("media:"):
        resp = send(http, "GET", f"{API}/v1/posts/{platform_ref}", headers=_headers(token))
        if resp.status_code == 404:
            return None
        _fail(resp, "post lookup", platform_ref)
        return _result(json_or_empty(resp))

    media_uuid, _, since = platform_ref[len("media:"):].partition("@")
    start = datetime.fromisoformat(since) - RECOVERY_LOOKBACK if since else None
    params = {"size": 50, "includeUnpublished": "true"}
    if start:
        params["startDate"] = start.isoformat()
    resp = _call(http, "GET", f"{API}/v1/posts", "post lookup", headers=_headers(token), params=params)
    for p in json_or_empty(resp).get("data") or []:
        created = p.get("createdAt")
        if media_uuid in (p.get("mediaUuids") or []) and (
                start is None or not created or datetime.fromisoformat(created.replace("Z", "+00:00")) >= start):
            return _result(p)
    return None


def fetch_metrics(http, token: str, external_user_id: str, external_post_id: str) -> dict:
    resp = _call(http, "GET", f"{API}/v1/posts/{external_post_id}", "post metrics", headers=_headers(token))
    p = json_or_empty(resp)
    return {"likes": p.get("likesCount"), "comments": p.get("commentsCount"), "tips": p.get("tips")}


def _unsupported(*_a, **_k):
    raise PlatformError("unsupported", "Fanvue post comments are not part of Phase 3.", False)


fetch_comments = send_reply = _unsupported
