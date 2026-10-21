"""Image input: frames for the AI (ffmpeg), provider payloads, prompt wording. No real API calls."""

import base64
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from app.config import Settings
from app.services import frames
from app.services.claude_client import ClaudeClient
from app.services.frames import ImageInput, get_asset_images
from app.services.mistral_client import MistralClient

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
GOOD = json.dumps({"content_format": "f", "hook": "h", "caption": "c", "hashtags": [], "call_to_action": "", "content_notes": "n"})
IMGS = [ImageInput("image/jpeg", bytes([0xFF, 0xD8]) + b"fake")]
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def settings(tmp_path, **kw):
    return Settings(_env_file=None, MEDIA_UPLOAD_DIR=tmp_path, ANTHROPIC_API_KEY="k", MISTRAL_API_KEY="m", **kw)


def asset(**kw):
    base = dict(storage_type="upload", local_relpath="a.png", media_type="image", mime_type="image/png")
    return SimpleNamespace(**{**base, **kw})


# ---- provider payloads -------------------------------------------------------

def test_claude_sends_image_blocks_before_text(tmp_path):
    c = ClaudeClient(settings(tmp_path))
    c.client = MagicMock()
    c.client.messages.create.return_value = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text=GOOD)],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1), _request_id="r", model="m")
    c.generate("sys", "brief", images=IMGS)
    content = c.client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(content[0]["source"]["data"]) == IMGS[0].data
    assert content[-1] == {"type": "text", "text": "brief"}


def test_claude_without_images_keeps_plain_string(tmp_path):
    c = ClaudeClient(settings(tmp_path))
    c.client = MagicMock()
    c.client.messages.create.return_value = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text=GOOD)],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1), _request_id="r", model="m")
    c.generate("sys", "brief")
    assert c.client.messages.create.call_args.kwargs["messages"][0]["content"] == "brief"


def test_mistral_sends_data_uri_image(tmp_path):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": GOOD}}]})

    MistralClient(settings(tmp_path), transport=httpx.MockTransport(handler)).generate("s", "brief", images=IMGS)
    parts = seen["body"]["messages"][1]["content"]
    assert parts[0]["type"] == "image_url" and parts[0]["image_url"].startswith("data:image/jpeg;base64,")
    assert parts[-1] == {"type": "text", "text": "brief"}


# ---- frame extraction ---------------------------------------------------------

def test_external_link_and_disabled_flag_give_no_images(tmp_path):
    assert get_asset_images(asset(storage_type="external_url", local_relpath=None), settings(tmp_path)) == []
    (tmp_path / "a.png").write_bytes(PNG)
    assert get_asset_images(asset(), settings(tmp_path, ENABLE_IMAGE_INPUT=False)) == []


def test_small_image_sent_raw_when_no_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(frames.shutil, "which", lambda name: None)
    (tmp_path / "a.png").write_bytes(PNG)
    out = get_asset_images(asset(), settings(tmp_path))
    assert out == [ImageInput("image/png", PNG)]


def test_missing_file_gives_no_images(tmp_path):
    assert get_asset_images(asset(local_relpath="nope.png"), settings(tmp_path)) == []


def test_path_traversal_gives_no_images(tmp_path):
    assert get_asset_images(asset(local_relpath="../../etc/passwd"), settings(tmp_path)) == []


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_video_yields_three_jpeg_frames(tmp_path):
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                    "-t", "3", "-pix_fmt", "yuv420p", str(tmp_path / "v.mp4")], check=True)
    out = get_asset_images(asset(media_type="video", mime_type="video/mp4", local_relpath="v.mp4"), settings(tmp_path))
    assert len(out) == 3 and all(i.media_type == "image/jpeg" and i.data[:2] == bytes([0xFF, 0xD8]) for i in out)


# ---- end to end through the API ------------------------------------------------

def test_generate_shows_ai_the_uploaded_image_and_says_so(client, fake_claude, halloween_asset):
    r = client.post("/api/v1/content/generate", json=dict(
        profile_id=1, platform="instagram", content_type="reel", campaign="halloween", asset_id=halloween_asset["id"]))
    assert r.status_code == 201, r.text
    assert len(fake_claude.last_images) == 1
    assert "shown 1 image" in fake_claude.last_system and "Attached: 1 image" in fake_claude.last_user


def test_text_only_generate_says_ai_cannot_see_media(client, fake_claude):
    r = client.post("/api/v1/content/generate", json=dict(
        profile_id=1, platform="instagram", content_type="reel", campaign="halloween"))
    assert r.status_code == 201, r.text
    assert fake_claude.last_images == [] and "cannot see the media" in fake_claude.last_system
