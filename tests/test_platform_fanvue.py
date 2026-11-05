"""Fanvue platform module + its wiring (PLAN_PHASE3 C2). MockTransport only - Fanvue has no
sandbox, so nothing here may ever reach the network. Fixtures follow docs/PLATFORM_API_NOTES.md."""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from app.models import ContentPost, MediaAsset, OAuthState, PlatformAccount, PublishAttempt
from app.platforms import PlatformError, fanvue
from app.services import AppError, accounts
from app.services import publishing as pub
from tests.test_publish_live import NOON, NoHttp, db, make_post

SETTINGS = SimpleNamespace(FANVUE_CLIENT_ID="fv-client", FANVUE_CLIENT_SECRET=SecretStr("fv-secret-XYZ"),
                           FANVUE_API_VERSION="2025-06-26", PUBLIC_BASE_URL="https://studio.example.com",
                           FANVUE_SCOPES="read:self read:chat write:post write:media")
PART = 10


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(fanvue, "_sleep", sleeps.append)
    return sleeps


@pytest.fixture
def fv(phase2):
    """phase2 env + Fanvue credentials (fake values)."""
    phase2(FANVUE_CLIENT_ID="fv-client", FANVUE_CLIENT_SECRET="fv-secret-XYZ")
    return phase2


class Fake:
    """A tiny Fanvue: token endpoint, /users/me, multipart upload, posts."""

    def __init__(self, media_status=("processing", "ready"), token_status=200, post_status=201, posts=None):
        self.calls, self.media_status = [], list(media_status)
        self.token_status, self.post_status, self.posts = token_status, post_status, posts or []
        self.overrides = {}

    def client(self):
        def handler(req: httpx.Request) -> httpx.Response:
            body = None
            if req.content and req.headers.get("content-type", "").startswith("application/json"):
                body = json.loads(req.content)
            self.calls.append(SimpleNamespace(method=req.method, url=req.url, path=req.url.path,
                                              headers=dict(req.headers), json=body, raw=req.content))
            key = (req.method, req.url.path)
            if key in self.overrides:
                return self.overrides[key].pop(0) if isinstance(self.overrides[key], list) else self.overrides[key]
            if req.url.host == "auth.fanvue.com":
                if self.token_status != 200:
                    return httpx.Response(self.token_status, json={"error": "invalid_grant",
                                                                   "error_description": "refresh token reused"})
                return httpx.Response(200, json={"access_token": "ACCESS-NEW", "refresh_token": "REFRESH-NEW",
                                                 "id_token": "x", "token_type": "Bearer", "expires_in": 3600,
                                                 "scope": "openid offline_access read:self write:post"})
            if req.url.host == "s3.fake":
                return httpx.Response(200, headers={"ETag": f'"etag-{req.url.params["n"]}"'})
            p = req.url.path
            if p == "/v1/users/me":
                return httpx.Response(200, json={"uuid": "creator-uuid", "handle": "its_me_summer",
                                                 "email": "private@example.com", "isCreator": True})
            if p == "/v1/media/uploads" and req.method == "POST":
                return httpx.Response(200, json={"mediaUuid": "media-1", "uploadId": "up-1", "partSize": PART,
                                                 "maxParts": 10000, "totalParts": None})
            if p.endswith("/parts/urls"):
                lo, hi = int(req.url.params["from"]), int(req.url.params["to"])
                return httpx.Response(200, json={"partSize": PART, "parts": [
                    {"partNumber": n, "url": f"https://s3.fake/part?n={n}"} for n in range(lo, hi + 1)]})
            if p == "/v1/media/uploads/up-1" and req.method == "PATCH":
                return httpx.Response(200, json={"status": "processing"})
            if p == "/v1/media/media-1":
                st = self.media_status.pop(0) if len(self.media_status) > 1 else self.media_status[0]
                return httpx.Response(200, json={"uuid": "media-1", "status": st,
                                                 **({"errorReason": "unsupported_format"} if st == "error" else {})})
            if p == "/v1/posts" and req.method == "POST":
                if self.post_status != 201:
                    return httpx.Response(self.post_status, json={"error": "nope"})
                return httpx.Response(201, json={"uuid": "post-1", "createdAt": "2026-11-02T12:00:01Z",
                                                 "publishedAt": "2026-11-02T12:00:02Z", "audience": body["audience"]})
            if p == "/v1/posts" and req.method == "GET":
                return httpx.Response(200, json={"data": self.posts, "nextCursor": None})
            if p.startswith("/v1/posts/"):
                found = [x for x in self.posts if x["uuid"] == p.rsplit("/", 1)[1]]
                return httpx.Response(200, json=found[0]) if found else httpx.Response(404, json={"error": "not found"})
            return httpx.Response(599, json={"error": f"unexpected {req.method} {p}"})

        return httpx.Client(transport=httpx.MockTransport(handler))

    def api_calls(self):
        return [c for c in self.calls if c.url.host == "api.fanvue.com"]


def media_file(tmp_path, size=25):
    p = tmp_path / "m.jpg"
    p.write_bytes(bytes(range(size)))
    return p


def fv_post(media_uuid=None, audience=None, price=None):
    return SimpleNamespace(caption="Hello subscribers", fanvue_audience=audience, fanvue_price_cents=price,
                           asset=SimpleNamespace(media_type="image", filename="m.jpg", fanvue_media_uuid=media_uuid))


# ---- OAuth ------------------------------------------------------------------

def test_authorize_url_has_pkce_system_scopes_and_exact_redirect():
    url = fanvue.authorize_url(SETTINGS, "STATE1", "CHALLENGE1")
    u = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert f"{u.scheme}://{u.netloc}{u.path}" == "https://auth.fanvue.com/oauth2/auth"
    assert q["client_id"] == "fv-client" and q["response_type"] == "code" and q["state"] == "STATE1"
    assert q["code_challenge"] == "CHALLENGE1" and q["code_challenge_method"] == "S256"
    assert q["redirect_uri"] == "https://studio.example.com/oauth/fanvue/callback"
    assert q["scope"].split() == ["openid", "offline_access", "offline", "read:self", "read:chat", "write:post",
                                  "write:media"]
    assert "%20" in u.query and "+" not in u.query   # space-separated scopes, percent-encoded


def test_authorize_url_refuses_without_pkce():
    with pytest.raises(PlatformError):
        fanvue.authorize_url(SETTINGS, "S", None)


def test_exchange_uses_basic_auth_and_verifier_and_keeps_only_uuid_and_handle():
    fake = Fake()
    ts = fanvue.exchange_code(fake.client(), SETTINGS, "CODE1", "VERIFIER1")
    tok = fake.calls[0]
    form = {k: v[0] for k, v in parse_qs(tok.raw.decode()).items()}
    assert form == {"grant_type": "authorization_code", "code": "CODE1", "code_verifier": "VERIFIER1",
                    "redirect_uri": "https://studio.example.com/oauth/fanvue/callback"}
    assert "fv-secret-XYZ" not in tok.raw.decode()                           # never in the body
    assert tok.headers["authorization"] == "Basic " + base64.b64encode(b"fv-client:fv-secret-XYZ").decode()
    me = fake.calls[1]
    assert me.path == "/v1/users/me" and me.headers["x-fanvue-api-version"] == "2025-06-26"
    assert me.headers["authorization"] == "Bearer ACCESS-NEW"
    assert (ts.external_user_id, ts.username, ts.refresh_token) == ("creator-uuid", "its_me_summer", "REFRESH-NEW")
    assert ts.scopes == ["openid", "offline_access", "read:self", "write:post"]
    assert "private@example.com" not in repr(ts)
    assert timedelta(minutes=59) < ts.expires_at - datetime.now(timezone.utc) <= timedelta(hours=1)


def test_exchange_requires_verifier():
    with pytest.raises(PlatformError):
        fanvue.exchange_code(Fake().client(), SETTINGS, "CODE1", None)


def test_refresh_returns_the_rotated_refresh_token():
    fake = Fake()
    acct = SimpleNamespace(refresh_token="REFRESH-OLD", external_user_id="creator-uuid", username="its_me_summer")
    ts = fanvue.refresh(fake.client(), SETTINGS, acct)
    form = {k: v[0] for k, v in parse_qs(fake.calls[0].raw.decode()).items()}
    assert form == {"grant_type": "refresh_token", "refresh_token": "REFRESH-OLD"}
    assert ts.refresh_token == "REFRESH-NEW" and ts.access_token == "ACCESS-NEW"
    assert ts.external_user_id == "creator-uuid" and len(fake.calls) == 1   # no /users/me on refresh


def test_refresh_reuse_rejection_is_token_expired():
    acct = SimpleNamespace(refresh_token="R", external_user_id="u", username="n")
    with pytest.raises(PlatformError) as e:
        fanvue.refresh(Fake(token_status=400).client(), SETTINGS, acct)
    assert e.value.code == "token_expired" and not e.value.transient


# ---- connect flow through the accounts service ------------------------------------

def test_connect_flow_end_to_end_with_pkce(fv, client, app):
    from app.api.dependencies import get_platform_http

    fake = Fake()
    app.dependency_overrides[get_platform_http] = lambda: fake.client()
    try:
        r = client.get("/oauth/fanvue/start?profile_id=1", follow_redirects=False)
        assert r.status_code == 302
        q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
        row = db().get(OAuthState, q["state"])
        expected = base64.urlsafe_b64encode(hashlib.sha256(row.code_verifier.encode()).digest()).rstrip(b"=").decode()
        assert q["code_challenge"] == expected and len(row.code_verifier) >= 43
        assert row.code_verifier not in r.headers["location"]       # the verifier never leaves the server

        r = client.get(f"/oauth/fanvue/callback?code=C1&state={q['state']}", follow_redirects=False)
        assert r.status_code == 302 and "Connected" in r.headers["location"]
        form = {k: v[0] for k, v in parse_qs(fake.calls[0].raw.decode()).items()}
        assert form["code_verifier"] == row.code_verifier
        assert db().get(OAuthState, q["state"]) is None              # single use
        acct = db().query(PlatformAccount).filter_by(platform="fanvue").one()
        assert (acct.external_user_id, acct.username, acct.status) == ("creator-uuid", "its_me_summer", "connected")
        assert "ACCESS-NEW" not in acct.access_token_enc and accounts.decrypt(acct.refresh_token_enc) == "REFRESH-NEW"
        assert "Fanvue" in client.get("/accounts").text
    finally:
        app.dependency_overrides.pop(get_platform_http, None)


def test_fanvue_needs_its_credentials_before_connecting(phase2, client):
    assert set(accounts.missing_env("fanvue")) == {"FANVUE_CLIENT_ID", "FANVUE_CLIENT_SECRET"}
    assert "FANVUE_CLIENT_ID" in client.get("/accounts").text


# ---- worker-only refresh (P3-5) ---------------------------------------------------

def _fanvue_account(expires_in: timedelta, now=NOON):
    d = db()
    a = PlatformAccount(model_profile_id=1, platform="fanvue", external_user_id="creator-uuid", username="its_me_summer",
                        access_token_enc=accounts.encrypt("ACCESS-OLD"), refresh_token_enc=accounts.encrypt("REFRESH-OLD"),
                        scopes=[], status="connected", token_expires_at=now + expires_in)
    d.add(a)
    d.commit()
    return a.id


def test_web_side_never_refreshes_fanvue(fv, client, monkeypatch):
    monkeypatch.setattr(accounts, "is_worker", False)
    d = db()
    acct = d.get(PlatformAccount, _fanvue_account(timedelta(minutes=2)))
    http = NoHttp()
    assert accounts.get_valid_token(d, acct, http, NOON) == "ACCESS-OLD"   # still valid: used as-is
    assert http.sent == 0
    acct.token_expires_at = NOON - timedelta(seconds=1)
    d.commit()
    with pytest.raises(PlatformError) as e:
        accounts.get_valid_token(d, acct, http, NOON)
    assert e.value.code == "worker_not_running" and e.value.transient and http.sent == 0


def test_worker_refreshes_and_persists_both_rotated_tokens(fv, client, monkeypatch):
    monkeypatch.setattr(accounts, "is_worker", True)
    d = db()
    acct = d.get(PlatformAccount, _fanvue_account(timedelta(minutes=2)))
    assert accounts.get_valid_token(d, acct, Fake().client(), NOON) == "ACCESS-NEW"
    fresh = db().get(PlatformAccount, acct.id)
    assert accounts.decrypt(fresh.refresh_token_enc) == "REFRESH-NEW"


def test_worker_refresh_stage_handles_fanvue_window_and_rejection(fv, client):
    from app.services import scheduler

    scheduler._refresh_tried.clear()
    soon = _fanvue_account(timedelta(minutes=15))
    out = scheduler.refresh_due_tokens(db(), NOON, Fake().client())
    assert out == {"refreshed": 1, "failed": 0}
    assert accounts.decrypt(db().get(PlatformAccount, soon).access_token_enc) == "ACCESS-NEW"

    later = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    d = db()
    acct = d.get(PlatformAccount, soon)
    acct.token_expires_at = later + timedelta(minutes=10)
    d.commit()
    out = scheduler.refresh_due_tokens(db(), later, Fake(token_status=400).client())
    assert out == {"refreshed": 0, "failed": 1}
    assert db().get(PlatformAccount, soon).status == "expired"          # human must reconnect (+ push)


def test_refresh_tokens_cli_skips_fanvue(fv, client):
    from click.testing import CliRunner

    from app.cli import cli

    _fanvue_account(timedelta(minutes=2), now=datetime.now(timezone.utc))
    out = CliRunner().invoke(cli, ["refresh-tokens"])
    assert "only the worker refreshes it" in out.output


# ---- upload + publish ----------------------------------------------------------

def test_multipart_upload_signs_puts_without_auth_completes_and_polls(tmp_path, no_sleep):
    fake = Fake(media_status=("processing", "processing", "ready"))
    path = media_file(tmp_path, size=25)                                     # 3 parts of 10/10/5 bytes
    assert fanvue.upload_media(fake.client(), "TOK", path, "m.jpg", "image") == "media-1"
    start = fake.calls[0]
    assert start.json == {"name": "m.jpg", "filename": "m.jpg", "mediaType": "image", "sizeBytes": 25}
    puts = [c for c in fake.calls if c.method == "PUT"]
    assert [len(c.raw) for c in puts] == [10, 10, 5] and b"".join(c.raw for c in puts) == path.read_bytes()
    assert all("authorization" not in c.headers for c in puts)
    done = [c for c in fake.calls if c.method == "PATCH"][0]
    assert done.json == {"parts": [{"PartNumber": n, "ETag": f'"etag-{n}"'} for n in (1, 2, 3)]}
    assert no_sleep == [fanvue.POLL_SECONDS] * 2
    assert all(c.headers.get("x-fanvue-api-version") == "2025-06-26" for c in fake.api_calls())


def test_media_error_is_rejected_and_slow_processing_is_transient(tmp_path):
    with pytest.raises(PlatformError) as e:
        fanvue.upload_media(Fake(media_status=("error",)).client(), "T", media_file(tmp_path), "m", "image")
    assert e.value.code == "media_rejected" and not e.value.transient and "unsupported_format" in e.value.message
    with pytest.raises(PlatformError) as e:
        fanvue.upload_media(Fake(media_status=("processing",)).client(), "T", media_file(tmp_path), "m", "image")
    assert e.value.code == "timeout" and e.value.transient


def test_publish_uploads_once_then_creates_a_subscribers_post(tmp_path):
    fake, refs = Fake(), []
    post = fv_post()
    res = fanvue.publish(fake.client(), "TOK", "creator-uuid", post, None, media_file(tmp_path), refs.append,
                         caption="Hello subscribers")
    create = [c for c in fake.calls if c.method == "POST" and c.path == "/v1/posts"][0]
    assert create.json == {"text": "Hello subscribers", "mediaUuids": ["media-1"], "audience": "subscribers"}
    assert "publishAt" not in create.json                                     # our worker owns timing
    assert post.asset.fanvue_media_uuid == "media-1" and refs[0].startswith("media:media-1@")
    assert (res.external_post_id, res.platform_ref) == ("post-1", "post-1")
    assert res.published_at == datetime(2026, 11, 2, 12, 0, 2, tzinfo=timezone.utc)


def test_publish_reuses_uploaded_media_and_sends_price_and_audience(tmp_path):
    fake = Fake()
    fanvue.publish(fake.client(), "TOK", "u", fv_post("media-9", "followers-and-subscribers", 999), None, None,
                   caption="c")
    assert [c.path for c in fake.calls] == ["/v1/posts"]                      # no upload at all
    assert fake.calls[0].json == {"text": "c", "mediaUuids": ["media-9"], "audience": "followers-and-subscribers",
                                  "price": 999}


def test_publish_needs_image_or_video(tmp_path):
    post = fv_post()
    post.asset.media_type = "audio"
    with pytest.raises(PlatformError) as e:
        fanvue.publish(Fake().client(), "T", "u", post, None, media_file(tmp_path))
    assert e.value.code == "media_not_publishable"


@pytest.mark.parametrize("status,code,transient", [(401, "token_expired", False), (403, "missing_scope", False),
                                                   (410, "api_version_sunset", False), (400, "bad_request", False),
                                                   (500, "upstream_error", True), (429, "rate_limited", True)])
def test_error_mapping(status, code, transient):
    with pytest.raises(PlatformError) as e:
        fanvue.publish(Fake(post_status=status).client(), "T", "u", fv_post("m"), None, None, caption="c")
    assert (e.value.code, e.value.transient, e.value.http_status) == (code, transient, status)


def test_short_retry_after_is_honoured_once(no_sleep):
    fake = Fake()
    fake.overrides[("POST", "/v1/posts")] = [httpx.Response(429, headers={"Retry-After": "7"}),
                                             httpx.Response(201, json={"uuid": "post-2", "createdAt": "2026-11-02T12:00:00Z"})]
    res = fanvue.publish(fake.client(), "T", "u", fv_post("m"), None, None, caption="c")
    assert res.external_post_id == "post-2" and no_sleep == [7]


# ---- stuck-claim recovery ---------------------------------------------------------

def test_check_status_by_post_uuid():
    fake = Fake(posts=[{"uuid": "post-1", "createdAt": "2026-11-02T12:00:00Z", "mediaUuids": ["m"]}])
    assert fanvue.check_status(fake.client(), "T", "u", "post-1").external_post_id == "post-1"
    assert fanvue.check_status(fake.client(), "T", "u", "post-404") is None


def test_check_status_by_media_ref_finds_only_a_recent_post_with_that_media():
    since = "2026-11-02T12:00:00+00:00"
    old = {"uuid": "old", "createdAt": "2026-10-01T09:00:00Z", "mediaUuids": ["media-1"]}
    new = {"uuid": "new", "createdAt": "2026-11-02T12:00:03Z", "mediaUuids": ["media-1"]}
    other = {"uuid": "other", "createdAt": "2026-11-02T12:00:04Z", "mediaUuids": ["media-2"]}
    fake = Fake(posts=[other, old])
    assert fanvue.check_status(fake.client(), "T", "u", f"media:media-1@{since}") is None
    assert fake.calls[0].url.params["startDate"] == "2026-11-02T11:55:00+00:00"
    assert fanvue.check_status(Fake(posts=[other, new, old]).client(), "T", "u",
                               f"media:media-1@{since}").external_post_id == "new"


# ---- publishing service ----------------------------------------------------------

def _fanvue_db_post(client, price=None, audience=None):
    pid = make_post(client, platform="fanvue", content_type="fanvue_post", video=False, hashtags=())
    d = db()
    p = d.get(ContentPost, pid)
    p.fanvue_price_cents, p.fanvue_audience = price, audience
    d.commit()
    return pid


@pytest.mark.parametrize("price,audience,ok", [(None, None, True), (300, "subscribers", True), (5000, None, True),
                                               (299, None, False), (5001, None, False), (None, "everyone", False)])
def test_preflight_price_and_audience(fv, client, price, audience, ok):
    pid = _fanvue_db_post(client, price, audience)
    d = db()
    post = d.get(ContentPost, pid)
    if ok:
        pub.check_publishable(d, post, now=NOON)
        assert not any(r.startswith("invalid_fanvue_offer") for r in pub.preflight_reasons(d, post))
    else:
        with pytest.raises(AppError) as e:
            pub.check_publishable(d, post, now=NOON)
        assert e.value.code == "invalid_fanvue_offer" and e.value.code in pub.PERSISTENT_PREFLIGHT
        assert any(r.startswith("invalid_fanvue_offer") for r in pub.preflight_reasons(d, post))


def test_dry_run_worker_would_publish_fanvue_post_with_zero_http(fv, client):
    from app.services import scheduler

    fv(PUBLISHING_MODE="dry_run")
    pid = _fanvue_db_post(client)
    http = NoHttp()
    scheduler.run_once(db(), NOON, http=http)
    assert http.sent == 0
    (attempt,) = db().query(PublishAttempt).filter_by(post_id=pid).all()
    assert attempt.trigger == "dry_run" and "would publish fanvue_post to fanvue" in attempt.error_message
    assert db().get(ContentPost, pid).status == "scheduled"


def test_live_publish_end_to_end_persists_media_uuid_and_marks_published(fv, client, monkeypatch):
    fv(PUBLISHING_MODE="live", DASHBOARD_USERNAME="lee", DASHBOARD_PASSWORD="a-long-password-123")
    monkeypatch.setattr(accounts, "is_worker", True)
    pid = _fanvue_db_post(client)
    _fanvue_account(timedelta(hours=1))
    fake = Fake()
    out = pub.publish_post(db(), pid, trigger="schedule", now=NOON, http=fake.client())
    assert out.status == "published" and out.external_post_id == "post-1" and out.published_via == "api"
    asset_id = db().get(ContentPost, pid).media_asset_id
    assert db().get(MediaAsset, asset_id).fanvue_media_uuid == "media-1"     # reused by the next post
    create = [c for c in fake.calls if c.path == "/v1/posts"][0]
    assert create.json["text"] == "Hello world"                               # no hashtags on Fanvue
    assert create.headers["authorization"] == "Bearer ACCESS-OLD"


def test_crash_after_upload_retry_finds_or_creates_exactly_one_post(fv, client, monkeypatch):
    """The upload finished and the ref was saved, then post creation failed (5xx). The retry asks
    Fanvue first, reuses the uploaded media and creates the post once."""
    fv(PUBLISHING_MODE="live", DASHBOARD_USERNAME="lee", DASHBOARD_PASSWORD="a-long-password-123")
    monkeypatch.setattr(accounts, "is_worker", True)
    pid = _fanvue_db_post(client)
    _fanvue_account(timedelta(hours=1))
    first = pub.publish_post(db(), pid, trigger="schedule", now=NOON, http=Fake(post_status=500).client())
    assert first.status == "scheduled" and first.last_publish_error.startswith("upstream_error")
    ref = db().query(PublishAttempt).filter_by(post_id=pid).one().platform_ref
    assert ref.startswith("media:media-1@")

    d = db()
    d.get(ContentPost, pid).next_attempt_at = None
    d.commit()
    fake = Fake()
    out = pub.publish_post(db(), pid, trigger="retry", now=NOON + timedelta(minutes=5), http=fake.client())
    assert out.status == "published"
    paths = [(c.method, c.path) for c in fake.calls]
    assert ("POST", "/v1/media/uploads") not in paths                         # uploaded media reused
    assert paths.count(("GET", "/v1/posts")) == 1 and paths.count(("POST", "/v1/posts")) == 1
