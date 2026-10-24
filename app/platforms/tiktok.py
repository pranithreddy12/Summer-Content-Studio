"""TikTok Content Posting API - inbox (draft upload) mode is the Phase 2 deliverable.

Calls are recorded in docs/PLATFORM_API_NOTES.md (checked 2026-10-23). The official
doc pages were unreachable that day (HTTP 503): chunking/status details are SECONDARY
and the OAuth v2 token/refresh/user-info calls are UNVERIFIED until live milestone L3.
Direct post is mock-only until TikTok audits the app.
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.platforms import PlatformError, PublishResult, TokenSet, callback_url, json_or_empty, send

API = "https://open.tiktokapis.com"
SCOPES = ["user.info.basic", "video.upload"]
MB = 1024 * 1024
MIN_CHUNK, CHUNK, MAX_CHUNK, MAX_FINAL_CHUNK = 5 * MB, 10 * MB, 64 * MB, 128 * MB
POLL_SECONDS, POLL_MAX = 3, 60
_sleep = time.sleep  # tests replace this

DONE = ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE")


def _fail(resp: httpx.Response, ref: str | None = None) -> None:
    """Raise a classified PlatformError for HTTP errors or a non-ok `error.code` body."""
    body = json_or_empty(resp)
    err = body.get("error") or {}
    code = str(err.get("code") or "ok")
    if resp.status_code < 400 and code == "ok":
        return
    msg = str(err.get("message") or f"TikTok API error {resp.status_code}")[:300]
    if resp.status_code >= 500:
        raise PlatformError("upstream_error", msg, True, resp.status_code, ref)
    if resp.status_code == 429 or code == "rate_limit_exceeded":
        raise PlatformError("rate_limited", msg, True, resp.status_code, ref)
    if resp.status_code == 401 or code in ("access_token_invalid", "scope_not_authorized"):
        raise PlatformError("token_expired", msg, False, resp.status_code, ref)
    if code == "unaudited_client_can_only_post_to_private_accounts":
        raise PlatformError("unaudited_client", msg, False, resp.status_code, ref)
    raise PlatformError("bad_request", msg, False, resp.status_code, ref)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}


def authorize_url(settings, state: str, code_challenge: str | None = None) -> str:
    q = {"client_key": settings.TIKTOK_CLIENT_KEY, "scope": ",".join(SCOPES), "response_type": "code",
         "redirect_uri": callback_url(settings, "tiktok"), "state": state}
    return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(q)


def _tokenset(body: dict, now: datetime, username: str | None = None) -> TokenSet:
    if not body.get("access_token"):
        raise PlatformError("bad_request", "TikTok returned no access token.", False)
    exp = now + timedelta(seconds=int(body["expires_in"])) if body.get("expires_in") else None
    rexp = now + timedelta(seconds=int(body["refresh_expires_in"])) if body.get("refresh_expires_in") else None
    scopes = [s for s in str(body.get("scope", "")).split(",") if s] or SCOPES
    open_id = str(body.get("open_id", ""))
    return TokenSet(body["access_token"], body.get("refresh_token"), exp, rexp, scopes, open_id, username or open_id)


def _token_call(http, settings, **fields) -> dict:
    resp = send(http, "POST", f"{API}/v2/oauth/token/", data={
        "client_key": settings.TIKTOK_CLIENT_KEY,
        "client_secret": settings.TIKTOK_CLIENT_SECRET.get_secret_value(), **fields})
    body = json_or_empty(resp)
    if resp.status_code >= 400 or body.get("error"):  # v2 token errors are flat: {"error": "...", "error_description": ...}
        msg = str(body.get("error_description") or f"TikTok token error {resp.status_code}")[:300]
        raise PlatformError("upstream_error" if resp.status_code >= 500 else "token_expired", msg,
                            resp.status_code >= 500, resp.status_code)
    return body


def exchange_code(http, settings, code: str, code_verifier: str | None = None) -> TokenSet:
    now = datetime.now(timezone.utc)
    fields = {"code": code, "grant_type": "authorization_code", "redirect_uri": callback_url(settings, "tiktok")}
    if code_verifier:
        fields["code_verifier"] = code_verifier
    ts = _tokenset(_token_call(http, settings, **fields), now)
    try:  # UNVERIFIED call; never blocks connecting
        resp = send(http, "GET", f"{API}/v2/user/info/", params={"fields": "open_id,display_name"},
                    headers={"Authorization": f"Bearer {ts.access_token}"})
        name = ((json_or_empty(resp).get("data") or {}).get("user") or {}).get("display_name")
        if name:
            ts.username = name
    except PlatformError:
        pass
    return ts


def refresh(http, settings, account) -> TokenSet:
    now = datetime.now(timezone.utc)
    ts = _tokenset(_token_call(http, settings, grant_type="refresh_token", refresh_token=account.refresh_token), now,
                   username=account.username)
    ts.external_user_id = ts.external_user_id or account.external_user_id
    return ts


def _chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count): <5 MB one chunk; else 10 MB chunks (up to 64 MB when the
    1000-chunk cap needs it), count = floor(size/chunk), remainder folded into the final chunk (<=128 MB)."""
    if size < MIN_CHUNK:
        return size, 1
    chunk = CHUNK
    if size // chunk > 1000:
        chunk = MAX_CHUNK
    count = max(1, size // chunk)
    if count > 1000 or size - chunk * (count - 1) > MAX_FINAL_CHUNK:
        raise PlatformError("media_rejected", "Video is too large for a TikTok upload.", False)
    return chunk, count


def _upload(http, upload_url: str, path: Path, mime: str, chunk: int, count: int) -> None:
    size = path.stat().st_size
    with path.open("rb") as fh:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            data = fh.read(end - start + 1)
            resp = send(http, "PUT", upload_url, content=data, headers={
                "Content-Type": mime, "Content-Length": str(len(data)),
                "Content-Range": f"bytes {start}-{end}/{size}"})
            if resp.status_code >= 500:
                raise PlatformError("upstream_error", f"TikTok upload failed ({resp.status_code}).", True, resp.status_code)
            if resp.status_code >= 300:
                raise PlatformError("media_rejected", f"TikTok rejected the upload ({resp.status_code}).", False, resp.status_code)


def _status(http, token: str, publish_id: str) -> dict:
    resp = send(http, "POST", f"{API}/v2/post/publish/status/fetch/", json={"publish_id": publish_id}, headers=_auth(token))
    _fail(resp, publish_id)
    return json_or_empty(resp).get("data") or {}


def publish(http, token: str, external_user_id: str, post, media_url: str | None, media_path=None,
            on_ref=None, caption: str | None = None) -> PublishResult:
    """Inbox mode (default): upload the video to the creator's TikTok drafts.
    Direct mode (TIKTOK_POST_MODE=direct, mock-only until audited) posts with privacy + is_aigc."""
    from app.config import get_settings

    asset = post.asset
    if post.content_type == "photo_post" or asset is None or asset.media_type != "video":
        raise PlatformError("media_not_publishable", "TikTok inbox mode accepts videos only; post photos manually.", False)
    if media_path is None or not Path(media_path).is_file():
        raise PlatformError("media_required", "The video file is not available on the server.", False)
    path = Path(media_path)
    size = path.stat().st_size
    chunk, count = _chunk_plan(size)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}

    direct = get_settings().TIKTOK_POST_MODE == "direct"
    body: dict = {"source_info": source}
    if direct:  # UNVERIFIED init path + is_aigc; privacy must come from creator_info
        info = send(http, "POST", f"{API}/v2/post/publish/creator_info/query/", headers=_auth(token))
        _fail(info)
        options = (json_or_empty(info).get("data") or {}).get("privacy_level_options") or []
        level = post.tiktok_privacy_level
        if not level or level not in options:
            raise PlatformError("bad_request", "Choose a privacy level offered by TikTok before posting.", False)
        body["post_info"] = {"title": (caption if caption is not None else post.caption)[:2200],
                             "privacy_level": level, "is_aigc": True}
        init_path = "/v2/post/publish/video/init/"
    else:
        init_path = "/v2/post/publish/inbox/video/init/"

    resp = send(http, "POST", API + init_path, json=body, headers=_auth(token))
    _fail(resp)
    data = json_or_empty(resp).get("data") or {}
    publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
    if not publish_id or not upload_url:
        raise PlatformError("bad_request", "TikTok returned no upload target.", False)
    if on_ref:
        on_ref(publish_id)

    _upload(http, upload_url, path, asset.mime_type or "video/mp4", chunk, count)

    for attempt in range(POLL_MAX):
        d = _status(http, token, publish_id)
        status = d.get("status")
        if status in DONE:
            return PublishResult(str(publish_id), None, None, publish_id)
        if status == "FAILED":
            raise PlatformError("media_rejected", f"TikTok failed the upload ({d.get('fail_reason', 'unknown')}).",
                                False, platform_ref=publish_id)
        if attempt < POLL_MAX - 1:
            _sleep(POLL_SECONDS)
    raise PlatformError("timeout", "TikTok is still processing the upload.", True, platform_ref=publish_id)


def check_status(http, token: str, external_user_id: str, platform_ref: str) -> PublishResult | None:
    if _status(http, token, platform_ref).get("status") in DONE:
        return PublishResult(platform_ref, None, None, platform_ref)
    return None


def _unsupported(*_a, **_k):
    raise PlatformError("unsupported", "TikTok does not provide this to this app type.", False)


fetch_metrics = fetch_comments = send_reply = _unsupported
