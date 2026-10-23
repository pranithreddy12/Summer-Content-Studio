"""Instagram API with Instagram Login (host graph.instagram.com).

Every call below is recorded in docs/PLATFORM_API_NOTES.md (checked 2026-10-23);
items marked UNVERIFIED there are proven live in milestone L2.
Docs: https://developers.facebook.com/docs/instagram-platform/content-publishing
"""

import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from app.platforms import PlatformError, PublishResult, TokenSet, callback_url, json_or_empty, send

API_VERSION = "v26.0"
BASE = f"https://graph.instagram.com/{API_VERSION}"
SCOPES = ["instagram_business_basic", "instagram_business_content_publish", "instagram_business_manage_comments"]
POLL_SECONDS = 60   # docs: once a minute ...
POLL_MAX = 5        # ... for no more than 5 minutes
_sleep = time.sleep  # tests replace this

RATE_CODES = {4, 17, 32, 613}


def _fail(resp: httpx.Response, ref: str | None = None) -> None:
    """Raise a classified PlatformError for a non-2xx Graph response."""
    if resp.status_code < 400:
        return
    err = json_or_empty(resp).get("error") or {}
    msg = str(err.get("message") or f"Instagram API error {resp.status_code}")[:300]
    code = err.get("code")
    if resp.status_code >= 500:
        raise PlatformError("upstream_error", msg, True, resp.status_code, ref)
    if resp.status_code == 429 or code in RATE_CODES:
        raise PlatformError("rate_limited", msg, True, resp.status_code, ref)
    if resp.status_code == 401 or code == 190:
        raise PlatformError("token_expired", msg, False, resp.status_code, ref)
    if code == 368:
        raise PlatformError("policy_rejected", msg, False, resp.status_code, ref)
    raise PlatformError("bad_request", msg, False, resp.status_code, ref)


def _get(http, path: str, token: str, ref=None, **params) -> dict:
    resp = send(http, "GET", f"{BASE}/{path}", params={**params, "access_token": token})
    _fail(resp, ref)
    return json_or_empty(resp)


def _post(http, path: str, token: str, ref=None, **fields) -> dict:
    resp = send(http, "POST", f"{BASE}/{path}", data={**fields, "access_token": token})
    _fail(resp, ref)
    return json_or_empty(resp)


def _token_and_expiry(body: dict, now: datetime) -> tuple[str, datetime | None]:
    if not body.get("access_token"):
        raise PlatformError("bad_request", "Instagram returned no access token.", False)
    expires = now + timedelta(seconds=int(body["expires_in"])) if body.get("expires_in") else None
    return body["access_token"], expires


def authorize_url(settings, state: str, code_challenge: str | None = None) -> str:
    q = {"client_id": settings.INSTAGRAM_APP_ID, "redirect_uri": callback_url(settings, "instagram"),
         "response_type": "code", "scope": ",".join(SCOPES), "state": state}
    return "https://www.instagram.com/oauth/authorize?" + urlencode(q)


def exchange_code(http, settings, code: str, code_verifier: str | None = None) -> TokenSet:
    """code -> short-lived token -> 60-day token (+ best-effort username lookup)."""
    now = datetime.now(timezone.utc)
    secret = settings.INSTAGRAM_APP_SECRET.get_secret_value()
    resp = send(http, "POST", "https://api.instagram.com/oauth/access_token", data={
        "client_id": settings.INSTAGRAM_APP_ID,
        "client_secret": secret,
        "grant_type": "authorization_code",
        "redirect_uri": callback_url(settings, "instagram"),
        "code": code.removesuffix("#_"),
    })
    _fail(resp)
    body = json_or_empty(resp)
    short = (body.get("data") or [body])[0]  # doc shows a data array; accept flat too
    short_token, _ = _token_and_expiry(short, now)
    user_id = str(short.get("user_id", ""))

    resp = send(http, "GET", "https://graph.instagram.com/access_token", params={
        "grant_type": "ig_exchange_token", "client_secret": secret, "access_token": short_token})
    _fail(resp)
    token, expires = _token_and_expiry(json_or_empty(resp), now)

    username = "unknown"
    try:  # UNVERIFIED fields; never blocks connecting
        me = _get(http, "me", token, fields="user_id,username")
        username = me.get("username") or username
        user_id = str(me.get("user_id") or user_id)
    except PlatformError:
        pass
    granted = short.get("permissions") or SCOPES
    if isinstance(granted, str):
        granted = [p for p in granted.split(",") if p]
    return TokenSet(token, None, expires, None, list(granted), user_id, username)


def refresh(http, settings, account) -> TokenSet:
    """Refresh a long-lived token (must be >= 24 h old and unexpired)."""
    now = datetime.now(timezone.utc)
    resp = send(http, "GET", "https://graph.instagram.com/refresh_access_token",
                params={"grant_type": "ig_refresh_token", "access_token": account.access_token})
    _fail(resp)
    token, expires = _token_and_expiry(json_or_empty(resp), now)
    return TokenSet(token, None, expires, None, list(account.scopes or SCOPES),
                    account.external_user_id, account.username)


def _quota_reached(http, token: str, uid: str) -> bool:
    """Best-effort 100/24h check; field names UNVERIFIED so any surprise = not reached."""
    try:
        row = (_get(http, f"{uid}/content_publishing_limit", token, fields="quota_usage,config").get("data") or [{}])[0]
        used, total = row.get("quota_usage"), (row.get("config") or {}).get("quota_total", 100)
        return used is not None and int(used) >= int(total)
    except (PlatformError, ValueError, TypeError):
        return False


def _container_fields(post, media_url: str, caption: str) -> dict:
    asset = post.asset
    is_video = asset.media_type == "video"
    if post.content_type == "reel" and not is_video:
        raise PlatformError("media_not_publishable", "A Reel needs a video.", False)
    if post.content_type == "story":
        return {"media_type": "STORIES", "video_url" if is_video else "image_url": media_url}
    if is_video:  # feed videos are Reels on Instagram
        return {"media_type": "REELS", "video_url": media_url, "caption": caption}
    if (asset.mime_type or "") != "image/jpeg":
        raise PlatformError("media_not_publishable", "Instagram accepts JPEG images only; convert the file.", False)
    return {"image_url": media_url, "caption": caption}


def publish(http, token: str, external_user_id: str, post, media_url: str | None, media_path=None,
            on_ref=None, caption: str | None = None) -> PublishResult:
    """container -> poll FINISHED -> media_publish. `caption` = rendered caption + hashtags."""
    if not media_url:
        raise PlatformError("media_required", "Instagram needs a public media URL.", False)
    fields = _container_fields(post, media_url, caption if caption is not None else post.caption)
    if _quota_reached(http, token, external_user_id):
        raise PlatformError("platform_quota", "Instagram's 24-hour publishing limit is reached.", True)

    container = _post(http, f"{external_user_id}/media", token, **fields).get("id")
    if not container:
        raise PlatformError("bad_request", "Instagram returned no container id.", False)
    if on_ref:
        on_ref(container)

    for attempt in range(POLL_MAX):
        status = _get(http, container, token, container, fields="status_code").get("status_code")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            raise PlatformError("media_rejected", f"Instagram could not process the media ({status}).",
                                False, platform_ref=container)
        if attempt < POLL_MAX - 1:
            _sleep(POLL_SECONDS)
    else:
        raise PlatformError("timeout", "Instagram is still processing the media.", True, platform_ref=container)

    media_id = _post(http, f"{external_user_id}/media_publish", token, container, creation_id=container).get("id")
    if not media_id:
        raise PlatformError("bad_request", "Instagram returned no media id.", False, platform_ref=container)
    url, when = None, None
    try:  # UNVERIFIED fields; fail-soft
        info = _get(http, media_id, token, container, fields="permalink,timestamp")
        url = info.get("permalink")
        if info.get("timestamp"):
            when = datetime.strptime(info["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
    except (PlatformError, ValueError):
        pass
    return PublishResult(str(media_id), url, when, container)


def check_status(http, token: str, external_user_id: str, platform_ref: str) -> PublishResult | None:
    """Stuck-claim check: PUBLISHED container -> result (container id, no permalink); else None."""
    status = _get(http, platform_ref, token, platform_ref, fields="status_code").get("status_code")
    if status == "PUBLISHED":
        return PublishResult(platform_ref, None, None, platform_ref)
    return None


def fetch_metrics(http, token: str, external_user_id: str, external_post_id: str) -> dict:
    """like_count / comments_count only; insights are not implemented (UNVERIFIED)."""
    body = _get(http, external_post_id, token, fields="like_count,comments_count")
    return {"likes": body.get("like_count"), "comments": body.get("comments_count")}


def fetch_comments(http, token: str, external_user_id: str, external_post_id: str) -> list[dict]:
    rows = _get(http, f"{external_post_id}/comments", token, fields="id,text,timestamp,username").get("data") or []
    return [{"external_id": str(r["id"]), "author_username": r.get("username") or "",
             "text": r.get("text") or "", "commented_at": r.get("timestamp")} for r in rows if r.get("id")]


def send_reply(http, token: str, external_user_id: str, external_comment_id: str, text: str) -> str:
    resp = send(http, "POST", f"{BASE}/{external_comment_id}/replies", json={"message": text},
                params={"access_token": token})
    _fail(resp)
    new_id = json_or_empty(resp).get("id")
    if not new_id:
        raise PlatformError("bad_request", "Instagram returned no reply id.", False)
    return str(new_id)
