"""Phase 3 C4: fan inbox API + dashboard pages, the Fanvue-only checkbox and Fanvue post fields.
Fanvue and the LLM are fakes (tests/test_fan_inbox.py helpers); nothing leaves the process."""
from datetime import timedelta

import pytest

from app.models import ContentPost, FanChat, FanMessage, MediaAsset
from tests.test_fan_inbox import Fake, FakeLLM, live, no_sleep, synced  # noqa: F401 (fixtures)
from tests.test_publish_live import NOON, db, make_post


def add_chat(status="needs_reply", handle="dave", text="hey you", draft=None, reason=None):
    d = db()
    c = FanChat(model_profile_id=1, fan_uuid=f"fan-{handle}", handle=handle, display_name="Dave", status=status,
                draft_text=draft, escalation_reason=reason, last_message_at=NOON)
    d.add(c)
    d.flush()
    d.add(FanMessage(chat_id=c.id, fanvue_message_uuid=f"m-{handle}", direction="in", text=text, sent_at=NOON))
    d.commit()
    return c.id


@pytest.fixture
def ui(live, monkeypatch):
    """`live` with a token valid on the real clock, and the web side NOT the worker (as in production)."""
    from datetime import datetime, timezone

    from app.models import PlatformAccount
    from app.services import accounts

    monkeypatch.setattr(accounts, "is_worker", False)
    d = db()
    acct = d.query(PlatformAccount).filter_by(platform="fanvue").one()
    acct.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    d.commit()
    return live


@pytest.fixture
def http_override(app):
    from app.api.dependencies import get_claude_client, get_platform_http

    state = {"fake": Fake(), "llm": FakeLLM()}
    app.dependency_overrides[get_platform_http] = lambda: state["fake"].client()
    app.dependency_overrides[get_claude_client] = lambda: state["llm"]
    yield state
    app.dependency_overrides.pop(get_platform_http, None)
    app.dependency_overrides.pop(get_claude_client, None)


# ---- API -------------------------------------------------------------------------

def test_api_list_get_and_filter(ui):
    a = add_chat("escalated", "eve", reason="asks whether Summer is real or AI")
    b = add_chat("needs_reply", "bob")
    add_chat("replied", "ray")
    items = ui.get("/api/v1/fans/chats").json()
    assert [i["id"] for i in items][:2] == [a, b]                            # escalated first
    assert [i["handle"] for i in ui.get("/api/v1/fans/chats?status=replied").json()] == ["ray"]
    assert ui.get("/api/v1/fans/chats?status=bogus").status_code == 422
    detail = ui.get(f"/api/v1/fans/chats/{b}").json()
    assert detail["messages"][0]["text"] == "hey you" and "fan_uuid" in detail
    assert ui.get("/api/v1/fans/chats/9999").status_code == 404


def test_api_draft_send_ignore_reopen(ui, http_override):
    cid = add_chat()
    r = ui.post(f"/api/v1/fans/chats/{cid}/draft", json={"instructions": "keep it short"})
    assert r.status_code == 200 and r.json()["draft_text"]
    assert "keep it short" in http_override["llm"].calls[0][1]
    r = ui.post(f"/api/v1/fans/chats/{cid}/send", json={"text": "thanks!"})
    assert r.status_code == 200 and r.json()["status"] == "replied"
    assert ui.post(f"/api/v1/fans/chats/{cid}/send", json={"text": "again"}).status_code == 409
    assert len(http_override["fake"].sends()) == 1
    assert ui.post(f"/api/v1/fans/chats/{cid}/ignore").json()["status"] == "ignored"
    assert ui.post(f"/api/v1/fans/chats/{cid}/reopen").json()["status"] == "needs_reply"


def test_api_send_validation(ui, http_override):
    cid = add_chat()
    assert ui.post(f"/api/v1/fans/chats/{cid}/send", json={"text": ""}).status_code == 422
    assert ui.post(f"/api/v1/fans/chats/{cid}/send", json={"text": "x", "price_cents": 999}).status_code == 422
    assert ui.post(f"/api/v1/fans/chats/{cid}/send", json={"text": "x", "asset_id": 1, "price_cents": 1}).status_code == 422
    assert http_override["fake"].sends() == []


def test_api_sync_live_only(phase2, client):
    phase2(PUBLISHING_MODE="dry_run")
    r = client.post("/api/v1/fans/sync")
    assert r.status_code == 409 and "mode_not_live" in r.text


def test_api_sync_live(ui, http_override):
    r = ui.post("/api/v1/fans/sync")
    assert r.status_code == 200 and r.json()["needs_reply"] == 1


# ---- pages -------------------------------------------------------------------------

def test_fans_pages_render_and_escape_fan_text(ui):
    cid = add_chat("escalated", "eve", text="<script>alert(1)</script>", reason="asks whether Summer is real or AI")
    page = ui.get("/fans")
    assert page.status_code == 200 and "needs you" in page.text and "<script>alert(1)" not in page.text
    assert 'href="/fans"' in page.text                                       # sidebar link
    chat = ui.get(f"/fans/{cid}")
    assert chat.status_code == 200 and "&lt;script&gt;" in chat.text and "<script>alert(1)" not in chat.text
    assert "No AI draft is offered" in chat.text and "Draft with AI" not in chat.text
    assert ui.get("/fans/9999").status_code == 404


def test_handle_never_reaches_inline_javascript(ui):
    cid = add_chat(handle="x');alert(1);//")
    page = ui.get(f"/fans/{cid}").text
    confirm = [line for line in page.splitlines() if "confirm(" in line]
    assert confirm and all("alert(1)" not in line for line in confirm)


def test_page_draft_and_send_flow(ui, http_override):
    cid = add_chat()
    r = ui.post(f"/fans/{cid}/draft", follow_redirects=False)
    assert r.status_code == 303 and "Draft%20ready" in r.headers["location"]
    assert "AI draft" in ui.get(f"/fans/{cid}").text
    r = ui.post(f"/fans/{cid}/send", data={"text": "hello you", "price": ""}, follow_redirects=False)
    assert "Sent." in r.headers["location"].replace("%20", " ")
    assert http_override["fake"].sends()[0].json == {"text": "hello you"}


def test_page_send_with_paid_media_and_bad_price(ui, http_override):
    from tests.test_publish_live import JPG

    cid = add_chat()
    a = ui.post("/api/v1/assets/upload", files={"file": ("p.jpg", JPG + b"ui", "image/jpeg")},
                  data={"description": "d"}).json()
    r = ui.post(f"/fans/{cid}/send", data={"text": "treat", "asset_id": a["id"], "price": "abc"}, follow_redirects=False)
    assert "Price%20must%20be%20a%20number" in r.headers["location"] and http_override["fake"].sends() == []
    r = ui.post(f"/fans/{cid}/send", data={"text": "treat", "asset_id": a["id"], "price": "inf"}, follow_redirects=False)
    assert "Price%20must%20be%20a%20number" in r.headers["location"]
    ui.post(f"/fans/{cid}/send", data={"text": "treat", "asset_id": a["id"], "price": "$9.99"})
    assert http_override["fake"].sends()[0].json["price"] == 999


def test_page_sync_in_dry_run_is_refused_politely(phase2, client):
    phase2(PUBLISHING_MODE="dry_run")
    r = client.post("/fans/sync", follow_redirects=False)
    assert r.status_code == 303 and "live%20mode%20only" in r.headers["location"]
    assert "works in <strong>live</strong> mode" in client.get("/fans").text


# ---- Fanvue-only checkbox (H7) -------------------------------------------------------

def test_fanvue_only_via_api_and_form(client, halloween_asset):
    aid = halloween_asset["id"]
    r = client.patch(f"/api/v1/assets/{aid}", json={"fanvue_only": True})
    assert r.status_code == 200 and r.json()["fanvue_only"] is True
    assert 'name="fanvue_only" value="true" checked' in client.get(f"/assets/{aid}").text
    client.post(f"/assets/{aid}", data={"filename": "test.png", "description": "d", "tags": "", "is_active": "true"})
    assert db().get(MediaAsset, aid).fanvue_only is False                     # unticked checkbox = off
    client.post(f"/assets/{aid}", data={"filename": "test.png", "description": "d", "tags": "", "is_active": "true",
                                        "fanvue_only": "true"})
    assert db().get(MediaAsset, aid).fanvue_only is True


# ---- Fanvue post fields ----------------------------------------------------------------

def test_fanvue_price_and_audience_via_api(phase2, client):
    pid = make_post(client, platform="fanvue", content_type="fanvue_post", video=False, hashtags=(), status="approved",
                    planned=None)
    r = client.patch(f"/api/v1/posts/{pid}", json={"fanvue_price_cents": 999, "fanvue_audience": "followers-and-subscribers"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["fanvue_price_cents"], body["fanvue_audience"], body["status"]) == (999, "followers-and-subscribers", "draft")
    assert client.patch(f"/api/v1/posts/{pid}", json={"fanvue_price_cents": 299}).status_code == 422
    assert client.patch(f"/api/v1/posts/{pid}", json={"fanvue_audience": "everyone"}).status_code == 422
    r = client.patch(f"/api/v1/posts/{pid}", json={"fanvue_price_cents": None})
    assert r.status_code == 200 and r.json()["fanvue_price_cents"] is None   # explicit null = free


def test_offer_fields_refused_on_instagram(phase2, client):
    pid = make_post(client)
    r = client.patch(f"/api/v1/posts/{pid}", json={"fanvue_price_cents": 999})
    assert r.status_code == 422 and "Fanvue posts only" in r.text


def test_post_form_shows_and_saves_fanvue_fields(phase2, client):
    pid = make_post(client, platform="fanvue", content_type="fanvue_post", video=False, hashtags=(), status="draft",
                    planned=None)
    page = client.get(f"/posts/{pid}").text
    assert 'name="fanvue_price"' in page and "Subscribers only" in page
    post = db().get(ContentPost, pid)
    form = {"hook": post.hook, "caption": post.caption, "hashtags": "", "call_to_action": post.call_to_action,
            "content_notes": post.content_notes, "campaign": post.campaign, "tags": "", "asset_id": post.media_asset_id,
            "planned_publish_at": "", "fanvue_audience": "subscribers", "fanvue_price": "12.50"}
    r = client.post(f"/posts/{pid}", data=form, follow_redirects=False)
    assert "updated" in r.headers["location"]
    assert db().get(ContentPost, pid).fanvue_price_cents == 1250
    r = client.post(f"/posts/{pid}", data={**form, "fanvue_price": "nan"}, follow_redirects=False)
    assert "Price%20must%20be%20a%20number" in r.headers["location"]
    r = client.post(f"/posts/{pid}", data={**form, "fanvue_price": "1.00"}, follow_redirects=False)
    assert "kind=error" in r.headers["location"] and db().get(ContentPost, pid).fanvue_price_cents == 1250
    assert 'name="fanvue_price"' not in client.get(f"/posts/{make_post(client)}").text   # Instagram: no price field


def test_browser_form_with_empty_media_and_price_sends_text_only(ui, http_override):
    """What a real browser posts when 'None' is picked and the price is left empty."""
    cid = add_chat()
    r = ui.post(f"/fans/{cid}/send", data={"text": "hi there", "asset_id": "", "price": ""}, follow_redirects=False)
    assert r.status_code == 303 and "kind=error" not in r.headers["location"]
    assert http_override["fake"].sends()[0].json == {"text": "hi there"}


def test_dashboard_posts_are_csrf_protected(ui, http_override):
    cid = add_chat()
    r = ui.post(f"/fans/{cid}/send", data={"text": "hi"}, headers={"Origin": "https://evil.example"},
                follow_redirects=False)
    assert r.status_code == 403 and http_override["fake"].sends() == []
    r = ui.post(f"/api/v1/fans/chats/{cid}/send", json={"text": "hi"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and http_override["fake"].sends() == []


def test_fanvue_posts_can_be_generated_and_filtered_from_the_dashboard(client, fake_claude):
    page = client.get("/generate").text
    assert '<option value="fanvue_post" data-platform="fanvue">' in page and 'value="fanvue"' in page
    assert 'value="fanvue"' in client.get("/posts").text
