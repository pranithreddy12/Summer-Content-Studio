"""New random drafts push a Telegram message with an Approve button; tapping it approves the draft (worker poll).
A fake Telegram API only: no network."""

import json
from pathlib import Path

import httpx
import pytest

from app import platforms
from app.db import get_session_factory
from app.models import ContentType, Platform
from app.schemas import PostCreate
from app.services import notifications
from app.services.posts import create_post, get_post

GIF = (Path(__file__).parent / "fixtures" / "tiny.gif").read_bytes()
URL = "/api/v1/content/generate-random"
CHAT = "4242"


class FakeTelegram:
    def __init__(self):
        self.calls, self.updates = [], []

    def client(self):
        def handler(req: httpx.Request):
            method = req.url.path.rsplit("/", 1)[-1]
            body = json.loads(req.content) if req.content else {}
            self.calls.append((method, body))
            if method == "getUpdates":
                return httpx.Response(200, json={"ok": True, "result": self.updates})
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Client(transport=httpx.MockTransport(handler))

    def sent(self, method):
        return [body for m, body in self.calls if m == method]


@pytest.fixture
def tg(env, monkeypatch):
    env(NOTIFY_PROVIDER="telegram", TELEGRAM_BOT_TOKEN="BOT-TOKEN", TELEGRAM_CHAT_ID=CHAT,
        PUBLIC_BASE_URL="https://studio.example.com")
    fake = FakeTelegram()
    monkeypatch.setattr(platforms, "http_client", lambda *a, **k: fake.client())
    notifications._tg["offset"] = None
    return fake


def db():
    return get_session_factory()()


def draft():
    return create_post(db(), PostCreate(profile_id=1, platform=Platform.instagram, content_type=ContentType.reel,
                                        campaign="lifestyle", caption="Hello from the draft")).id


def tap(update_id, post_id, chat=CHAT, data=None):
    return {"update_id": update_id, "callback_query": {
        "id": f"cq{update_id}", "data": data or f"approve:{post_id}",
        "message": {"message_id": 7, "chat": {"id": int(chat)}, "text": "New draft"}}}


def test_new_random_drafts_push_a_message_with_an_approve_button(client, fake_claude, tg):
    client.post("/api/v1/assets/upload", files={"file": ("g.gif", GIF, "image/gif")})
    r = client.post(URL, json={"platform": "instagram", "count": 1})
    assert r.status_code == 201, r.text
    pid = r.json()["posts"][0]["id"]
    (msg,) = tg.sent("sendMessage")
    assert f"New draft #{pid}" in msg["text"] and "g.gif" in msg["text"] and f"/posts/{pid}" in msg["text"]
    row = msg["reply_markup"]["inline_keyboard"][0]
    assert {"text": "Approve ✅", "callback_data": f"approve:{pid}"} in row
    assert {"text": "Open", "url": f"https://studio.example.com/posts/{pid}"} in row


def test_tap_approves_the_draft_and_updates_the_message(app, tg):
    pid = draft()
    tg.updates = [tap(10, pid)]
    assert notifications.poll_telegram(db()) == 1
    assert get_post(db(), pid).status == "approved"
    assert tg.sent("answerCallbackQuery")[0]["text"] == f"Approved #{pid}"
    edit = tg.sent("editMessageText")[0]
    assert edit["text"].endswith("✅ Approved") and edit["message_id"] == 7
    tg.updates = []
    notifications.poll_telegram(db())
    assert tg.sent("getUpdates")[-1]["offset"] == 11  # handled taps are acknowledged


def test_tap_from_another_chat_is_refused(app, tg):
    pid = draft()
    tg.updates = [tap(10, pid, chat="999")]
    assert notifications.poll_telegram(db()) == 0
    assert get_post(db(), pid).status == "draft"
    assert tg.sent("answerCallbackQuery")[0]["text"] == "Not allowed."


def test_repeat_and_bad_taps_are_harmless(app, tg):
    pid = draft()
    tg.updates = [tap(10, pid), tap(12, pid, data="approve:abc"), tap(13, pid, data="delete:1"), tap(14, 999999)]
    assert notifications.poll_telegram(db()) == 1  # only the real approve counts; junk and a missing post do not
    texts = [c["text"] for c in tg.sent("answerCallbackQuery")]
    assert texts[0] == f"Approved #{pid}" and texts.count("Unknown action.") == 2
    assert texts[-1].startswith("#999999:") and get_post(db(), pid).status == "approved"
    tg.updates = [tap(15, pid)]  # tapping again on an approved draft changes nothing
    notifications.poll_telegram(db())
    assert get_post(db(), pid).status == "approved"
    archived = draft()
    from app.services.posts import archive_post
    archive_post(db(), archived)
    tg.updates = [tap(16, archived)]  # a draft that was archived meanwhile is NOT approved by an old button
    assert notifications.poll_telegram(db()) == 0 and get_post(db(), archived).status == "archived"


def test_poll_does_nothing_without_telegram(app, env):
    env(NOTIFY_PROVIDER="none")
    assert notifications.poll_telegram(db()) == 0


def test_poll_survives_telegram_errors(app, tg, monkeypatch):
    monkeypatch.setattr(platforms, "http_client", lambda *a, **k: httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(502))))
    assert notifications.poll_telegram(db()) == 0  # never raises


def test_large_batches_push_only_the_first_ten(app, tg):
    from app.services.random_draft import MAX_DRAFT_PUSHES, _notify_new_drafts

    ids = [draft() for _ in range(MAX_DRAFT_PUSHES + 3)]
    _notify_new_drafts(db(), [get_post(db(), i) for i in ids])
    texts = [m["text"] for m in tg.sent("sendMessage")]
    assert len(texts) == MAX_DRAFT_PUSHES + 1 and texts[-1].startswith("3 more new drafts")
