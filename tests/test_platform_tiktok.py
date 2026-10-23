"""TikTok platform module (inbox mode + mock-only direct mode). MockTransport only;
fixtures follow docs/PLATFORM_API_NOTES.md (SECONDARY/UNVERIFIED items flagged there)."""
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from app.config import clear_settings_cache
from app.platforms import PlatformError, tiktok

SETTINGS = SimpleNamespace(TIKTOK_CLIENT_KEY="ck123", TIKTOK_CLIENT_SECRET=SecretStr("shh-secret"),
                           PUBLIC_BASE_URL="https://studio.example.com")
UPLOAD = "https://upload.tiktokapis.com/video/?upload_id=u1&upload_token=t1"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(tiktok, "_sleep", sleeps.append)
    return sleeps


def video(tmp_path, size=1000):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\0" * size)
    return p


def post(content_type="video", media_type="video", privacy=None):
    return SimpleNamespace(content_type=content_type, caption="cap", tiktok_privacy_level=privacy,
                           asset=SimpleNamespace(media_type=media_type, mime_type="video/mp4"))


class Fake:
    def __init__(self, status_seq=("PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"), init=None, put_status=201,
                 creator_options=("SELF_ONLY",)):
        self.calls, self.status_seq = [], list(status_seq)
        self.init, self.put_status, self.creator_options = init, put_status, list(creator_options)

    def client(self):
        def handler(req: httpx.Request) -> httpx.Response:
            body = None
            if req.content and req.headers.get("content-type", "").startswith("application/json"):
                body = json.loads(req.content)
            self.calls.append((req.method, str(req.url), dict(req.headers), body, len(req.content or b"")))
            path = req.url.path
            if req.method == "PUT":
                return httpx.Response(self.put_status)
            if path.endswith("/video/init/"):
                return self.init or httpx.Response(200, json={"data": {"publish_id": "v_pub1", "upload_url": UPLOAD},
                                                              "error": {"code": "ok", "message": "", "log_id": "l"}})
            if path.endswith("/creator_info/query/"):
                return httpx.Response(200, json={"data": {"privacy_level_options": self.creator_options},
                                                 "error": {"code": "ok"}})
            if path.endswith("/status/fetch/"):
                st = self.status_seq.pop(0) if len(self.status_seq) > 1 else self.status_seq[0]
                data = {"status": st, **({"fail_reason": "file_format_check_failed"} if st == "FAILED" else {})}
                return httpx.Response(200, json={"data": data, "error": {"code": "ok"}})
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "no route"}})
        return httpx.Client(transport=httpx.MockTransport(handler))

    def init_call(self):
        return next(c for c in self.calls if c[1].endswith("/video/init/"))

    def puts(self):
        return [c for c in self.calls if c[0] == "PUT"]


def test_inbox_small_video_single_chunk(tmp_path):
    f = Fake()
    refs = []
    res = tiktok.publish(f.client(), "TOK", "oid", post(), None, video(tmp_path, 1000), on_ref=refs.append)
    method, url, headers, body, _ = f.init_call()
    assert url.endswith("/v2/post/publish/inbox/video/init/") and headers["authorization"] == "Bearer TOK"
    assert body == {"source_info": {"source": "FILE_UPLOAD", "video_size": 1000, "chunk_size": 1000, "total_chunk_count": 1}}
    (put,) = f.puts()
    assert put[1] == UPLOAD and put[2]["content-range"] == "bytes 0-999/1000" and put[2]["content-type"] == "video/mp4"
    assert (res.external_post_id, res.platform_ref, res.external_url) == ("v_pub1", "v_pub1", None)
    assert refs == ["v_pub1"]


def test_chunk_plan_floor_rule_and_ranges(tmp_path, monkeypatch):
    mb = tiktok.MB
    assert tiktok._chunk_plan(4 * mb) == (4 * mb, 1)
    assert tiktok._chunk_plan(10 * mb) == (10 * mb, 1)
    assert tiktok._chunk_plan(25 * mb) == (10 * mb, 2)   # remainder folds into the final chunk (15 MB)
    assert tiktok._chunk_plan(35 * mb) == (10 * mb, 3)
    assert tiktok._chunk_plan(11 * 1024 * mb) == (64 * mb, 176)   # 11 GB: >1000 x 10 MB, so 64 MB chunks
    with pytest.raises(PlatformError):                            # > 64 GB cannot fit in 1000 chunks
        tiktok._chunk_plan(70 * 1024 * mb)


def test_multi_chunk_upload_ranges_are_sequential_and_cover_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tiktok, "MIN_CHUNK", 10)
    monkeypatch.setattr(tiktok, "CHUNK", 10)
    f = Fake()
    tiktok.publish(f.client(), "T", "o", post(), None, video(tmp_path, 25))
    body = f.init_call()[3]["source_info"]
    assert (body["chunk_size"], body["total_chunk_count"]) == (10, 2)
    ranges = [c[2]["content-range"] for c in f.puts()]
    assert ranges == ["bytes 0-9/25", "bytes 10-24/25"]
    assert [c[4] for c in f.puts()] == [10, 15]


def test_polls_until_inbox(no_sleep, tmp_path):
    f = Fake(status_seq=("PROCESSING_UPLOAD", "PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"))
    tiktok.publish(f.client(), "T", "o", post(), None, video(tmp_path))
    assert no_sleep == [tiktok.POLL_SECONDS, tiktok.POLL_SECONDS]


def test_failed_status_is_permanent(tmp_path):
    with pytest.raises(PlatformError) as e:
        tiktok.publish(Fake(status_seq=("FAILED",)).client(), "T", "o", post(), None, video(tmp_path))
    assert (e.value.code, e.value.transient, e.value.platform_ref) == ("media_rejected", False, "v_pub1")
    assert "file_format_check_failed" in e.value.message


def test_processing_timeout_is_transient(tmp_path):
    with pytest.raises(PlatformError) as e:
        tiktok.publish(Fake(status_seq=("PROCESSING_UPLOAD",)).client(), "T", "o", post(), None, video(tmp_path))
    assert (e.value.code, e.value.transient, e.value.platform_ref) == ("timeout", True, "v_pub1")


def test_photo_post_and_image_asset_not_publishable(tmp_path):
    f = Fake()
    for p in (post("photo_post", "image"), post("video", "image")):
        with pytest.raises(PlatformError) as e:
            tiktok.publish(f.client(), "T", "o", p, None, video(tmp_path))
        assert e.value.code == "media_not_publishable"
    assert not f.calls


def test_missing_file_is_media_required(tmp_path):
    with pytest.raises(PlatformError) as e:
        tiktok.publish(Fake().client(), "T", "o", post(), None, tmp_path / "gone.mp4")
    assert e.value.code == "media_required"


@pytest.mark.parametrize("status,body,code,transient", [
    (500, {}, "upstream_error", True),
    (429, {}, "rate_limited", True),
    (200, {"error": {"code": "rate_limit_exceeded", "message": "slow"}}, "rate_limited", True),
    (401, {}, "token_expired", False),
    (200, {"error": {"code": "access_token_invalid", "message": "x"}}, "token_expired", False),
    (403, {"error": {"code": "unaudited_client_can_only_post_to_private_accounts", "message": "x"}}, "unaudited_client", False),
    (400, {"error": {"code": "invalid_param", "message": "x"}}, "bad_request", False),
])
def test_init_error_mapping(tmp_path, status, body, code, transient):
    f = Fake(init=httpx.Response(status, json=body))
    with pytest.raises(PlatformError) as e:
        tiktok.publish(f.client(), "T", "o", post(), None, video(tmp_path))
    assert (e.value.code, e.value.transient) == (code, transient)
    assert not f.puts()


def test_upload_http_errors(tmp_path):
    with pytest.raises(PlatformError) as e:
        tiktok.publish(Fake(put_status=503).client(), "T", "o", post(), None, video(tmp_path))
    assert (e.value.code, e.value.transient) == ("upstream_error", True)
    with pytest.raises(PlatformError) as e:
        tiktok.publish(Fake(put_status=400).client(), "T", "o", post(), None, video(tmp_path))
    assert (e.value.code, e.value.transient) == ("media_rejected", False)


def test_missing_upload_target(tmp_path):
    f = Fake(init=httpx.Response(200, json={"data": {}, "error": {"code": "ok"}}))
    with pytest.raises(PlatformError) as e:
        tiktok.publish(f.client(), "T", "o", post(), None, video(tmp_path))
    assert e.value.code == "bad_request"


def test_transport_timeout_is_transient(tmp_path):
    def boom(req):
        raise httpx.ReadTimeout("t")
    with pytest.raises(PlatformError) as e:
        tiktok.publish(httpx.Client(transport=httpx.MockTransport(boom)), "T", "o", post(), None, video(tmp_path))
    assert (e.value.code, e.value.transient) == ("timeout", True)


@pytest.fixture
def direct_mode():
    import os
    os.environ["TIKTOK_POST_MODE"] = "direct"
    clear_settings_cache()
    yield
    os.environ.pop("TIKTOK_POST_MODE", None)
    clear_settings_cache()


def test_direct_mode_sends_privacy_and_aigc(direct_mode, tmp_path):
    f = Fake(status_seq=("PUBLISH_COMPLETE",))
    tiktok.publish(f.client(), "T", "o", post(privacy="SELF_ONLY"), None, video(tmp_path), caption="cap #tag")
    assert any(c[1].endswith("/creator_info/query/") for c in f.calls)
    url, body = f.init_call()[1], f.init_call()[3]
    assert url.endswith("/v2/post/publish/video/init/")
    assert body["post_info"] == {"title": "cap #tag", "privacy_level": "SELF_ONLY", "is_aigc": True}


def test_direct_mode_requires_privacy_offered_by_tiktok(direct_mode, tmp_path):
    for level in (None, "PUBLIC_TO_EVERYONE"):
        f = Fake()
        with pytest.raises(PlatformError) as e:
            tiktok.publish(f.client(), "T", "o", post(privacy=level), None, video(tmp_path))
        assert e.value.code == "bad_request" and not f.puts()


def test_check_status():
    assert tiktok.check_status(Fake(status_seq=("SEND_TO_USER_INBOX",)).client(), "T", "o", "v_pub1").external_post_id == "v_pub1"
    assert tiktok.check_status(Fake(status_seq=("PROCESSING_UPLOAD",)).client(), "T", "o", "v_pub1") is None


def test_metrics_comments_replies_unsupported():
    for fn in (tiktok.fetch_metrics, tiktok.fetch_comments, tiktok.send_reply):
        with pytest.raises(PlatformError) as e:
            fn(None, "T", "o", "x")
        assert (e.value.code, e.value.transient) == ("unsupported", False)


def test_authorize_url():
    q = parse_qs(tiktok.authorize_url(SETTINGS, "S1").split("?", 1)[1])
    assert q["client_key"] == ["ck123"] and q["state"] == ["S1"] and q["response_type"] == ["code"]
    assert q["scope"] == ["user.info.basic,video.upload"]
    assert q["redirect_uri"] == ["https://studio.example.com/oauth/tiktok/callback"]


def token_client(body, status=200, record=None):
    def handler(req):
        if record is not None:
            record.append((str(req.url), parse_qs(req.content.decode()) if req.content else {}))
        if req.url.path.endswith("/user/info/"):
            return httpx.Response(200, json={"data": {"user": {"open_id": "oid", "display_name": "Summer"}}})
        return httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_exchange_code_and_refresh():
    rec = []
    tok = {"access_token": "A1", "expires_in": 86400, "refresh_token": "R1", "refresh_expires_in": 31536000,
           "open_id": "oid", "scope": "user.info.basic,video.upload", "token_type": "Bearer"}
    ts = tiktok.exchange_code(token_client(tok, record=rec), SETTINGS, "CODE", code_verifier="V")
    assert (ts.access_token, ts.refresh_token, ts.external_user_id, ts.username) == ("A1", "R1", "oid", "Summer")
    assert ts.scopes == ["user.info.basic", "video.upload"] and ts.expires_at and ts.refresh_expires_at
    form = rec[0][1]
    assert form["grant_type"] == ["authorization_code"] and form["code_verifier"] == ["V"]
    assert form["client_secret"] == ["shh-secret"]

    rec2 = []
    acct = SimpleNamespace(refresh_token="R1", username="Summer", external_user_id="oid")
    ts2 = tiktok.refresh(token_client({**tok, "access_token": "A2", "refresh_token": "R2"}, record=rec2), SETTINGS, acct)
    assert (ts2.access_token, ts2.refresh_token, ts2.username) == ("A2", "R2", "Summer")
    assert rec2[0][1]["grant_type"] == ["refresh_token"] and rec2[0][1]["refresh_token"] == ["R1"]


def test_token_errors():
    with pytest.raises(PlatformError) as e:
        tiktok.refresh(token_client({"error": "invalid_grant", "error_description": "expired"}, status=400), SETTINGS,
                       SimpleNamespace(refresh_token="R", username="s", external_user_id="o"))
    assert (e.value.code, e.value.transient) == ("token_expired", False)
    with pytest.raises(PlatformError) as e:
        tiktok.exchange_code(token_client({}, status=503), SETTINGS, "C")
    assert (e.value.code, e.value.transient) == ("upstream_error", True)
