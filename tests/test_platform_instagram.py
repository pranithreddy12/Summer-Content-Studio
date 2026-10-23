"""Instagram platform module against doc-derived fixtures (docs/PLATFORM_API_NOTES.md).
No real HTTP: httpx.MockTransport only."""
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from app.platforms import PlatformError, instagram

UID = "1784140000"
SETTINGS = SimpleNamespace(INSTAGRAM_APP_ID="app123", INSTAGRAM_APP_SECRET=SecretStr("shh-secret"),
                           PUBLIC_BASE_URL="https://studio.example.com")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(instagram, "_sleep", sleeps.append)
    return sleeps


def post(content_type="reel", media_type="video", mime="video/mp4", caption="hello"):
    return SimpleNamespace(content_type=content_type, caption=caption,
                           asset=SimpleNamespace(media_type=media_type, mime_type=mime))


class Fake:
    """Route table: (METHOD, path-suffix) -> list of responses (last one repeats)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def client(self):
        def handler(req: httpx.Request) -> httpx.Response:
            body = parse_qs(req.content.decode()) if req.content and not req.content.startswith(b"{") else {}
            self.calls.append((req.method, req.url.path, dict(req.url.params), body))
            for (method, suffix), resp in self.routes.items():
                if req.method == method and req.url.path.endswith(suffix):
                    seq = resp if isinstance(resp, list) else [resp]
                    item = seq.pop(0) if len(seq) > 1 else seq[0]
                    return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)
            return httpx.Response(404, json={"error": {"message": "no route", "code": 100}})
        return httpx.Client(transport=httpx.MockTransport(handler))


def happy_routes(over=None):
    routes = {
        ("GET", f"/{UID}/content_publishing_limit"): {"data": [{"quota_usage": 3, "config": {"quota_total": 100}}]},
        ("POST", f"/{UID}/media"): {"id": "C1"},
        ("GET", "/C1"): {"status_code": "FINISHED"},
        ("POST", f"/{UID}/media_publish"): {"id": "M1"},
        ("GET", "/M1"): {"permalink": "https://www.instagram.com/reel/abc/", "timestamp": "2026-10-23T10:00:00+0000"},
    }
    routes.update(over or {})
    return routes


def test_reel_flow_params_and_result():
    f = Fake(happy_routes())
    refs = []
    res = instagram.publish(f.client(), "TOK", UID, post(), "https://studio.example.com/m/x", on_ref=refs.append,
                            caption="hello\n\n#a #b")
    create = next(c for c in f.calls if c[0] == "POST" and c[1].endswith(f"/{UID}/media"))
    assert create[3]["media_type"] == ["REELS"] and create[3]["video_url"] == ["https://studio.example.com/m/x"]
    assert create[3]["caption"] == ["hello\n\n#a #b"] and create[3]["access_token"] == ["TOK"]
    pub = next(c for c in f.calls if c[1].endswith("/media_publish"))
    assert pub[3]["creation_id"] == ["C1"]
    assert (res.external_post_id, res.platform_ref, res.external_url) == ("M1", "C1", "https://www.instagram.com/reel/abc/")
    assert res.published_at.year == 2026 and refs == ["C1"]


def test_feed_jpeg_image_uses_image_url_and_no_media_type():
    f = Fake(happy_routes())
    instagram.publish(f.client(), "TOK", UID, post("feed_post", "image", "image/jpeg"), "https://x/m/1")
    create = next(c for c in f.calls if c[0] == "POST" and c[1].endswith(f"/{UID}/media"))
    assert create[3]["image_url"] == ["https://x/m/1"] and "media_type" not in create[3]


def test_story_has_no_caption():
    f = Fake(happy_routes())
    instagram.publish(f.client(), "TOK", UID, post("story", "image", "image/jpeg"), "https://x/m/1")
    create = next(c for c in f.calls if c[0] == "POST" and c[1].endswith(f"/{UID}/media"))
    assert create[3]["media_type"] == ["STORIES"] and create[3]["image_url"] == ["https://x/m/1"]
    assert "caption" not in create[3]


def test_feed_video_becomes_reel():
    f = Fake(happy_routes())
    instagram.publish(f.client(), "TOK", UID, post("feed_post", "video"), "https://x/m/1")
    create = next(c for c in f.calls if c[0] == "POST" and c[1].endswith(f"/{UID}/media"))
    assert create[3]["media_type"] == ["REELS"]


def test_png_image_is_not_publishable_before_any_http():
    f = Fake(happy_routes())
    with pytest.raises(PlatformError) as e:
        instagram.publish(f.client(), "TOK", UID, post("feed_post", "image", "image/png"), "https://x/m/1")
    assert e.value.code == "media_not_publishable" and not e.value.transient
    assert not [c for c in f.calls if c[0] == "POST"]


def test_reel_with_image_rejected():
    with pytest.raises(PlatformError) as e:
        instagram.publish(Fake(happy_routes()).client(), "TOK", UID, post("reel", "image", "image/jpeg"), "https://x/m/1")
    assert e.value.code == "media_not_publishable"


def test_no_media_url_is_media_required():
    with pytest.raises(PlatformError) as e:
        instagram.publish(Fake({}).client(), "TOK", UID, post(), None)
    assert e.value.code == "media_required"


def test_polls_until_finished(no_sleep):
    f = Fake(happy_routes({("GET", "/C1"): [{"status_code": "IN_PROGRESS"}, {"status_code": "IN_PROGRESS"},
                                              {"status_code": "FINISHED"}]}))
    instagram.publish(f.client(), "TOK", UID, post(), "https://x/m/1")
    assert no_sleep == [instagram.POLL_SECONDS, instagram.POLL_SECONDS]


def test_processing_timeout_is_transient_and_keeps_ref():
    f = Fake(happy_routes({("GET", "/C1"): {"status_code": "IN_PROGRESS"}}))
    with pytest.raises(PlatformError) as e:
        instagram.publish(f.client(), "TOK", UID, post(), "https://x/m/1")
    assert (e.value.code, e.value.transient, e.value.platform_ref) == ("timeout", True, "C1")
    assert not [c for c in f.calls if c[1].endswith("/media_publish")]


@pytest.mark.parametrize("status", ["ERROR", "EXPIRED"])
def test_container_error_is_permanent_media_rejected(status):
    f = Fake(happy_routes({("GET", "/C1"): {"status_code": status}}))
    with pytest.raises(PlatformError) as e:
        instagram.publish(f.client(), "TOK", UID, post(), "https://x/m/1")
    assert (e.value.code, e.value.transient, e.value.platform_ref) == ("media_rejected", False, "C1")


def test_quota_reached_is_transient():
    f = Fake(happy_routes({("GET", f"/{UID}/content_publishing_limit"):
                             {"data": [{"quota_usage": 100, "config": {"quota_total": 100}}]}}))
    with pytest.raises(PlatformError) as e:
        instagram.publish(f.client(), "TOK", UID, post(), "https://x/m/1")
    assert (e.value.code, e.value.transient) == ("platform_quota", True)
    assert not [c for c in f.calls if c[0] == "POST"]


def test_permalink_failure_is_soft():
    f = Fake(happy_routes({("GET", "/M1"): httpx.Response(404, json={"error": {"message": "x", "code": 100}})}))
    res = instagram.publish(f.client(), "TOK", UID, post(), "https://x/m/1")
    assert res.external_post_id == "M1" and res.external_url is None


@pytest.mark.parametrize("status,body,code,transient", [
    (500, {}, "upstream_error", True),
    (429, {}, "rate_limited", True),
    (400, {"error": {"message": "rate", "code": 4}}, "rate_limited", True),
    (400, {"error": {"message": "bad token", "code": 190}}, "token_expired", False),
    (401, {}, "token_expired", False),
    (400, {"error": {"message": "policy", "code": 368}}, "policy_rejected", False),
    (400, {"error": {"message": "nope", "code": 100}}, "bad_request", False),
])
def test_http_error_mapping(status, body, code, transient):
    f = Fake(happy_routes({("POST", f"/{UID}/media"): httpx.Response(status, json=body)}))
    with pytest.raises(PlatformError) as e:
        instagram.publish(f.client(), "TOK", UID, post(), "https://x/m/1")
    assert (e.value.code, e.value.transient, e.value.http_status) == (code, transient, status)


def test_transport_errors_are_transient():
    def boom(req):
        raise httpx.ConnectTimeout("t")
    with pytest.raises(PlatformError) as e:
        instagram.publish(httpx.Client(transport=httpx.MockTransport(boom)), "TOK", UID, post(), "https://x/m/1")
    assert (e.value.code, e.value.transient) == ("timeout", True)

    def down(req):
        raise httpx.ConnectError("c")
    with pytest.raises(PlatformError) as e:
        instagram.publish(httpx.Client(transport=httpx.MockTransport(down)), "TOK", UID, post(), "https://x/m/1")
    assert (e.value.code, e.value.transient) == ("connection_error", True)


def test_check_status_published_vs_not():
    assert instagram.check_status(Fake({("GET", "/C9"): {"status_code": "PUBLISHED"}}).client(), "T", UID, "C9").external_post_id == "C9"
    assert instagram.check_status(Fake({("GET", "/C9"): {"status_code": "FINISHED"}}).client(), "T", UID, "C9") is None


def test_authorize_url():
    url = instagram.authorize_url(SETTINGS, "STATE1")
    assert url.startswith("https://www.instagram.com/oauth/authorize?")
    q = parse_qs(url.split("?", 1)[1])
    assert q["client_id"] == ["app123"] and q["state"] == ["STATE1"] and q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["https://studio.example.com/oauth/instagram/callback"]
    assert q["scope"] == ["instagram_business_basic,instagram_business_content_publish,instagram_business_manage_comments"]


@pytest.mark.parametrize("short", [
    {"access_token": "SHORT", "user_id": 111, "permissions": "instagram_business_basic"},
    {"data": [{"access_token": "SHORT", "user_id": 111, "permissions": ["instagram_business_basic"]}]},
])
def test_exchange_code_short_then_long(short):
    f = Fake({("POST", "/oauth/access_token"): short,
              ("GET", "/access_token"): {"access_token": "LONG", "token_type": "bearer", "expires_in": 5184000},
              ("GET", "/me"): {"user_id": "999", "username": "summerh_03"}})
    ts = instagram.exchange_code(f.client(), SETTINGS, "CODE#_")
    assert (ts.access_token, ts.external_user_id, ts.username) == ("LONG", "999", "summerh_03")
    assert ts.expires_at is not None and ts.refresh_token is None
    first = f.calls[0]
    assert first[3]["code"] == ["CODE"] and first[3]["client_secret"] == ["shh-secret"]
    long = next(c for c in f.calls if c[1].endswith("/access_token") and c[0] == "GET")
    assert long[2]["grant_type"] == "ig_exchange_token" and long[2]["access_token"] == "SHORT"


def test_exchange_code_survives_me_failure():
    f = Fake({("POST", "/oauth/access_token"): {"access_token": "S", "user_id": 111},
              ("GET", "/access_token"): {"access_token": "L", "expires_in": 100}})
    ts = instagram.exchange_code(f.client(), SETTINGS, "C")
    assert (ts.external_user_id, ts.username) == ("111", "unknown")


def test_exchange_code_rejected():
    f = Fake({("POST", "/oauth/access_token"): httpx.Response(400, json={"error": {"message": "Invalid code", "code": 100}})})
    with pytest.raises(PlatformError) as e:
        instagram.exchange_code(f.client(), SETTINGS, "BAD")
    assert e.value.code == "bad_request"


def test_refresh_uses_ig_refresh_token():
    f = Fake({("GET", "/refresh_access_token"): {"access_token": "L2", "token_type": "bearer", "expires_in": 5184000}})
    acct = SimpleNamespace(access_token="L1", scopes=["instagram_business_basic"], external_user_id="999", username="s")
    ts = instagram.refresh(f.client(), SETTINGS, acct)
    assert ts.access_token == "L2" and ts.external_user_id == "999"
    assert f.calls[0][2]["grant_type"] == "ig_refresh_token" and f.calls[0][2]["access_token"] == "L1"


def test_comments_metrics_reply():
    f = Fake({("GET", "/M1/comments"): {"data": [{"id": "c1", "text": "love it", "timestamp": "2026-10-23T11:00:00+0000", "username": "fan"},
                                                  {"text": "no id"}]},
              ("GET", "/M1"): {"like_count": 12, "comments_count": 2},
              ("POST", "/c1/replies"): {"id": "r1"}})
    http = f.client()
    assert instagram.fetch_comments(http, "T", UID, "M1") == [
        {"external_id": "c1", "author_username": "fan", "text": "love it", "commented_at": "2026-10-23T11:00:00+0000"}]
    assert instagram.fetch_metrics(http, "T", UID, "M1") == {"likes": 12, "comments": 2}
    assert instagram.send_reply(http, "T", UID, "c1", "thanks!") == "r1"
    reply = next(c for c in f.calls if c[1].endswith("/c1/replies"))
    assert reply[2]["access_token"] == "T"


def test_tokens_never_in_error_messages():
    f = Fake(happy_routes({("POST", f"/{UID}/media"): httpx.Response(400, json={"error": {"message": "bad", "code": 100}})}))
    with pytest.raises(PlatformError) as e:
        instagram.publish(f.client(), "SECRET-TOKEN-XYZ", UID, post(), "https://x/m/1")
    assert "SECRET-TOKEN-XYZ" not in str(e.value) and "SECRET-TOKEN-XYZ" not in e.value.message
